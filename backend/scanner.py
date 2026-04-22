import os
import hashlib
import threading
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Generator, Dict, Any
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import Session
from database import FileEntry, ScanRecord

DOC_EXTENSIONS = {'.doc', '.docx', '.wps', '.wpt'}
CSV_EXTENSIONS = {'.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}
PDF_EXTENSIONS = {'.pdf'}
PPT_EXTENSIONS = {'.ppt', '.pptx', '.pot', '.potx'}
EBOOK_EXTENSIONS = {'.epub', '.mobi', '.azw', '.azw3', '.azw4', '.kf8', '.kfx', '.fb2', '.cbr', '.cbz', '.chm', '.ibooks'}
ALL_SUPPORTED = DOC_EXTENSIONS | CSV_EXTENSIONS | IMAGE_EXTENSIONS | PDF_EXTENSIONS | PPT_EXTENSIONS | EBOOK_EXTENSIONS

SCAN_STOP_FLAG = threading.Event()
SCAN_LOCK = threading.Lock()
_active_scan = None

BATCH_COMMIT_SIZE = 100
BATCH_QUERY_SIZE = 500

def set_stop_check(callback):
    global SCAN_STOP_FLAG
    SCAN_STOP_FLAG = threading.Event()
    def check():
        if callback():
            SCAN_STOP_FLAG.set()
    return check

def clear_stop_flag():
    global SCAN_STOP_FLAG
    SCAN_STOP_FLAG.clear()

def calculate_md5(file_path: str, chunk_size: int = 8192) -> Optional[str]:
    try:
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except Exception:
        return None

def scan_directory(
    db: Session,
    directory: str,
    extensions: Optional[set] = None,
    progress_callback=None
) -> Generator[Dict[str, Any], None, None]:
    global SCAN_STOP_FLAG, _active_scan

    if extensions is None:
        extensions = ALL_SUPPORTED

    _active_scan = threading.current_thread()

    scan_record = ScanRecord(
        scan_path=directory,
        scan_time=datetime.now(),
        total_files=0,
        total_size=0,
        status='active'
    )
    db.add(scan_record)
    db.commit()

    all_files = []
    file_count = 0
    for root, dirs, files in os.walk(directory):
        if SCAN_STOP_FLAG.is_set():
            scan_record.status = 'stopped'
            db.commit()
            yield {"type": "stopped", "directory": directory}
            return

        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in extensions:
                full_path = os.path.join(root, filename)
                all_files.append(full_path)

        file_count += len(files)
        if file_count % 1000 == 0:
            yield {"type": "progress", "current": len(all_files), "total": None, "percentage": None}

    total = len(all_files)
    yield {"type": "start", "directory": directory, "total": total, "scan_record_id": scan_record.id}

    existing_paths = set()
    for i in range(0, len(all_files), BATCH_QUERY_SIZE):
        batch = all_files[i:i + BATCH_QUERY_SIZE]
        existing = db.query(FileEntry.path).filter(FileEntry.path.in_(batch)).all()
        existing_paths.update(row[0] for row in existing)
        if SCAN_STOP_FLAG.is_set():
            scan_record.status = 'stopped'
            db.commit()
            yield {"type": "stopped", "directory": directory}
            return

    stats = {}
    scanned_count = 0
    scanned_size = 0
    entries_to_add = []
    now = datetime.now()

    for idx, file_path in enumerate(all_files):
        if SCAN_STOP_FLAG.is_set():
            if entries_to_add:
                db.add_all(entries_to_add)
                db.commit()
            scan_record.status = 'stopped'
            scan_record.total_files = scanned_count
            scan_record.total_size = scanned_size
            scan_record.stats_json = json.dumps(stats)
            db.commit()
            yield {"type": "stopped", "directory": directory}
            return

        if file_path in existing_paths:
            continue

        stat = None
        for attempt in range(3):
            try:
                stat = os.stat(file_path)
                break
            except:
                time.sleep(0.01)
        if stat is None:
            continue

        ext = os.path.splitext(file_path)[1].lower()

        try:
            entry = FileEntry(
                path=file_path,
                name=os.path.basename(file_path),
                extension=ext,
                size=stat.st_size,
                md5=None,
                created_time=datetime.fromtimestamp(stat.st_ctime),
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                scan_time=now,
                is_duplicate=False,
                duplicate_of_id=None,
                status='available',
                source_path=directory,
                scan_record_id=scan_record.id
            )
            entries_to_add.append(entry)

            scanned_count += 1
            scanned_size += stat.st_size

            if ext not in stats:
                stats[ext] = {"count": 0, "size": 0}
            stats[ext]["count"] += 1
            stats[ext]["size"] += stat.st_size

            if len(entries_to_add) >= BATCH_COMMIT_SIZE:
                db.add_all(entries_to_add)
                db.commit()
                entries_to_add = []

                yield {
                    "type": "progress",
                    "directory": directory,
                    "current": idx + 1,
                    "total": total,
                    "file": file_path,
                    "percentage": int((idx + 1) / total * 100),
                    "stats": stats
                }
        except Exception as e:
            yield {"type": "error", "file": file_path, "message": str(e)}

    if entries_to_add:
        db.add_all(entries_to_add)
        db.commit()

    scan_record.total_files = scanned_count
    scan_record.total_size = scanned_size
    scan_record.stats_json = json.dumps(stats)
    db.commit()

    yield {"type": "complete", "directory": directory, "total": total, "stats": stats}

def compute_md5_batch(db: Session, file_ids: List[int]) -> Generator[Dict[str, Any], None, None]:
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
    total = len(entries)

    yield {"type": "start", "total": total}

    for idx, entry in enumerate(entries):
        if SCAN_STOP_FLAG.is_set():
            yield {"type": "stopped"}
            return

        md5 = calculate_md5(entry.path)
        if md5:
            entry.md5 = md5
            db.commit()

        yield {
            "type": "progress",
            "current": idx + 1,
            "total": total,
            "file": entry.path
        }

    yield {"type": "complete", "total": total}

def find_duplicates(db: Session, scan_record_id: int) -> Generator[Dict[str, Any], None, None]:
    entries = db.query(FileEntry).filter(and_(FileEntry.scan_record_id == scan_record_id, FileEntry.md5 != None)).all()
    md5_groups = {}
    for entry in entries:
        if entry.md5:
            if entry.md5 not in md5_groups:
                md5_groups[entry.md5] = []
            md5_groups[entry.md5].append(entry)

    duplicates = []
    for md5, group in md5_groups.items():
        if len(group) > 1:
            for entry in group[1:]:
                entry.is_duplicate = True
                entry.duplicate_of_id = group[0].id
                duplicates.append(entry.id)

    if duplicates:
        db.commit()

    yield {"type": "complete", "duplicate_count": len(duplicates)}

def stop_scan():
    global SCAN_STOP_FLAG
    SCAN_STOP_FLAG.set()
