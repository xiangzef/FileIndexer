import os
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from database import FileEntry

def get_file_status(file: FileEntry) -> str:
    if not os.path.exists(file.path):
        return 'unavailable'
    return file.status or 'available'

def update_file_status(db: Session, file_id: int, status: str) -> bool:
    entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if entry:
        entry.status = status
        db.commit()
        return True
    return False

def check_file_accessible(file_path: str) -> bool:
    try:
        return os.path.exists(file_path) and os.access(file_path, os.R_OK)
    except:
        return False

def check_and_update_unavailable_files(db: Session) -> Dict[str, Any]:
    all_entries = db.query(FileEntry).all()
    unavailable_count = 0
    updated_count = 0

    for entry in all_entries:
        is_accessible = check_file_accessible(entry.path)
        if not is_accessible and entry.status != 'unavailable':
            entry.status = 'unavailable'
            updated_count += 1
        if not is_accessible:
            unavailable_count += 1

    db.commit()
    return {
        'total_files': len(all_entries),
        'unavailable_count': unavailable_count,
        'updated_count': updated_count
    }

def get_files_by_source_path(db: Session, source_path: str = None) -> List[FileEntry]:
    query = db.query(FileEntry)
    if source_path:
        query = query.filter(FileEntry.source_path == source_path)
    return query.all()

def get_all_source_paths(db: Session) -> List[str]:
    entries = db.query(FileEntry.source_path).filter(
        FileEntry.source_path.isnot(None)
    ).distinct().all()
    return [entry[0] for entry in entries if entry[0]]

def suspend_files_by_source(db: Session, source_path: str) -> Dict[str, Any]:
    entries = db.query(FileEntry).filter(FileEntry.source_path == source_path).all()
    count = 0
    for entry in entries:
        entry.status = 'suspended'
        count += 1
    db.commit()
    return {
        'source_path': source_path,
        'suspended_count': count
    }

def restore_files_by_source(db: Session, source_path: str) -> Dict[str, Any]:
    entries = db.query(FileEntry).filter(FileEntry.source_path == source_path).all()
    count = 0
    for entry in entries:
        if entry.status == 'suspended':
            entry.status = 'available'
            count += 1
    db.commit()
    return {
        'source_path': source_path,
        'restored_count': count
    }

def delete_files_by_source(db: Session, source_path: str) -> Dict[str, Any]:
    entries = db.query(FileEntry).filter(FileEntry.source_path == source_path).all()
    count = 0
    for entry in entries:
        db.delete(entry)
        count += 1
    db.commit()
    return {
        'source_path': source_path,
        'deleted_count': count
    }

def suspend_files_by_ids(db: Session, file_ids: List[int]) -> Dict[str, Any]:
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
    count = 0
    for entry in entries:
        entry.status = 'suspended'
        count += 1
    db.commit()
    return {
        'suspended_count': count
    }

def restore_files_by_ids(db: Session, file_ids: List[int]) -> Dict[str, Any]:
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
    count = 0
    for entry in entries:
        if entry.status == 'suspended':
            entry.status = 'available'
            count += 1
    db.commit()
    return {
        'restored_count': count
    }

def get_source_stats(db: Session) -> List[Dict[str, Any]]:
    source_paths = get_all_source_paths(db)
    stats = []
    for source in source_paths:
        entries = db.query(FileEntry).filter(FileEntry.source_path == source).all()
        total = len(entries)
        unavailable = len([e for e in entries if e.status == 'unavailable'])
        suspended = len([e for e in entries if e.status == 'suspended'])
        stats.append({
            'source_path': source,
            'total': total,
            'unavailable': unavailable,
            'suspended': suspended,
            'available': total - unavailable - suspended
        })
    return stats
