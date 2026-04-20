import os
import re
import json
import shutil
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from database import FileEntry
from ai_provider import get_ai_provider

STOP_WORDS = {'的', '了', '和', '与', '或', '是', '在', '有', '为', '以', 'the', 'and', 'of', 'in', 'to', 'for', 'with', 'on', 'at', 'by', 'from', 'a', 'an', 'is'}

AI_EXT_TYPE_MAP = {
    'doc': 'Word文档', 'docx': 'Word文档',
    'pdf': 'PDF文档',
    'txt': '文本文件', 'md': 'Markdown文档', 'log': '日志文件',
    'csv': '表格文件', 'xls': '表格文件', 'xlsx': '表格文件', 'xlsb': '表格文件',
    'ppt': 'PPT演示', 'pptx': 'PPT演示', 'pptm': 'PPT演示',
    'jpg': '图片', 'jpeg': '图片', 'png': '图片', 'gif': '图片', 'bmp': '图片', 'ico': '图片', 'webp': '图片', 'svg': '图片', 'tiff': '图片',
    'mp3': '音频', 'wav': '音频', 'flac': '音频', 'aac': '音频', 'ogg': '音频', 'wma': '音频',
    'mp4': '视频', 'avi': '视频', 'mkv': '视频', 'mov': '视频', 'wmv': '视频', 'flv': '视频', 'webm': '视频',
    'zip': '压缩包', 'rar': '压缩包', '7z': '压缩包', 'tar': '压缩包', 'gz': '压缩包',
    'exe': '程序文件', 'msi': '安装程序', 'dmg': '安装程序', 'pkg': '安装程序',
    'html': '网页文件', 'htm': '网页文件', 'css': '网页文件', 'js': '脚本文件', 'ts': '脚本文件',
    'py': 'Python代码', 'java': 'Java代码', 'cpp': 'C++代码', 'c': 'C代码', 'h': '头文件', 'cs': 'C#代码', 'go': 'Go代码', 'rs': 'Rust代码', 'php': 'PHP代码',
    'json': '数据文件', 'xml': '数据文件', 'yaml': '数据文件', 'yml': '数据文件', 'toml': '数据文件', 'ini': '配置文件', 'cfg': '配置文件', 'conf': '配置文件',
    'psd': '设计文件', 'ai': '设计文件', 'sketch': '设计文件', 'xd': '设计文件', 'fig': '设计文件',
    'dwg': 'CAD图纸', 'dxf': 'CAD图纸', 'sldprt': 'CAD文件', 'slddrw': 'CAD文件',
    'ttf': '字体文件', 'otf': '字体文件', 'woff': '字体文件', 'woff2': '字体文件',
    'db': '数据库文件', 'sqlite': '数据库文件', 'mdb': '数据库文件',
}

def get_file_category(ext: str) -> str:
    return AI_EXT_TYPE_MAP.get(ext.lower().lstrip('.'), '其他文件')

def get_base_name(name: str) -> str:
    name = re.sub(r'[_-]?\d+$', '', name)
    name = re.sub(r'[_-]?(copy|副本|备份|backup)$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', '', name)
    return name.lower()

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
    使用AI按文件类型分类归档文件
    """
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    if not entries:
        return [{"error": "没有选择文件"}]

    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    by_category = {}
    for entry in entries:
        cat = get_file_category(entry.extension)
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(entry)

    results = []
    path_updates = []

    for category, cat_files in by_category.items():
        cat_dir = os.path.join(target_dir, category)
        os.makedirs(cat_dir, exist_ok=True)

        similar_groups = {}
        for f in cat_files:
            base = get_base_name(f.name)
            if base not in similar_groups:
                similar_groups[base] = []
            similar_groups[base].append(f)

        for base_name, group_files in similar_groups.items():
            for i, file in enumerate(group_files):
                try:
                    if i == 0:
                        final_filename = file.name
                    else:
                        ext = os.path.splitext(file.name)[1]
                        final_filename = f"{base_name}_{i+1:02d}{ext}"

                    final_path = os.path.join(cat_dir, final_filename)
                    if os.path.exists(final_path):
                        ext = os.path.splitext(file.name)[1]
                        counter = 1
                        while os.path.exists(final_path):
                            final_filename = f"{base_name}_{counter:02d}{ext}"
                            final_path = os.path.join(cat_dir, final_filename)
                            counter += 1

                    temp_filename = f"{os.path.splitext(file.name)[0]}_temp{os.path.splitext(file.name)[1]}"
                    temp_path = os.path.join(temp_dir, temp_filename)

                    if mode == 'move':
                        shutil.move(file.path, temp_path)
                    else:
                        shutil.copy2(file.path, temp_path)

                    shutil.move(temp_path, final_path)

                    path_updates.append((file, final_path, final_filename, category))

                    results.append({
                        "id": file.id,
                        "name": file.name,
                        "category": category,
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

    for file, new_path, new_name, category in path_updates:
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