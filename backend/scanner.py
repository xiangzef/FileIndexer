import os
import hashlib
import threading
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Generator, Dict, Any
from sqlalchemy.orm import Session
from database import FileEntry, ScanRecord

DOC_EXTENSIONS = {'.doc', '.docx', '.wps', '.wpt'}
CSV_EXTENSIONS = {'.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}
PDF_EXTENSIONS = {'.pdf'}
ALL_SUPPORTED = DOC_EXTENSIONS | CSV_EXTENSIONS | IMAGE_EXTENSIONS | PDF_EXTENSIONS

SCAN_STOP_FLAG = threading.Event()
SCAN_LOCK = threading.Lock()
_active_scan = None

def set_stop_check(callback):
    global SCAN_STOP_FLAG
    SCAN_STOP_FLAG = threading.Event()
    def check():
        if callback():
            SCAN_STOP_FLAG.set()
    return check

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

    total = len(all_files)
    yield {"type": "start", "directory": directory, "total": total, "scan_record_id": scan_record.id}

    stats = {}
    scanned_count = 0
    scanned_size = 0

    for idx, file_path in enumerate(all_files):
        if SCAN_STOP_FLAG.is_set():
            scan_record.status = 'stopped'
            scan_record.total_files = scanned_count
            scan_record.total_size = scanned_size
            scan_record.stats_json = json.dumps(stats)
            db.commit()
            yield {"type": "stopped", "directory": directory}
            return

        stat = None
        for attempt in range(3):
            if SCAN_STOP_FLAG.is_set():
                scan_record.status = 'stopped'
                scan_record.total_files = scanned_count
                scan_record.total_size = scanned_size
                scan_record.stats_json = json.dumps(stats)
                db.commit()
                yield {"type": "stopped", "directory": directory}
                return
            try:
                stat = os.stat(file_path)
                break
            except:
                time.sleep(0.05)
        if stat is None:
            continue

        ext = os.path.splitext(file_path)[1].lower()
        existing = db.query(FileEntry).filter_by(path=file_path).first()
        if existing:
            continue

        try:
            entry = FileEntry(
                path=file_path,
                name=os.path.basename(file_path),
                extension=ext,
                size=stat.st_size,
                md5=None,
                created_time=datetime.fromtimestamp(stat.st_ctime),
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                scan_time=datetime.now(),
                is_duplicate=False,
                duplicate_of_id=None,
                status='available',
                source_path=directory,
                scan_record_id=scan_record.id
            )
            db.add(entry)

            scanned_count += 1
            scanned_size += stat.st_size

            if ext not in stats:
                stats[ext] = {"count": 0, "size": 0}
            stats[ext]["count"] += 1
            stats[ext]["size"] += stat.st_size

            if scanned_count % 10 == 0:
                scan_record.total_files = scanned_count
                scan_record.total_size = scanned_size
                db.commit()

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
            "file_id": entry.id,
            "md5": md5,
            "percentage": int((idx + 1) / total * 100)
        }

    yield {"type": "complete", "total": total}

def find_duplicates(db: Session) -> List[Dict[str, Any]]:
    duplicates = []

    md5_groups = {}
    entries = db.query(FileEntry).filter(FileEntry.md5.isnot(None)).all()

    for entry in entries:
        if entry.md5 not in md5_groups:
            md5_groups[entry.md5] = []
        md5_groups[entry.md5].append(entry)

    for md5, group in md5_groups.items():
        if len(group) > 1:
            group.sort(key=lambda x: (x.modified_time or datetime.min, x.path))
            original = group[0]
            for dup in group[1:]:
                dup.is_duplicate = True
                dup.duplicate_of_id = original.id
                duplicates.append({
                    "original_id": original.id,
                    "original_path": original.path,
                    "duplicate_id": dup.id,
                    "duplicate_path": dup.path,
                    "size": dup.size,
                    "md5": md5
                })

    db.commit()
    return duplicates

def stop_scan():
    global SCAN_STOP_FLAG
    SCAN_STOP_FLAG.set()