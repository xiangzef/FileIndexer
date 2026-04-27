from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import os
import re
import json
import shutil
import threading
import asyncio
from datetime import datetime

from database import engine, SessionLocal, Base, FileEntry, ScanRecord
from scanner import (
    scan_directory, compute_md5_batch, find_duplicates,
    stop_scan, ALL_SUPPORTED, calculate_md5, SCAN_STOP_FLAG
)
from archiver import deduplicate_files, rename_duplicates_by_date, get_unique_filename, archive_files_smart
from ai_analyzer import analyze_files, ai_archive_files
from ai_provider import get_ai_provider
from ai_organizer import OrganizePromptBuilder, LearnedRule
from auto_mode import auto_detect_mode
from file_manager import (
    check_and_update_unavailable_files,
    get_all_source_paths,
    get_source_stats,
    suspend_files_by_source,
    restore_files_by_source,
    delete_files_by_source,
    suspend_files_by_ids,
    restore_files_by_ids
)

app = FastAPI(title="FileIndexer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _sanitize_json(text: str) -> str:
    """修复本地模型常见的JSON格式问题"""
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace('，', ',').replace('：', ':').replace('【', '[').replace('】', ']')
    text = text.replace('{', '{').replace('}', '}')
    text = text.replace('（', '(').replace('）', ')')
    return text

def _fix_json_brackets(text: str) -> str:
    """修复括号错乱问题：把对象位置的()替换为{}"""
    result = []
    i = 0
    in_string = False
    escape_next = False
    while i < len(text):
        ch = text[i]
        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == '\\':
            result.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if not in_string:
            if ch == '(':
                result.append('{')
                i += 1
                continue
            elif ch == ')':
                result.append('}')
                i += 1
                continue
        result.append(ch)
        i += 1
    return ''.join(result)

def _extract_json_array(text: str):
    """从文本中提取JSON数组"""
    text = _sanitize_json(text)
    text = _fix_json_brackets(text)
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i+1]
    return None

def _extract_json_object(text: str):
    """从文本中提取JSON对象"""
    text = _sanitize_json(text)
    text = _fix_json_brackets(text)
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i+1]
    return None

def _try_fix_json(text: str) -> str:
    """尝试修复不完整的JSON"""
    text = _sanitize_json(text)
    text = _fix_json_brackets(text)
    text = text.strip()
    if not text.startswith('{') and not text.startswith('['):
        if text.startswith('json') or text.startswith('JSON') or text.startswith('```'):
            lines = text.split('\n')
            for idx, line in enumerate(lines):
                line = line.strip()
                if line.startswith('{') or line.startswith('['):
                    text = '\n'.join(lines[idx:])
                    break
            else:
                for idx, line in enumerate(lines):
                    if '{' in line or '[' in line:
                        text = '\n'.join(lines[idx:])
                        break
        else:
            idx = text.find('{')
            if idx >= 0:
                text = text[idx:]
            else:
                idx = text.find('[')
                if idx >= 0:
                    text = text[idx:]
    
    # 修复截断的JSON：补全未闭合的括号和引号
    if text:
        fixed = _complete_json(text)
        if fixed:
            return fixed
    return text

def _complete_json(text: str) -> str:
    """尝试补全被截断的JSON"""
    stack = []
    in_string = False
    escape_next = False
    last_valid = 0
    
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            stack.append((ch, i))
            last_valid = i
        elif ch in ('}', ']'):
            if stack:
                open_ch, _ = stack[-1]
                if (open_ch == '{' and ch == '}') or (open_ch == '[' and ch == ']'):
                    stack.pop()
                    last_valid = i
                else:
                    # 括号不匹配，截断到这里
                    return text[:i] + _close_brackets(stack)
            else:
                # 多余的闭合括号，截断
                return text[:i] + _close_brackets([])
    
    # 如果还有未闭合的括号，尝试补全
    if stack:
        return text + _close_brackets(stack)
    return None

def _close_brackets(stack) -> str:
    """补全未闭合的括号"""
    result = []
    for ch, _ in reversed(stack):
        if ch == '{':
            result.append('}')
        elif ch == '[':
            result.append(']')
    return ''.join(result)

def _ai_organize_chunked(files, learned_rules, include_content, ai_provider):
    """
    分批处理大量文件的AI整理
    """
    BATCH_SIZE = 50
    batches = [files[i:i+BATCH_SIZE] for i in range(0, len(files), BATCH_SIZE)]

    prompt_builder = OrganizePromptBuilder(learned_rules)
    system_prompt = prompt_builder.build_system_prompt()

    all_classifications = {}
    total_batches = len(batches)

    for batch_idx, batch in enumerate(batches):
        batch_data = []
        for f in batch:
            fd = {"id": f.id, "name": f.name, "ext": f.extension, "size": f.size}
            if include_content and f.extension in ['.txt', '.csv', '.md', '.log', '.py', '.js', '.java', '.cpp', '.c', '.h', '.css', '.html', '.xml', '.json']:
                try:
                    with open(f.path, 'r', encoding='utf-8', errors='ignore') as fp:
                        fd['text'] = fp.read()[:150]
                except:
                    pass
            batch_data.append(fd)

        user_prompt = f"""这是第{batch_idx+1}/{total_batches}批文件（共{len(batch)}个）。请分析并返回JSON格式的分类结果：

{prompt_builder.format_file_list(batch_data)}

只返回一个JSON数组，格式：[{{"name":"文件名","folder":"建议的文件夹名"}}]"""

        response = ai_provider.chat(system_prompt, user_prompt)
        if response.startswith("错误:"):
            print(f"第{batch_idx+1}批处理失败: {response}")
            continue

        try:
            classifications = json.loads(response)
            if isinstance(classifications, list):
                for c in classifications:
                    if isinstance(c, dict) and 'name' in c and 'folder' in c:
                        all_classifications[c['name']] = c['folder']
                print(f"第{batch_idx+1}批处理成功，分类{len(classifications)}个文件")
                continue
        except:
            pass

        json_str = _extract_json_array(response)
        if json_str:
            try:
                classifications = json.loads(_sanitize_json(json_str))
                if isinstance(classifications, list):
                    for c in classifications:
                        if isinstance(c, dict) and 'name' in c and 'folder' in c:
                            all_classifications[c['name']] = c['folder']
                    print(f"第{batch_idx+1}批提取成功，分类{len(classifications)}个文件")
                    continue
            except Exception as e:
                print(f"第{batch_idx+1}批解析失败: {e}")

        json_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', response)
        if json_match:
            try:
                classifications = json.loads(_sanitize_json(json_match.group(0)))
                if isinstance(classifications, list):
                    for c in classifications:
                        if isinstance(c, dict) and 'name' in c and 'folder' in c:
                            all_classifications[c['name']] = c['folder']
                    print(f"第{batch_idx+1}批正则提取成功，分类{len(classifications)}个文件")
                    continue
            except:
                pass

        print(f"第{batch_idx+1}批解析失败，跳过。返回内容: {response[:150]}")
        continue

    if not all_classifications:
        return {"error": "AI分类失败，所有批次均未成功返回结果"}

    file_list = "\n".join([f"{f.name} → {all_classifications.get(f.name, '未分类')}" for f in files])

    final_prompt = f"""基于以下文件分类结果，生成最终的整理方案JSON：

{file_list}

请返回JSON格式，只返回JSON不要其他内容：
{{"folders":[{{"name":"文件夹名","files":[{{"id":文件ID,"name":"文件名"}}]}}]}}"""

    response = ai_provider.chat(system_prompt, final_prompt)
    if response.startswith("错误:"):
        return {"error": f"生成最终方案失败: {response}"}

    try:
        plan = json.loads(response)
        if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'folders' in plan[0]:
            return plan[0]
        return plan
    except:
        pass

    json_str = _extract_json_object(response)
    if json_str:
        try:
            plan = json.loads(json_str)
            return plan
        except Exception as e:
            print(f"提取JSON对象失败: {e}")

    json_str = _extract_json_array(response)
    if json_str:
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and 'folders' in parsed[0]:
                return parsed[0]
        except:
            pass

    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
    if json_match:
        content = json_match.group(1).strip()
        content = _try_fix_json(content)
        try:
            plan = json.loads(content)
            if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'folders' in plan[0]:
                return plan[0]
            return plan
        except:
            pass

    fixed = _try_fix_json(response)
    if fixed:
        try:
            plan = json.loads(fixed)
            if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'folders' in plan[0]:
                return plan[0]
            return plan
        except:
            pass

    return {"error": f"AI返回格式错误。实际返回: {response[:300]}"}

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(status_code=500, content={"error": f"服务器内部错误: {str(exc)}"})

Base.metadata.create_all(bind=engine)

scan_lock = threading.Lock()
_stop_flags = {}

def _send_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

@app.get("/")
async def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"))

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

@app.get("/ai/models")
async def get_ai_models(provider: str = "ollama"):
    from ai_provider import PROVIDER_CONFIGS
    config = PROVIDER_CONFIGS.get(provider, {})
    return {
        "provider": provider,
        "default_model": config.get("default_model", ""),
        "models": config.get("models", [])
    }

@app.post("/scan")
async def scan_paths(request: Request):
    """
    扫描指定目录，生成文件索引
    """
    import uuid

    body = await request.json()
    paths = body if isinstance(body, list) else body.get("paths", [])

    job_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    from scanner import clear_stop_flag
    clear_stop_flag()
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
                if stop_event.is_set():
                    yield _send_sse({"type": "stopped", "directory": directory})
                    break
                if result["type"] == "progress":
                    yield _send_sse(result)
                elif result["type"] == "complete":
                    total_scanned += result["total"]
                    yield _send_sse(result)
                elif result["type"] == "error":
                    yield _send_sse(result)
                elif result["type"] == "stopped":
                    yield _send_sse(result)
                    break

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
    from scanner import SCAN_STOP_FLAG
    if job_id:
        with scan_lock:
            if job_id in _stop_flags:
                _stop_flags[job_id].set()
                SCAN_STOP_FLAG.set()
                return {"success": True, "message": f"已停止任务: {job_id}"}
    else:
        for ev in _stop_flags.values():
            ev.set()
        SCAN_STOP_FLAG.set()
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
    keyword: str = None,
    status: str = None,
    file_type: str = None,
    file_types: str = None
):
    db = SessionLocal()
    query = db.query(FileEntry)

    if extension:
        query = query.filter(FileEntry.extension == extension)
    if is_duplicate is not None:
        query = query.filter(FileEntry.is_duplicate == is_duplicate)
    if keyword:
        query = query.filter(FileEntry.name.contains(keyword))
    if status:
        query = query.filter(FileEntry.status == status)
    if file_type:
        type_map = {
            'doc': ['.doc', '.docx', '.wps', '.wpt'],
            'pdf': ['.pdf'],
            'xls': ['.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'],
            'ppt': ['.ppt', '.pptx', '.pot', '.potx'],
            'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
            'txt': ['.txt', '.md', '.log', '.chm'],
            'ebook': ['.epub', '.mobi', '.azw', '.azw3', '.azw4', '.kf8', '.kfx', '.fb2', '.cbr', '.cbz', '.ibooks']
        }
        exts = type_map.get(file_type, [])
        if exts:
            query = query.filter(FileEntry.extension.in_(exts))
    if file_types:
        import json
        try:
            types = json.loads(file_types)
            all_exts = []
            type_map = {
                'doc': ['.doc', '.docx', '.wps', '.wpt'],
                'pdf': ['.pdf'],
                'xls': ['.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'],
                'ppt': ['.ppt', '.pptx', '.pot', '.potx'],
                'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
                'txt': ['.txt', '.md', '.log', '.chm'],
                'ebook': ['.epub', '.mobi', '.azw', '.azw3', '.azw4', '.kf8', '.kfx', '.fb2', '.cbr', '.cbz', '.ibooks']
            }
            for t in types:
                all_exts.extend(type_map.get(t, []))
            if all_exts:
                query = query.filter(FileEntry.extension.in_(all_exts))
        except:
            pass

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
            "duplicate_of_id": e.duplicate_of_id,
            "status": e.status,
            "source_path": e.source_path
        } for e in items]
    }

@app.get("/files/all-ids")
async def get_all_file_ids(
    extension: str = None,
    is_duplicate: bool = None,
    keyword: str = None,
    status: str = None,
    file_types: str = None
):
    db = SessionLocal()
    query = db.query(FileEntry.id)

    if extension:
        query = query.filter(FileEntry.extension == extension)
    if is_duplicate is not None:
        query = query.filter(FileEntry.is_duplicate == is_duplicate)
    if keyword:
        query = query.filter(FileEntry.name.contains(keyword))
    if status:
        query = query.filter(FileEntry.status == status)
    else:
        query = query.filter(FileEntry.status != 'suspended')
    if file_types:
        import json
        try:
            types = json.loads(file_types)
            all_exts = []
            type_map = {
                'doc': ['.doc', '.docx'],
                'pdf': ['.pdf'],
                'xls': ['.csv', '.xls', '.xlsx', '.els', '.elsx'],
                'ppt': ['.ppt', '.pptx'],
                'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
                'txt': ['.txt', '.md', '.log']
            }
            for t in types:
                all_exts.extend(type_map.get(t, []))
            if all_exts:
                query = query.filter(FileEntry.extension.in_(all_exts))
        except:
            pass

    ids = [row[0] for row in query.all()]
    db.close()
    return {"ids": ids, "total": len(ids)}

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
    智能归档文件到指定目录，按文件类型分类，相似文件归到同一文件夹
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    async def generate():
        db = SessionLocal()
        for result in archive_files_smart(db, file_ids, target_dir, mode):
            yield _send_sse(result)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })

@app.post("/archive-simple")
async def archive_files_simple(request: dict):
    """
    简单归档文件到指定目录（不分类）
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    path_updates = []
    results = []
    for entry in entries:
        try:
            temp_name = get_unique_filename(temp_dir, entry.name)
            temp_path = os.path.join(temp_dir, temp_name)

            if mode == "move":
                shutil.move(entry.path, temp_path)
            else:
                shutil.copy2(entry.path, temp_path)

            unique_name = get_unique_filename(target_dir, entry.name)
            target_path = os.path.join(target_dir, unique_name)
            shutil.move(temp_path, target_path)

            path_updates.append((entry, target_path, unique_name))
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

    for entry, new_path, new_name in path_updates:
        entry.path = new_path
        entry.name = new_name

    db.commit()
    db.close()

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

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

@app.post("/ai/analyze")
async def ai_analyze_files(request: dict):
    """
    使用AI分析文件，基于文件名和内容
    """
    try:
        file_ids = request.get("file_ids", [])
        provider = request.get("provider", "ollama")
        api_key = request.get("api_key", None)
        model = request.get("model", None)
        base_url = request.get("base_url", None)

        db = SessionLocal()
        try:
            ai_provider = get_ai_provider(provider, api_key, model, base_url)
            result = analyze_files(db, file_ids, ai_provider)
            return result
        finally:
            db.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e), "detail": "AI分析失败，请检查配置和文件路径"}

@app.post("/ai/archive")
async def ai_archive_files_endpoint(request: dict):
    """
    使用AI分析结果归档文件
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")
    provider = request.get("provider", "ollama")
    api_key = request.get("api_key", None)
    model = request.get("model", None)
    base_url = request.get("base_url", None)

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    db = SessionLocal()
    ai_provider = get_ai_provider(provider, api_key, model, base_url)
    results = ai_archive_files(db, file_ids, target_dir, mode, ai_provider)
    db.close()
    return {"results": results}

@app.post("/auto/detect-mode")
async def auto_detect_mode_endpoint(file_ids: List[int]):
    """
    自动检测文件的处理模式
    """
    db = SessionLocal()
    result = auto_detect_mode(db, file_ids)
    db.close()
    return result

@app.post("/check-unavailable")
async def check_unavailable():
    """
    检测并标记不可访问的文件
    """
    db = SessionLocal()
    result = check_and_update_unavailable_files(db)
    db.close()
    return result

@app.get("/source-paths")
async def get_source_paths():
    """
    获取所有来源路径及其统计信息
    """
    db = SessionLocal()
    stats = get_source_stats(db)
    db.close()
    return {"sources": stats}

@app.post("/source/{source_path}/suspend")
async def suspend_source(source_path: str):
    """
    挂起指定来源路径下的所有文件
    """
    db = SessionLocal()
    result = suspend_files_by_source(db, source_path)
    db.close()
    return result

@app.post("/source/{source_path}/restore")
async def restore_source(source_path: str):
    """
    恢复指定来源路径下的所有文件
    """
    db = SessionLocal()
    result = restore_files_by_source(db, source_path)
    db.close()
    return result

@app.delete("/source/{source_path}")
async def delete_source(source_path: str):
    """
    删除指定来源路径下的所有文件记录
    """
    db = SessionLocal()
    result = delete_files_by_source(db, source_path)
    db.close()
    return result

@app.post("/files/suspend")
async def suspend_files(request: dict):
    """
    挂起指定文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    result = suspend_files_by_ids(db, file_ids)
    db.close()
    return result

@app.post("/files/restore")
async def restore_files(request: dict):
    """
    恢复指定文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    result = restore_files_by_ids(db, file_ids)
    db.close()
    return result

@app.get("/scan-records")
async def get_scan_records():
    """
    获取所有扫描记录及统计
    """
    db = SessionLocal()
    records = db.query(ScanRecord).order_by(ScanRecord.scan_time.desc()).all()
    result = []
    for r in records:
        stats = {}
        if r.stats_json:
            try:
                stats = json.loads(r.stats_json)
            except:
                pass
        result.append({
            "id": r.id,
            "scan_path": r.scan_path,
            "scan_time": r.scan_time.isoformat() if r.scan_time else None,
            "total_files": r.total_files,
            "total_size": r.total_size,
            "status": r.status,
            "stats": stats
        })
    db.close()
    return {"records": result}

@app.post("/scan-records/{record_id}/suspend")
async def suspend_scan_record(record_id: int):
    """
    挂起指定扫描记录下的所有文件
    """
    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    count = 0
    for entry in entries:
        entry.status = 'suspended'
        count += 1
    db.commit()
    db.close()
    return {"suspended_count": count}

@app.post("/scan-records/{record_id}/restore")
async def restore_scan_record(record_id: int):
    """
    恢复指定扫描记录下的所有文件
    """
    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    count = 0
    for entry in entries:
        if entry.status == 'suspended':
            entry.status = 'available'
            count += 1
    db.commit()
    db.close()
    return {"restored_count": count}

@app.delete("/scan-records/{record_id}")
async def delete_scan_record(record_id: int):
    """
    删除指定扫描记录下的所有文件记录
    """
    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    count = 0
    for entry in entries:
        db.delete(entry)
        count += 1
    record = db.query(ScanRecord).filter(ScanRecord.id == record_id).first()
    if record:
        db.delete(record)
    db.commit()
    db.close()
    return {"deleted_count": count}

@app.post("/files/batch-delete")
async def batch_delete_files(request: dict):
    """
    批量删除选中的文件记录
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    count = 0
    for fid in file_ids:
        entry = db.query(FileEntry).filter(FileEntry.id == fid).first()
        if entry:
            db.delete(entry)
            count += 1
    db.commit()
    db.close()
    return {"deleted_count": count}

@app.post("/files/batch-suspend")
async def batch_suspend_files(request: dict):
    """
    批量挂起选中的文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    count = 0
    for fid in file_ids:
        entry = db.query(FileEntry).filter(FileEntry.id == fid).first()
        if entry:
            entry.status = 'suspended'
            count += 1
    db.commit()
    db.close()
    return {"suspended_count": count}

@app.post("/files/batch-restore")
async def batch_restore_files(request: dict):
    """
    批量恢复选中的文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    count = 0
    for fid in file_ids:
        entry = db.query(FileEntry).filter(FileEntry.id == fid).first()
        if entry and entry.status == 'suspended':
            entry.status = 'available'
            count += 1
    db.commit()
    db.close()
    return {"restored_count": count}

@app.get("/scan-record/{record_id}/files")
async def get_scan_record_files(record_id: int):
    """
    获取指定扫描记录下的所有文件
    """
    db = SessionLocal()
    files = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    result = []
    for f in files:
        result.append({
            "id": f.id,
            "name": f.name,
            "path": f.path,
            "extension": f.extension,
            "size": f.size,
            "md5": f.md5,
            "status": f.status
        })
    db.close()
    return {"files": result}

@app.post("/ai/organize/plan")
async def generate_organize_plan(request: dict):
    """
    使用AI生成文件整理方案
    """
    record_id = request.get("record_id")
    file_ids = request.get("file_ids")
    provider = request.get("provider", "ollama")
    api_key = request.get("api_key")
    model = request.get("model")
    include_content = request.get("include_content", False)
    learn_mode = request.get("learn_mode", True)

    db = SessionLocal()
    try:
        rule_storage_path = os.path.join(os.path.dirname(__file__), "learned_rules.json")
        rule_learner = LearnedRule(rule_storage_path)
        learned_rules = rule_learner.get_recent_rules(limit=10) if learn_mode else []

        ai_provider = get_ai_provider(provider, api_key, model)

        if file_ids:
            if len(file_ids) > 500:
                files = []
                for i in range(0, len(file_ids), 500):
                    batch = file_ids[i:i+500]
                    batch_files = db.query(FileEntry).filter(FileEntry.id.in_(batch)).all()
                    files.extend(batch_files)
            else:
                files = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
        else:
            files = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()

        if not files:
            return {"error": "没有找到文件"}

        MAX_FILES_FOR_AI = 60
        if len(files) > MAX_FILES_FOR_AI:
            return _ai_organize_chunked(files, learned_rules, include_content, ai_provider)

        file_data = []
        for f in files:
            fd = {
                "id": f.id,
                "name": f.name,
                "ext": f.extension,
                "size": f.size
            }

            if include_content and f.extension in ['.txt', '.csv', '.md', '.log', '.py', '.js', '.java', '.cpp', '.c', '.h', '.css', '.html', '.xml', '.json']:
                try:
                    with open(f.path, 'r', encoding='utf-8', errors='ignore') as fp:
                        fd['text'] = fp.read()[:300]
                except:
                    pass

            file_data.append(fd)

        prompt_builder = OrganizePromptBuilder(learned_rules)
        system_prompt = prompt_builder.build_system_prompt()
        user_prompt = prompt_builder.build_user_prompt(file_data, learned_rules, include_content)

        response = ai_provider.chat(system_prompt, user_prompt)

        if response.startswith("错误:"):
            return {"error": response}

        import json
        import re
        try:
            plan = json.loads(response)
        except json.JSONDecodeError:
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*\})\s*```', response)
            if json_match:
                plan_str = json_match.group(1)
            else:
                json_match = re.search(r'\{[\s\S]*\}', response)
                if json_match:
                    plan_str = json_match.group(0)
                else:
                    return {"error": f"AI返回格式错误: 未找到JSON。实际返回内容前200字符: {response[:200]}", "raw": response[:1000]}
            try:
                plan = json.loads(plan_str)
            except json.JSONDecodeError:
                brace_count = 0
                quote_count = 0
                in_string = False
                escape_next = False
                end_idx = len(plan_str)
                for i, c in enumerate(plan_str):
                    if escape_next:
                        escape_next = False
                        continue
                    if c == '\\' and in_string:
                        escape_next = True
                        continue
                    if c == '"' and not escape_next:
                        quote_count += 1
                        in_string = not in_string
                    elif not in_string:
                        if c == '{':
                            brace_count += 1
                        elif c == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                plan_str = plan_str[:end_idx]
                try:
                    plan = json.loads(plan_str)
                except json.JSONDecodeError as e:
                    last_valid_pos = e.pos
                    while last_valid_pos > 0:
                        try:
                            test_str = plan_str[:last_valid_pos] + '"]}]}'
                            plan = json.loads(test_str)
                            break
                        except:
                            last_valid_pos -= 1
                    else:
                        return {"error": f"AI返回格式被截断(位置:{e.pos})，内容片段: {plan_str[max(0,e.pos-50):e.pos+50]}", "raw": response[:1000]}
            else:
                return {"error": "AI返回格式错误: 未找到JSON", "raw": response[:500]}

        if learn_mode:
            for folder in plan.get('folders', []):
                rule_learner.add_rule(
                    pattern=folder.get('name', ''),
                    action=f"移动到 {folder['name']}",
                    file_count=len(folder.get('files', []))
                )

        return plan

    except Exception as e:
        return {"error": f"服务器错误: {str(e)}"}
    finally:
        db.close()

@app.post("/ai/organize/execute")
async def execute_organize_plan(request: dict):
    """
    执行AI整理方案
    """
    record_id = request.get("record_id")
    plan = request.get("plan", {})
    target_dir = request.get("target_dir", "")
    archive_mode = request.get("archive_mode", "copy")
    archive_smart = request.get("archive_smart", True)

    if not plan or not plan.get("folders"):
        return {"success": False, "message": "无效的整理方案"}

    db = SessionLocal()
    scan_record = db.query(ScanRecord).filter(ScanRecord.id == record_id).first()

    if not scan_record:
        db.close()
        return {"success": False, "message": "未找到扫描记录"}

    if not target_dir:
        target_dir = os.path.join(os.path.dirname(scan_record.scan_path), "AI整理")

    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    results = []
    file_ids_in_plan = set()

    def get_file_ids_from_folder(folder):
        ids = []
        for f in folder.get('files', []):
            if isinstance(f, dict) and 'id' in f:
                ids.append(f['id'])
                file_ids_in_plan.add(f['id'])
        for sub in folder.get('subfolders', []):
            ids.extend(get_file_ids_from_folder(sub))
        return ids

    copy_func = shutil.move if archive_mode == "move" else shutil.copy2

    for folder in plan.get('folders', []):
        if not archive_smart:
            folder_path = target_dir
        else:
            folder_path = os.path.join(target_dir, folder.get('name', '未分类'))
        os.makedirs(folder_path, exist_ok=True)

        for f in folder.get('files', []):
            if isinstance(f, dict) and 'id' in f:
                entry = db.query(FileEntry).filter(FileEntry.id == f['id']).first()
                if entry and os.path.exists(entry.path):
                    unique_path = os.path.join(folder_path, get_unique_filename(folder_path, entry.name))
                    try:
                        copy_func(entry.path, unique_path)
                        results.append(f"✓ {entry.name} → {folder.get('name', '')}")
                        if archive_mode == "move":
                            entry.path = unique_path
                            entry.status = 'archived'
                    except Exception as e:
                        results.append(f"✗ {entry.name}: {str(e)}")

        for sub in folder.get('subfolders', []):
            sub_path = os.path.join(folder_path, sub.get('name', '子文件夹'))
            os.makedirs(sub_path, exist_ok=True)

            for f in sub.get('files', []):
                if isinstance(f, dict) and 'id' in f:
                    entry = db.query(FileEntry).filter(FileEntry.id == f['id']).first()
                    if entry and os.path.exists(entry.path):
                        unique_path = os.path.join(sub_path, get_unique_filename(sub_path, entry.name))
                        try:
                            copy_func(entry.path, unique_path)
                            results.append(f"✓ {entry.name} → {sub.get('name', '')}")
                            if archive_mode == "move":
                                entry.path = unique_path
                                entry.status = 'archived'
                        except Exception as e:
                            results.append(f"✗ {entry.name}: {str(e)}")

    db.commit()

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    archived_count = len([r for r in results if r.startswith('✓')])
    db.close()

    return {
        "success": True,
        "message": f"整理完成！已处理 {archived_count} 个文件",
        "details": results
    }

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=56789)