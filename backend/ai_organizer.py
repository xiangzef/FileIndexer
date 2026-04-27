import os
import re
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from difflib import SequenceMatcher

class OrganizePromptBuilder:
    """
    AI整理方案的Prompt构建器
    遵循用户的整理规则：
    1. 名字类似内容类似文件大小相近的文件 → 可能是某一次修改了好几次的不同版本，归纳到一个文件夹下
    2. 名称近似后缀不同的归纳到一个文件夹下
    3. 可以将整合成文件夹的不同文件的文件夹按照内容的公约数，近似领域归纳到一个文件夹下
    4. 然后是根据文件类型整理成一个统一的文件夹
    5. 再上一层是不同类型的文件类型文件夹
    """

    VERSION_INDICATORS = [
        'v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8', 'v9', 'v10',
        'V1', 'V2', 'V3', 'V4', 'V5', 'V6', 'V7', 'V8', 'V9', 'V10',
        'version', 'ver', '_v', '_V', '-v', '-V',
        '终稿', '草稿', 'draft', 'final', 'copy', '副本', '备份', 'backup',
        '旧版', '新版', 'new', 'old', 'new_', 'old_',
        '(1)', '(2)', '(3)', '1_', '2_', '3_'
    ]

    STOP_WORDS = {'的', '了', '和', '与', '或', '是', '在', '有', '为', '以', 'the', 'and', 'of', 'in', 'to', 'for', 'with', 'on', 'at', 'by', 'from', 'a', 'an', 'is'}

    def __init__(self, learned_rules: List[Dict] = None):
        self.learned_rules = learned_rules or []

    def build_system_prompt(self) -> str:
        return """你是文件整理专家。根据文件特征生成最优整理方案。

## 整理规则（按优先级）

1. **版本文件合并**：名字类似、内容类似、大小相近的文件→同一文件夹
   - 特征：名称主体相同，有版本后缀（_v1, _v2, 终稿, 草稿, copy等）
   - 命名：`原名称_版本组`

2. **同系列不同后缀**：名称近似但扩展名不同→同一文件夹
   - 例：report.docx, report.pdf → report相关文件夹

3. **内容领域归类**：已分组的文件夹按领域再次归类
   - 例：多个项目文件夹→"项目"文件夹

4. **按文件类型整理**：按扩展名分类
   - 文档：doc,docx,pdf,txt,md,wps | 表格：xls,xlsx,csv,et
   - 图片：jpg,png,gif,bmp,webp,svg | 视频：mp4,avi,mkv,mov
   - 音频：mp3,wav,flac,aac | 压缩：zip,rar,7z,tar
   - 代码：py,js,java,cpp,c,h,html,css | 数据：json,xml,yaml

5. **目录结构**：顶层按类型分类，内层按领域/项目细分

## 输出格式

必须返回JSON：
```json
{"folders":[{"name":"文件夹名","reason":"原因","files":[{"id":1,"name":"文件名"}],"subfolders":[{"name":"子文件夹","reason":"原因","files":[]}]}],"summary":"整理思路"}
```

## 规则
- 每个文件只能在一个文件夹中
- 子文件夹嵌套≤3层
- **输出简洁！文件夹≤10个，每个files数组精简**
- 只返回JSON，不要解释
- 分析所有文件，不遗漏
- 文件>30个：优先按主题/项目分类，次要按类型分类
- **重要要求**：
  1. 文件夹名必须是具体的分类名称，不要使用"文件夹名"等占位符
  2. 文件名必须是实际的文件名，不要使用"文件名"等占位符
  3. 文件ID必须是实际的数字ID，不要使用"文件ID"等占位符
  4. 不要用代码块包裹JSON，直接返回JSON
  5. 确保JSON格式完整，不要截断
  6. **必须生成至少一个文件夹**，不能为空的folders数组
  7. **所有文件都必须被分配到文件夹中**，不能遗漏任何文件"""

    def build_user_prompt(self, files: List[Dict], learned_rules: List[Dict] = None, include_content: bool = False) -> str:
        rules_text = ""
        if learned_rules:
            rules_text = "\n## 历史整理规则\n"
            for rule in learned_rules[-5:]:
                rules_text += f"- {rule['pattern']}: → {rule['action']}\n"

        content_text = ""
        if include_content:
            content_text = "\n## 文件内容摘要\n"
            for f in files[:30]:
                if f.get('text_preview'):
                    content_text += f"\n[{f['name']}]\n{f['text_preview'][:300]}\n"

        file_list = self.format_file_list(files)

        return f"""## 待整理文件

{file_list}
{rules_text}
{content_text}

请根据规则生成JSON整理方案。"""

    def format_file_list(self, files: List[Dict]) -> str:
        lines = []
        for i, f in enumerate(files):
            name = f.get('name', '')
            size = f.get('size', 0)
            ext = f.get('ext', f.get('extension', ''))
            text = f.get('text', '')[:60] if f.get('text', '') else ''

            size_str = self.format_size(size)
            lines.append(f"{i+1}. {name} [{ext}] {size_str}")
            if text:
                lines.append(f"   内容: {text}...")

        return "\n".join(lines)

    def format_size(self, size: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"

    def extract_base_name(self, filename: str) -> str:
        name = os.path.splitext(filename)[0]
        name = name.lower()

        for indicator in self.VERSION_INDICATORS:
            pattern = re.compile(re.escape(indicator), re.IGNORECASE)
            name = pattern.sub('', name)

        name = re.sub(r'[_\-]?\d+$', '', name)
        name = re.sub(r'\s+', '', name)

        return name.strip('_').strip('-')

    def detect_version_group(self, files: List[Dict]) -> Dict[str, List[Dict]]:
        groups = {}
        for f in files:
            base = self.extract_base_name(f['name'])
            if base not in groups:
                groups[base] = []
            groups[base].append(f)
        return {k: v for k, v in groups.items() if len(v) > 1}

    def detect_similar_names(self, files: List[Dict], threshold: float = 0.6) -> List[List[Dict]]:
        groups = []
        used = set()

        for i, f1 in enumerate(files):
            if i in used:
                continue
            similar = [f1]
            name1 = self.extract_base_name(f1['name'])
            if not name1:
                continue

            for j, f2 in enumerate(files[i+1:], i+1):
                if j in used:
                    continue
                name2 = self.extract_base_name(f2['name'])
                if name2 and SequenceMatcher(None, name1, name2).ratio() >= threshold:
                    similar.append(f2)
                    used.add(j)

            if len(similar) > 1:
                groups.append(similar)
                used.add(i)

        return groups

    def detect_same_content(self, files: List[Dict]) -> Dict[str, List[Dict]]:
        groups = {}
        for f in files:
            if f.get('md5'):
                if f['md5'] not in groups:
                    groups[f['md5']] = []
                groups[f['md5']].append(f)
        return {k: v for k, v in groups.items() if len(v) > 1}

    def get_file_type(self, extension: str) -> str:
        ext = extension.lower().lstrip('.')
        type_map = {
            'doc': '文档', 'docx': '文档', 'pdf': '文档', 'txt': '文档', 'md': '文档', 'wps': '文档', 'wpt': '文档',
            'xls': '表格', 'xlsx': '表格', 'csv': '表格', 'xlsb': '表格', 'et': '表格', 'ets': '表格',
            'jpg': '图片', 'jpeg': '图片', 'png': '图片', 'gif': '图片', 'bmp': '图片', 'webp': '图片', 'svg': '图片', 'ico': '图片', 'tiff': '图片',
            'mp4': '视频', 'avi': '视频', 'mkv': '视频', 'mov': '视频', 'wmv': '视频', 'flv': '视频', 'webm': '视频',
            'mp3': '音频', 'wav': '音频', 'flac': '音频', 'aac': '音频', 'ogg': '音频', 'wma': '音频',
            'zip': '压缩', 'rar': '压缩', '7z': '压缩', 'tar': '压缩', 'gz': '压缩',
            'py': '代码', 'js': '代码', 'java': '代码', 'cpp': '代码', 'c': '代码', 'h': '代码', 'cs': '代码', 'go': '代码', 'rs': '代码', 'php': '代码', 'html': '代码', 'css': '代码', 'ts': '代码',
            'json': '数据', 'xml': '数据', 'yaml': '数据', 'yml': '数据', 'toml': '数据', 'ini': '数据', 'cfg': '数据', 'conf': '数据',
            'psd': '设计', 'ai': '设计', 'sketch': '设计', 'xd': '设计', 'fig': '设计',
            'dwg': 'CAD', 'dxf': 'CAD', 'sldprt': 'CAD', 'slddrw': 'CAD',
            'ttf': '字体', 'otf': '字体', 'woff': '字体', 'woff2': '字体',
            'db': '数据库', 'sqlite': '数据库', 'mdb': '数据库',
            'exe': '程序', 'msi': '程序', 'dmg': '程序', 'pkg': '程序',
            'ppt': '演示', 'pptx': '演示', 'pot': '演示', 'potx': '演示',
            'epub': '电子书', 'mobi': '电子书', 'azw': '电子书', 'azw3': '电子书', 'azw4': '电子书', 'kf8': '电子书', 'kfx': '电子书', 'fb2': '电子书', 'cbr': '电子书', 'cbz': '电子书', 'chm': '电子书', 'ibooks': '电子书',
        }
        return type_map.get(ext, '其他')

class LearnedRule:
    def __init__(self, storage_path: str = None):
        self.storage_path = storage_path or os.path.join(os.path.dirname(__file__), 'learned_rules.json')
        self.rules = self.load_rules()

    def load_rules(self) -> List[Dict]:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return []
        return []

    def save_rules(self):
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(self.rules, f, ensure_ascii=False, indent=2)

    def add_rule(self, pattern: str, action: str, file_count: int = 0):
        existing = [r for r in self.rules if r['pattern'] == pattern]
        if existing:
            existing[0]['use_count'] += 1
            existing[0]['last_used'] = datetime.now().isoformat()
        else:
            self.rules.append({
                'pattern': pattern,
                'action': action,
                'use_count': 1,
                'file_count': file_count,
                'created': datetime.now().isoformat(),
                'last_used': datetime.now().isoformat()
            })
        if len(self.rules) > 50:
            self.rules = self.rules[-50:]
        self.save_rules()

    def get_recent_rules(self, limit: int = 10) -> List[Dict]:
        return self.rules[-limit:]
