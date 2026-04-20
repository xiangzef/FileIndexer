import os
import shutil
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import List, Generator, Dict, Any, Optional
from sqlalchemy.orm import Session
from database import FileEntry

DOC_EXTENSIONS = {'.doc', '.docx'}
CSV_EXTENSIONS = {'.csv', '.els', '.elsx'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}
PDF_EXTENSIONS = {'.pdf'}
ALL_SUPPORTED = DOC_EXTENSIONS | CSV_EXTENSIONS | IMAGE_EXTENSIONS | PDF_EXTENSIONS

EXT_TYPE_MAP = {
    'doc': 'Word文档',
    'docx': 'Word文档',
    'pdf': 'PDF文档',
    'txt': '文本文件',
    'csv': '表格文件',
    'xls': '表格文件',
    'xlsx': '表格文件',
    'ppt': 'PPT演示',
    'pptx': 'PPT演示',
    'jpg': '图片',
    'jpeg': '图片',
    'png': '图片',
    'gif': '图片',
    'bmp': '图片',
    'mp3': '音频',
    'mp4': '视频',
    'zip': '压缩包',
}

def calculate_md5(file_path: str) -> Optional[str]:
    try:
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except Exception:
        return None

def get_unique_filename(target_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    target_path = os.path.join(target_dir, filename)

    if not os.path.exists(target_path):
        return filename

    # 使用日期时间+序号的格式，更简洁且有意义
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    counter = 1
    while True:
        new_filename = f"{base}_{timestamp}_{counter}{ext}"
        new_path = os.path.join(target_dir, new_filename)
        if not os.path.exists(new_path):
            return new_filename
        counter += 1

def normalize_content_for_hash(file_path: str) -> Optional[str]:
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in {'.txt', '.csv', '.doc', '.docx', '.pdf', '.xls', '.xlsx'}:
            with open(file_path, 'rb') as f:
                content = f.read()
            content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            while b'\n\n' in content:
                content = content.replace(b'\n\n', b'\n')
            return hashlib.sha256(content).hexdigest()
        return calculate_md5(file_path)
    except Exception:
        return None

def get_base_name(name: str) -> str:
    name = re.sub(r'[_-]?\d+$', '', name)
    name = re.sub(r'[_-]?(copy|副本|备份|backup)$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', '', name)
    return name.lower()

def get_file_category(ext: str) -> str:
    return EXT_TYPE_MAP.get(ext.lower().lstrip('.'), '其他文件')

def group_similar_files(files: List[Dict]) -> Dict[str, List[Dict]]:
    groups = {}
    used = set()
    
    for i, f1 in enumerate(files):
        if f1['id'] in used:
            continue
        base1 = get_base_name(f1['name'])
        group_key = f1['name'][:10] if len(f1['name']) >= 10 else f1['name']
        group = [f1]
        used.add(f1['id'])
        
        for f2 in files[i+1:]:
            if f2['id'] in used:
                continue
            base2 = get_base_name(f2['name'])
            if base1 == base2 or (len(base1) > 5 and len(base2) > 5 and base1[:15] == base2[:15]):
                if abs(f1['size'] - f2['size']) < 1024 * 100:
                    group.append(f2)
                    used.add(f2['id'])
        
        group.sort(key=lambda x: x.get('modified_time') or datetime.min)
        groups[group_key] = group
    
    return groups

def archive_files_smart(db: Session, file_ids: List[int], target_dir: str, mode: str = 'copy') -> Generator[Dict[str, Any], None, None]:
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
    
    if not entries:
        yield {"type": "error", "message": "没有选择文件"}
        return
    
    yield {"type": "start", "total": len(entries)}
    
    # 创建目标目录和temp中转目录
    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    
    by_category = {}
    for entry in entries:
        cat = get_file_category(entry.extension)
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append({
            'id': entry.id,
            'name': entry.name,
            'path': entry.path,
            'size': entry.size,
            'extension': entry.extension,
            'modified_time': entry.modified_time
        })
    
    for category, files in by_category.items():
        cat_dir = os.path.join(target_dir, category)
        os.makedirs(cat_dir, exist_ok=True)
        yield {"type": "category", "category": category, "count": len(files)}
        
        groups = group_similar_files(files)
        
        for group_key, group_files in groups.items():
            if len(group_files) > 1:
                earliest = group_files[0]
                base_name = get_base_name(earliest['name']) or earliest['name']
                date_str = earliest['modified_time'].strftime("%Y%m%d") if earliest.get('modified_time') else "无日期"
                group_dir = os.path.join(cat_dir, f"{base_name}_{date_str}_类似文件组")
                os.makedirs(group_dir, exist_ok=True)
                
                for f in group_files:
                    try:
                        # 先复制/移动到temp目录作为中转
                        temp_filename = get_unique_filename(temp_dir, f['name'])
                        temp_path = os.path.join(temp_dir, temp_filename)
                        
                        if mode == 'move':
                            shutil.move(f['path'], temp_path)
                        else:
                            shutil.copy2(f['path'], temp_path)
                        
                        # 然后从temp目录移动到最终位置
                        final_filename = get_unique_filename(group_dir, f['name'])
                        final_path = os.path.join(group_dir, final_filename)
                        shutil.move(temp_path, final_path)
                        
                        yield {"type": "archived", "name": f['name'], "target": final_path, "group": group_dir}
                    except Exception as e:
                        yield {"type": "error", "file": f['name'], "message": str(e)}
            else:
                f = group_files[0]
                try:
                    # 先复制/移动到temp目录作为中转
                    temp_filename = get_unique_filename(temp_dir, f['name'])
                    temp_path = os.path.join(temp_dir, temp_filename)
                    
                    if mode == 'move':
                        shutil.move(f['path'], temp_path)
                    else:
                        shutil.copy2(f['path'], temp_path)
                    
                    # 然后从temp目录移动到最终位置
                    final_filename = get_unique_filename(cat_dir, f['name'])
                    final_path = os.path.join(cat_dir, final_filename)
                    shutil.move(temp_path, final_path)
                    
                    yield {"type": "archived", "name": f['name'], "target": final_path}
                except Exception as e:
                    yield {"type": "error", "file": f['name'], "message": str(e)}
        
        for entry in entries:
            if get_file_category(entry.extension) == category:
                # 由于文件名可能已经被修改，需要更新为最终的文件名
                final_filename = get_unique_filename(cat_dir, entry.name)
                entry.path = os.path.join(cat_dir, final_filename)
                entry.name = final_filename
                db.commit()
    
    yield {"type": "complete", "total": len(entries)}

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

    similar_entries = db.query(FileEntry).filter(FileEntry.is_duplicate == False).all()
    for entry in similar_entries:
        similar = db.query(FileEntry).filter(
            FileEntry.id != entry.id,
            FileEntry.size == entry.size,
            FileEntry.extension == entry.extension
        ).all()
        
        base1 = get_base_name(entry.name)
        for sim in similar:
            base2 = get_base_name(sim.name)
            if base1 == base2 and not sim.is_duplicate:
                sim.is_duplicate = True
                sim.duplicate_of_id = entry.id
                db.commit()
                total_duplicates += 1
                total_space_saved += sim.size
                yield {
                    "type": "similar_name",
                    "original_id": entry.id,
                    "original_path": entry.path,
                    "duplicate_id": sim.id,
                    "duplicate_path": sim.path,
                    "size": sim.size,
                    "reason": "name_similar"
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