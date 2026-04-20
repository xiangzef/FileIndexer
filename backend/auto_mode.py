import os
import re
import requests
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from database import FileEntry

class AutoModeDetector:
    def __init__(self):
        self.file_type_mapping = {
            '.doc': 'rule', '.docx': 'rule', '.pdf': 'rule', '.txt': 'rule', '.md': 'rule',
            '.csv': 'rule', '.xls': 'rule', '.xlsx': 'rule',
            '.jpg': 'general', '.jpeg': 'general', '.png': 'general', '.gif': 'general',
            '.zip': 'general', '.rar': 'general', '.exe': 'general', '.dll': 'general'
        }

        self.keyword_mapping = {
            'report': 'rule', 'analysis': 'rule', 'summary': 'rule', 'plan': 'rule',
            'project': 'rule', 'document': 'rule', 'note': 'rule', 'diary': 'rule',
            'log': 'rule', '论文': 'rule', '报告': 'rule', '方案': 'rule', '项目': 'rule'
        }

    def check_local_ai_available(self) -> bool:
        try:
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False

    def check_cloud_ai_available(self, api_key: Optional[str] = None) -> bool:
        if not api_key:
            return False
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = requests.get(
                "https://api.openai.com/v1/models",
                headers=headers,
                timeout=5
            )
            return response.status_code == 200
        except:
            return False

    def get_available_ai_mode(self, api_key: Optional[str] = None) -> str:
        if self.check_local_ai_available():
            return "ai_local"
        if self.check_cloud_ai_available(api_key):
            return "ai_cloud"
        return "rule"

    def detect_mode(self, file: FileEntry, force_mode: Optional[str] = None) -> str:
        if force_mode:
            return force_mode

        ext = file.extension.lower()
        mode = self.file_type_mapping.get(ext, 'general')

        if mode == 'rule' and self._has_keywords(file.name):
            return 'rule'
        if mode == 'rule' and file.size < 10 * 1024 * 1024:
            return 'rule'

        return 'general'

    def _has_keywords(self, text: str) -> bool:
        text_lower = text.lower()
        for keyword in self.keyword_mapping:
            if keyword in text_lower:
                return True
        return False

    def detect_batch_mode(self, files: List[FileEntry], force_mode: Optional[str] = None) -> Dict[str, List[FileEntry]]:
        result = {"rule": [], "ai": [], "general": []}

        for file in files:
            mode = self.detect_mode(file, force_mode)
            if mode == "rule":
                result["rule"].append(file)
            elif mode == "ai":
                result["ai"].append(file)
            else:
                result["general"].append(file)

        return result

def auto_detect_mode(db: Session, file_ids: List[int], api_key: Optional[str] = None) -> Dict[str, Any]:
    detector = AutoModeDetector()
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    if not entries:
        return {"ai_mode": "rule", "ai_files": [], "general_files": []}

    available_ai = detector.get_available_ai_mode(api_key)

    mode_result = detector.detect_batch_mode(entries)

    return {
        "ai_mode": available_ai,
        "ai_files": [e.id for e in mode_result["rule"] + mode_result["ai"]],
        "general_files": [e.id for e in mode_result["general"]],
        "rule_files": [e.id for e in mode_result["rule"]],
        "local_available": detector.check_local_ai_available(),
        "cloud_available": detector.check_cloud_ai_available(api_key)
    }