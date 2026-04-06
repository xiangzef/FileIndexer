from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class ScanRequest(BaseModel):
    paths: List[str]
    extensions: Optional[List[str]] = None

class ScanProgress(BaseModel):
    current_file: str
    scanned_count: int
    total_count: int
    percentage: int

class ArchiveMode(str, Enum):
    COPY = "copy"
    MOVE = "move"

class ArchiveRequest(BaseModel):
    file_ids: List[int]
    target_dir: str
    mode: ArchiveMode = ArchiveMode.COPY

class FileRecord(BaseModel):
    id: int
    path: str
    name: str
    extension: str
    size: int
    md5: Optional[str]
    created_time: Optional[datetime]
    modified_time: Optional[datetime]
    is_duplicate: bool = False
    duplicate_of_id: Optional[int] = None

class FileListResponse(BaseModel):
    total: int
    items: List[FileRecord]
    duplicates_count: int
