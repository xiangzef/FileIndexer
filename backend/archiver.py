import os
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from typing import List, Generator, Dict, Any, Optional
from sqlalchemy.orm import Session
from database import FileEntry

def calculate_md5(file_path: str) -> Optional[str]:
    try:
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except Exception:
        return None

def get_unique_filename(target_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    target_path = os.path.join(target_dir, filename)

    if not os.path.exists(target_path):
        return filename

    counter = 1
    while True:
        new_filename = f"{base}_{counter}{ext}"
        new_path = os.path.join(target_dir, new_filename)
        if not os.path.exists(new_path):
            return new_filename
        counter += 1

def deduplicate_files(db: Session, keep_originals: bool = True) -> Generator[Dict[str, Any], None, None]:
    md5_groups = {}
    entries = db.query(FileEntry).filter(FileEntry.md5.isnot(None)).all()

    for entry in entries:
        if entry.md5 not in md5_groups:
            md5_groups[entry.md5] = []
        md5_groups[entry.md5].append(entry)

    total_duplicates = 0
    total_space_saved = 0
    processed_groups = 0

    for md5, group in md5_groups.items():
        if len(group) <= 1:
            continue

        processed_groups += 1
        group.sort(key=lambda x: (x.modified_time or datetime.min, x.path))

        original = group[0]
        for dup in group[1:]:
            total_duplicates += 1
            total_space_saved += dup.size
            yield {
                "type": "duplicate",
                "original_id": original.id,
                "original_path": original.path,
                "duplicate_id": dup.id,
                "duplicate_path": dup.path,
                "size": dup.size,
                "md5": md5
            }

    yield {
        "type": "summary",
        "groups_processed": processed_groups,
        "total_duplicates": total_duplicates,
        "total_space_saved": total_space_saved
    }

def rename_duplicates_by_date(db: Session) -> Generator[Dict[str, Any], None, None]:
    entries = db.query(FileEntry).filter(
        FileEntry.is_duplicate == True,
        FileEntry.duplicate_of_id.isnot(None)
    ).all()

    total = len(entries)
    yield {"type": "start", "total": total}

    for idx, entry in enumerate(entries):
        if not entry.modified_time:
            entry.modified_time = datetime.now()

        date_suffix = entry.modified_time.strftime("%Y%m%d")
        base, ext = os.path.splitext(entry.name)
        dir_path = os.path.dirname(entry.path)

        new_name = f"{base}_{date_suffix}{ext}"
        new_path = os.path.join(dir_path, new_name)

        counter = 1
        while os.path.exists(new_path) and new_path != entry.path:
            new_name = f"{base}_{date_suffix}_{counter}{ext}"
            new_path = os.path.join(dir_path, new_name)
            counter += 1

        if new_path != entry.path:
            try:
                os.rename(entry.path, new_path)
                entry.path = new_path
                entry.name = new_name
                db.commit()
                yield {
                    "type": "renamed",
                    "old_path": entry.path,
                    "new_path": new_path,
                    "current": idx + 1,
                    "total": total
                }
            except Exception as e:
                yield {"type": "error", "file": entry.path, "message": str(e)}
        else:
            yield {
                "type": "skipped",
                "file": entry.path,
                "reason": "already_unique",
                "current": idx + 1,
                "total": total
            }

    yield {"type": "complete", "total": total}
