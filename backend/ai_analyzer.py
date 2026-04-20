import os
import re
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from database import FileEntry
from ai_provider import get_ai_provider

STOP_WORDS = {'的', '了', '和', '与', '或', '是', '在', '有', '为', '以', 'the', 'and', 'of', 'in', 'to', 'for', 'with', 'on', 'at', 'by', 'from', 'a', 'an', 'is'}

PROJECT_INDICATORS = [
    'project', '项目', '作业', 'report', '报告', '论文', 'thesis',
    'design', '设计', 'meeting', '会议', 'meeting', '会议纪要',
    'proposal', '方案', 'plan', '计划', 'task', '任务', 'assignment'
]

class AIAnalyzer:
    def __init__(self, ai_provider=None):
        if ai_provider is None:
            from ai_provider import AIProvider
            self.ai_provider = AIProvider("local")
        elif isinstance(ai_provider, str):
            from ai_provider import AIProvider
            self.ai_provider = AIProvider(ai_provider)
        else:
            self.ai_provider = ai_provider

    def extract_keywords(self, filename: str) -> List[str]:
        """
        提取文件名中的关键词
        """
        name = os.path.splitext(filename)[0].lower()
        words = re.findall(r'[\w]+', name)
        keywords = [w for w in words if w not in STOP_WORDS and len(w) > 1]
        return keywords[:10]

    def detect_project_group(self, filename: str) -> Optional[str]:
        """
        检测项目关联
        """
        name_lower = filename.lower()
        for indicator in PROJECT_INDICATORS:
            if indicator in name_lower:
                return indicator
        return None

    def detect_version_info(self, filename: str) -> Optional[Dict[str, Any]]:
        """
        检测版本信息
        """
        name = os.path.splitext(filename)[0]
        result = {'version': None, 'stage': None}

        version_match = re.search(r'[vV](\d+)', name)
        if version_match:
            result['version'] = int(version_match.group(1))

        if 'final' in name.lower() or '终稿' in name:
            result['stage'] = 'final'
        elif 'draft' in name.lower() or '草稿' in name or 'draft' in name.lower():
            result['stage'] = 'draft'
        elif 'v' in name.lower() and result.get('version'):
            result['stage'] = 'version'

        return result if result['version'] or result['stage'] else None

    def analyze_file_name(self, file_name: str) -> Dict[str, Any]:
        """
        分析文件名，提取关键信息
        """
        result = {
            "original_name": file_name,
            "keywords": [],
            "project": None,
            "version_info": None
        }

        result["keywords"] = self.extract_keywords(file_name)
        result["project"] = self.detect_project_group(file_name)
        result["version_info"] = self.detect_version_info(file_name)

        return result

    def analyze_file_content(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        分析文件内容，提取关键信息
        """
        try:
            ext = os.path.splitext(file_path)[1].lower()
            result = {
                "content_type": ext,
                "text_content": "",
                "keywords": [],
                "summary": ""
            }

            if ext in ['.txt', '.csv', '.md', '.log', '.py', '.js', '.java', '.cpp', '.c', '.h', '.css', '.html', '.xml']:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    result["text_content"] = f.read()[:5000]

            if result["text_content"]:
                words = re.findall(r'\b\w+\b', result["text_content"])
                keywords = [word.lower() for word in words if len(word) > 3 and word.lower() not in STOP_WORDS]
                word_freq = {}
                for word in keywords:
                    word_freq[word] = word_freq.get(word, 0) + 1
                sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]
                result["keywords"] = [word for word, _ in sorted_words]

                if len(result["text_content"]) > 200:
                    result["summary"] = result["text_content"][:200] + "..."
                else:
                    result["summary"] = result["text_content"]

            return result
        except Exception:
            return None

    def group_files_by_semantic(self, files: List[FileEntry]) -> Dict[str, List[FileEntry]]:
        """
        根据语义对文件进行分组
        优先级：项目组 > 版本组 > 关键词组
        """
        groups = {
            'projects': {},
            'versions': {},
            'by_keyword': {},
            'by_type': {}
        }

        for file in files:
            name_analysis = self.analyze_file_name(file.name)

            if name_analysis.get("project"):
                group_key = name_analysis["project"]
                if group_key not in groups['projects']:
                    groups['projects'][group_key] = []
                groups['projects'][group_key].append(file)

            elif name_analysis.get("version_info"):
                vi = name_analysis["version_info"]
                if vi.get("version"):
                    base_name = os.path.splitext(file.name)[0]
                    base_name = re.sub(r'[vV]\d+.*', '', base_name)
                    base_name = base_name.strip('_').strip('-')
                    group_key = f"{base_name}_v{vi['version']}" if base_name else f"v{vi['version']}"
                elif vi.get("stage"):
                    stage_name = {'final': '终稿', 'draft': '草稿'}.get(vi['stage'], vi['stage'])
                    group_key = f"{stage_name}组"
                else:
                    group_key = "版本组"

                if group_key not in groups['versions']:
                    groups['versions'][group_key] = []
                groups['versions'][group_key].append(file)

            else:
                keywords = name_analysis.get("keywords", [])
                if keywords:
                    group_key = keywords[0]
                else:
                    group_key = file.extension.lstrip('.') or "未分类"

                if group_key not in groups['by_keyword']:
                    groups['by_keyword'][group_key] = []
                groups['by_keyword'][group_key].append(file)

        merged_groups = {}
        for group_type, group_data in groups.items():
            for group_name, group_files in group_data.items():
                if group_files:
                    merged_groups[group_name] = group_files

        return merged_groups

    def suggest_folder_name(self, files: List[FileEntry]) -> str:
        """
        智能建议文件夹名称
        """
        if not files:
            return "未分类"

        analyses = [self.analyze_file_name(f.name) for f in files]

        projects = [a["project"] for a in analyses if a.get("project")]
        if projects:
            project_count = {}
            for p in projects:
                project_count[p] = project_count.get(p, 0) + 1
            most_common_project = max(project_count.items(), key=lambda x: x[1])[0]
            return f"{most_common_project}项目"

        keywords = []
        for a in analyses:
            keywords.extend(a.get("keywords", []))

        if keywords:
            keyword_freq = {}
            for kw in keywords:
                keyword_freq[kw] = keyword_freq.get(kw, 0) + 1
            top_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:2]
            return "_".join([kw for kw, _ in top_keywords])

        types = [f.extension.lstrip('.') for f in files if f.extension]
        if types:
            type_count = {}
            for t in types:
                type_count[t] = type_count.get(t, 0) + 1
            most_common_type = max(type_count.items(), key=lambda x: x[1])[0]
            return f"{most_common_type}文件"

        return "未分类"

    def generate_summary(self, files: List[FileEntry]) -> str:
        """
        为一组文件生成摘要
        """
        if not files:
            return "无文件"

        analyses = []
        content_texts = []

        for file in files:
            name_analysis = self.analyze_file_name(file.name)
            content_analysis = self.analyze_file_content(file.path)
            analyses.append({
                "name": file.name,
                "name_analysis": name_analysis,
                "content_analysis": content_analysis
            })

            if content_analysis and content_analysis.get("text_content"):
                content_texts.append(content_analysis["text_content"])

        summary = f"共 {len(files)} 个文件\n"

        type_count = {}
        for file in files:
            ext = file.extension.lstrip('.')
            type_count[ext] = type_count.get(ext, 0) + 1
        summary += "文件类型："
        summary += ", ".join([f"{ext}: {count}个" for ext, count in type_count.items()])
        summary += "\n"

        projects = [a["name_analysis"].get("project") for a in analyses if a["name_analysis"].get("project")]
        if projects:
            summary += f"检测到项目关联：{', '.join(set(projects))}\n"

        versions = [a["name_analysis"].get("version_info") for a in analyses if a["name_analysis"].get("version_info")]
        if versions:
            summary += "检测到版本信息\n"

        all_keywords = []
        for analysis in analyses:
            all_keywords.extend(analysis["name_analysis"].get("keywords", []))
            if analysis["content_analysis"] and analysis["content_analysis"].get("keywords"):
                all_keywords.extend(analysis["content_analysis"]["keywords"])

        if all_keywords:
            keyword_freq = {}
            for keyword in all_keywords:
                keyword_freq[keyword] = keyword_freq.get(keyword, 0) + 1
            sorted_keywords = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:5]
            summary += "关键词：" + ", ".join([keyword for keyword, _ in sorted_keywords])

        return summary

def analyze_files(db: Session, file_ids: List[int], ai_provider=None) -> Dict[str, Any]:
    """
    分析指定文件
    """
    analyzer = AIAnalyzer(ai_provider)
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    if not entries:
        return {"error": "没有选择文件"}

    analyses = []
    for entry in entries:
        name_analysis = analyzer.analyze_file_name(entry.name)
        content_analysis = analyzer.analyze_file_content(entry.path)

        analyses.append({
            "id": entry.id,
            "name": entry.name,
            "path": entry.path,
            "name_analysis": name_analysis,
            "content_analysis": content_analysis
        })

    groups = analyzer.group_files_by_semantic(entries)
    folder_name = analyzer.suggest_folder_name(entries)
    summary = analyzer.generate_summary(entries)

    return {
        "total_files": len(entries),
        "analyses": analyses,
        "groups": {k: [e.id for e in v] for k, v in groups.items()},
        "suggested_folder_name": folder_name,
        "summary": summary
    }

def ai_archive_files(db: Session, file_ids: List[int], target_dir: str, mode: str = 'copy', ai_provider=None) -> List[Dict[str, Any]]:
    """
    使用AI分析结果归档文件
    """
    analyzer = AIAnalyzer(ai_provider)
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    if not entries:
        return [{"error": "没有选择文件"}]

    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    groups = analyzer.group_files_by_semantic(entries)

    results = []
    path_updates = []

    for group_name, group_files in groups.items():
        safe_group_name = re.sub(r'[<>:"/\\|?*]', '_', group_name)
        group_dir = os.path.join(target_dir, safe_group_name)
        os.makedirs(group_dir, exist_ok=True)

        for file in group_files:
            try:
                temp_filename = f"{os.path.splitext(file.name)[0]}_temp{os.path.splitext(file.name)[1]}"
                temp_path = os.path.join(temp_dir, temp_filename)

                if mode == 'move':
                    import shutil
                    shutil.move(file.path, temp_path)
                else:
                    import shutil
                    shutil.copy2(file.path, temp_path)

                final_filename = file.name
                final_path = os.path.join(group_dir, final_filename)

                if os.path.exists(final_path):
                    base, ext = os.path.splitext(final_filename)
                    counter = 1
                    while os.path.exists(final_path):
                        final_filename = f"{base}_{counter:02d}{ext}"
                        final_path = os.path.join(group_dir, final_filename)
                        counter += 1

                import shutil
                shutil.move(temp_path, final_path)

                path_updates.append((file, final_path, final_filename, group_name))

                results.append({
                    "id": file.id,
                    "name": file.name,
                    "group": group_name,
                    "new_path": final_path,
                    "success": True
                })
            except Exception as e:
                results.append({
                    "id": file.id,
                    "name": file.name,
                    "error": str(e),
                    "success": False
                })

    for file, new_path, new_name, group_name in path_updates:
        file.path = new_path
        file.name = new_name

    db.commit()

    try:
        if os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    return results