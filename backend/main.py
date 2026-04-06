from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import os
import json
import threading
import asyncio
from datetime import datetime

from database import engine, SessionLocal, Base, FileEntry
from scanner import (
    scan_directory, compute_md5_batch, find_duplicates,
    stop_scan, ALL_SUPPORTED, calculate_md5, SCAN_STOP_FLAG
)
from archiver import deduplicate_files, rename_duplicates_by_date, get_unique_filename

app = FastAPI(title="FileIndexer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

scan_lock = threading.Lock()
_stop_flags = {}

def _send_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

@app.get("/")
async def root():
    return {"message": "FileIndexer API", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/supported-extensions")
async def get_supported_extensions():
    return {
        "doc": list({'.doc', '.docx'}),
        "csv": list({'.csv', '.els', '.elsx'}),
        "image": list({'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}),
        "pdf": list({'.pdf'}),
        "all": list(ALL_SUPPORTED)
    }

@app.post("/scan")
async def scan_paths(paths: List[str]):
    """
    扫描指定目录，生成文件索引
    """
    import uuid

    job_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    with scan_lock:
        _stop_flags[job_id] = stop_event

    async def generate():
        db = SessionLocal()

        def on_progress(progress_data):
            return _send_sse(progress_data)

        total_scanned = 0
        for directory in paths:
            if stop_event.is_set():
                break

            if not os.path.isdir(directory):
                yield _send_sse({"type": "error", "directory": directory, "message": "目录不存在"})
                continue

            for result in scan_directory(db, directory, progress_callback=on_progress):
                if result["type"] == "progress":
                    yield _send_sse(result)
                elif result["type"] == "complete":
                    total_scanned += result["total"]
                    yield _send_sse(result)
                elif result["type"] == "error":
                    yield _send_sse(result)

        yield _send_sse({
            "type": "done",
            "job_id": job_id,
            "total_scanned": total_scanned
        })

        with scan_lock:
            _stop_flags.pop(job_id, None)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    })

@app.post("/scan/stop")
async def stop_scan_job(job_id: str = None):
    if job_id:
        with scan_lock:
            if job_id in _stop_flags:
                _stop_flags[job_id].set()
                return {"success": True, "message": f"已停止任务: {job_id}"}
    else:
        for ev in _stop_flags.values():
            ev.set()
        return {"success": True, "message": "已停止所有任务"}
    return {"success": False, "message": "任务不存在"}

@app.post("/compute-md5")
async def compute_md5_for_files(file_ids: List[int] = None):
    """
    计算文件的 MD5 哈希值
    """
    import uuid

    job_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    with scan_lock:
        _stop_flags[job_id] = stop_event

    async def generate():
        db = SessionLocal()

        if file_ids:
            ids_to_compute = file_ids
        else:
            entries = db.query(FileEntry).filter(FileEntry.md5 == None).all()
            ids_to_compute = [e.id for e in entries]

        if not ids_to_compute:
            yield _send_sse({"type": "done", "job_id": job_id, "message": "没有需要计算 MD5 的文件"})
            return

        for result in compute_md5_batch(db, ids_to_compute):
            if stop_event.is_set():
                yield _send_sse({"type": "stopped"})
                break
            yield _send_sse(result)

        with scan_lock:
            _stop_flags.pop(job_id, None)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    })

@app.get("/files")
async def get_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    extension: str = None,
    is_duplicate: bool = None,
    keyword: str = None
):
    db = SessionLocal()
    query = db.query(FileEntry)

    if extension:
        query = query.filter(FileEntry.extension == extension)
    if is_duplicate is not None:
        query = query.filter(FileEntry.is_duplicate == is_duplicate)
    if keyword:
        query = query.filter(FileEntry.name.contains(keyword))

    total = query.count()
    items = query.order_by(FileEntry.id.desc()).offset((page-1)*page_size).limit(page_size).all()

    duplicates_count = db.query(FileEntry).filter(FileEntry.is_duplicate == True).count()

    db.close()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "duplicates_count": duplicates_count,
        "items": [{
            "id": e.id,
            "path": e.path,
            "name": e.name,
            "extension": e.extension,
            "size": e.size,
            "md5": e.md5,
            "created_time": e.created_time.isoformat() if e.created_time else None,
            "modified_time": e.modified_time.isoformat() if e.modified_time else None,
            "is_duplicate": e.is_duplicate,
            "duplicate_of_id": e.duplicate_of_id
        } for e in items]
    }

@app.get("/duplicates")
async def get_duplicates():
    db = SessionLocal()
    duplicates = db.query(FileEntry).filter(FileEntry.is_duplicate == True).all()
    db.close()

    groups = {}
    for dup in duplicates:
        if dup.duplicate_of_id:
            if dup.duplicate_of_id not in groups:
                groups[dup.duplicate_of_id] = []
            groups[dup.duplicate_of_id].append({
                "id": dup.id,
                "path": dup.path,
                "name": dup.name,
                "size": dup.size,
                "modified_time": dup.modified_time.isoformat() if dup.modified_time else None
            })

    return {"groups": groups, "total_duplicates": len(duplicates)}

@app.post("/deduplicate")
async def deduplicate():
    """
    查找并标记重复文件
    """
    async def generate():
        db = SessionLocal()
        for result in deduplicate_files(db):
            yield _send_sse(result)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })

@app.post("/rename-by-date")
async def rename_by_date():
    """
    将重复文件按日期重命名
    """
    async def generate():
        db = SessionLocal()
        for result in rename_duplicates_by_date(db):
            yield _send_sse(result)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })

@app.post("/archive")
async def archive_files(request: dict):
    """
    归档文件到指定目录
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    os.makedirs(target_dir, exist_ok=True)

    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    results = []
    for entry in entries:
        unique_name = get_unique_filename(target_dir, entry.name)
        target_path = os.path.join(target_dir, unique_name)

        try:
            if mode == "move":
                shutil.move(entry.path, target_path)
            else:
                shutil.copy2(entry.path, target_path)

            entry.path = target_path
            entry.name = unique_name
            db.commit()

            results.append({
                "id": entry.id,
                "success": True,
                "new_path": target_path
            })
        except Exception as e:
            results.append({
                "id": entry.id,
                "success": False,
                "error": str(e)
            })

    db.close()
    return {"results": results}

@app.delete("/files/{file_id}")
async def delete_file(file_id: int):
    db = SessionLocal()
    entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="文件记录不存在")

    if os.path.exists(entry.path):
        try:
            os.remove(entry.path)
        except Exception as e:
            db.close()
            raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")

    db.delete(entry)
    db.commit()
    db.close()

    return {"success": True, "message": "文件已删除"}

@app.delete("/duplicates/{duplicate_id}")
async def delete_duplicate(duplicate_id: int):
    db = SessionLocal()
    entry = db.query(FileEntry).filter(FileEntry.id == duplicate_id).first()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="文件不存在")

    if not entry.is_duplicate:
        db.close()
        raise HTTPException(status_code=400, detail="该文件不是重复文件")

    if os.path.exists(entry.path):
        try:
            os.remove(entry.path)
        except Exception as e:
            db.close()
            raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")

    db.delete(entry)
    db.commit()
    db.close()

    return {"success": True, "message": "重复文件已删除"}

@app.get("/stats")
async def get_stats():
    db = SessionLocal()
    total_files = db.query(FileEntry).count()
    total_duplicates = db.query(FileEntry).filter(FileEntry.is_duplicate == True).count()
    total_size = sum(e.size for e in db.query(FileEntry).all())

    by_extension = {}
    for ext in ALL_SUPPORTED:
        count = db.query(FileEntry).filter(FileEntry.extension == ext).count()
        if count > 0:
            by_extension[ext] = count

    db.close()

    return {
        "total_files": total_files,
        "total_duplicates": total_duplicates,
        "total_size": total_size,
        "by_extension": by_extension
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5678)
