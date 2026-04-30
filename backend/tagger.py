import os
import re
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

class TagPromptBuilder:
    """标签生成的Prompt构建器 - 专为政府文件优化"""

    TOPIC_TAGS = [
        '党建', '工会', '财务', '人事', '项目', '活动', '会议', '制度', '计划', '总结', '报告', '通知',
        '方案', '规划', '预算', '招标', '合同', '协议', '纪要', '讲话', '发言', '汇报', '致辞', '发言稿',
        '新闻', '宣传', '调研', '考察', '培训', '方案', '要点', '安排', '措施', '办法', '细则', '规程'
    ]

    SCENE_TAGS = [
        '请示', '批复', '函', '通知', '决定', '决议', '命令', '公报', '公告', '通告', '议案', '条例',
        '规定', '办法', '细则', '规范', '标准', '指南', '手册', '清单', '目录', '台账', '预案'
    ]

    DOMAIN_TAGS = [
        '教育', '卫生', '交通', '建设', '民政', '财政', '发改', '经贸', '农业', '林业', '水利', '国土',
        '环保', '旅游', '文化', '体育', '科技', '司法', '审计', '信访', '应急', '气象', '地震', '人社',
        '商务', '国资', '市场监管', '行政审批', '数据局', '城运中心', '大数据中心'
    ]

    FILE_TYPE_TAGS = [
        'Word文档', 'Excel表格', 'PPT演示', 'PDF文档', '图片', '压缩包', '音视频', '文本'
    ]

    def build_system_prompt(self, topic_only: bool = False) -> str:
        if topic_only:
            # 优化版：领域标签已从路径推理，AI只需推断主题和场景
            return """你是政府文件标签专家。根据文件名和内容，为文件生成3-6个精准标签。

## 标签体系（简化版）

**主题标签**（文件核心主题）：
""" + "、".join(self.TOPIC_TAGS[:20]) + """

**场景标签**（公文文种）：
""" + "、".join(self.SCENE_TAGS) + """

**注意**：领域标签已通过路径分析获取，无需重复推断。

## 分析维度
1. **文件名分析**：提取核心关键词判断主题和场景
2. **内容分析**（如有）：看开头几句判断文档类型

## 输出格式（严格JSON，只返回标签和摘要，不要解释）
```json
{"tags":[{"name":"党建","category":"主题","confidence":0.9},{"name":"通知","category":"场景","confidence":0.9}],"summary":"关于开展党史学习教育的通知"}
```

## 规则
1. 必须输出有效JSON，不要任何解释文字
2. 标签优先从上述列表中选择
3. confidence 0.5-1.0之间
4. 至少3个标签，最多6个
5. summary 10-20字"""

        return """你是政府文件标签专家。根据文件名、路径和内容，为文件生成3-8个精准标签。

## 政府文件标签体系

**主题标签**（文件核心主题，从中选择最匹配的）：
""" + "、".join(self.TOPIC_TAGS[:20]) + """

**场景标签**（公文文种，优先匹配）：
""" + "、".join(self.SCENE_TAGS) + """

**领域标签**（所属行业/部门，从中选择匹配的）：
""" + "、".join(self.DOMAIN_TAGS) + """

**类型标签**（文件格式）：
Word文档、Excel表格、PPT演示、PDF文档、图片、压缩包、音视频、文本

## 分析维度

1. **文件名分析**：提取核心关键词，判断文件主题
   - 含"通知""请示""报告"→ 场景标签
   - 含"方案""计划""要点"→ 主题标签
   - 含"党建""工会""项目"→ 领域/主题标签

2. **路径分析**：从完整路径提取有用信息
   - 路径中包含的目录名往往反映文件所属领域或部门
   - 如：D:\\党建\\2024\\通知.docx → 领域=党建

3. **内容分析**（如有）：
   - 看开头几句判断文档类型
   - 抓取核心业务词汇

## 输出格式（严格JSON）

```json
{
  "tags": [
    {"name": "党建", "category": "领域", "confidence": 0.95},
    {"name": "通知", "category": "场景", "confidence": 0.90},
    {"name": "Word文档", "category": "类型", "confidence": 1.0}
  ],
  "summary": "关于开展党史学习教育的通知，用于部署党建工作"
}
```

## 规则
1. **必须输出有效JSON**，不要任何解释文字
2. 标签优先从上述列表中选择，列表中没有的可自行添加
3. confidence表示置信度，0.5-1.0之间
4. **至少3个标签，最多8个**
5. summary要用一句话描述文件核心内容，10-30字
6. 如果是政府文件，优先使用政府文件常用语"""

    def build_user_prompt(self, file_name: str, file_path: str, content_preview: str = None) -> str:
        dir_name = os.path.dirname(file_path)
        info = f"""## 待分析文件

**文件名**: {file_name}

**完整路径**: {file_path}

**所在目录**: {dir_name}"""

        if content_preview:
            info += f"\n\n**内容预览（前200字）**:\n{content_preview[:200]}"

        info += "\n\n请根据以上信息分析文件主题、场景、领域和类型，输出JSON格式的标签结果。"
        return info


class DomainTagGenerator:
    """领域标签生成器 - 用于根据文件夹路径自动推断领域"""

    DOMAIN_PATTERNS = {
        '党建': ['党建', '党委', '支部', '党组织', '党务', '党史', '主题教育', '思想政治'],
        '工会': ['工会', '职工', '工会', '福利', '活动', '文体'],
        '财务': ['财务', '会计', '预算', '决算', '资金', '经费', '报销', '账务'],
        '人事': ['人事', '干部', '人才', '招聘', '绩效', '考核', '工资', '社保'],
        '项目': ['项目', '建设', '工程', '招标', '采购', '实施'],
        '办公室': ['办公室', '综合', '协调', '后勤', '保密', '档案'],
        '发改': ['发改', '规划', '计划', '立项', '审批', '投资'],
        '经信': ['经信', '工业', '信息化', '企业', '民营'],
        '教育': ['教育', '学校', '培训', '教学', '师资'],
        '卫生': ['卫生', '医疗', '健康', '疾控', '卫健'],
        '交通': ['交通', '运输', '公路', '物流', '运管'],
        '建设': ['建设', '城建', '房地产', '住房', '园林'],
        '民政': ['民政', '社会', '救助', '养老', '社区', '基层治理'],
        '文旅': ['文旅', '旅游', '文化', '文物', '体育', '广电'],
        '数据局': ['数据', '信息化', '智慧城市', '数字经济', '算力', '数据要素'],
    }

    @classmethod
    def infer_from_path(cls, file_path: str) -> List[Dict[str, Any]]:
        """从文件路径推断可能的领域标签"""
        path_lower = file_path.lower()
        results = []

        for domain, keywords in cls.DOMAIN_PATTERNS.items():
            for keyword in keywords:
                if keyword.lower() in path_lower:
                    results.append({
                        'name': domain,
                        'category': '领域',
                        'confidence': 0.8,
                        'source': 'path'
                    })
                    break

        return results


class AITagger:
    """AI标签生成器 - 结合路径推断和AI分析"""

    CONTENT_EXTENSIONS = {'.txt', '.csv', '.md', '.log', '.py', '.js', '.java', '.cpp',
                          '.c', '.h', '.css', '.html', '.xml', '.json', '.wps', '.wpt',
                          '.doc', '.docx', '.pdf'}

    def __init__(self, ai_provider):
        self.ai_provider = ai_provider
        self.prompt_builder = TagPromptBuilder()
        self.domain_generator = DomainTagGenerator()

    def get_content_preview(self, file_path: str, max_chars: int = 200) -> Optional[str]:
        """获取文件内容预览"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.CONTENT_EXTENSIONS:
            return None

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read(max_chars)
        except Exception:
            return None

    def _add_file_type_tag(self, file_name: str, tags: List[Dict]) -> None:
        """根据文件扩展名添加类型标签"""
        ext = os.path.splitext(file_name)[1].lower()
        type_map = {
            '.doc': ('Word文档', '类型', 1.0),
            '.docx': ('Word文档', '类型', 1.0),
            '.xls': ('Excel表格', '类型', 1.0),
            '.xlsx': ('Excel表格', '类型', 1.0),
            '.csv': ('Excel表格', '类型', 0.9),
            '.ppt': ('PPT演示', '类型', 1.0),
            '.pptx': ('PPT演示', '类型', 1.0),
            '.pdf': ('PDF文档', '类型', 1.0),
            '.jpg': ('图片', '类型', 1.0),
            '.jpeg': ('图片', '类型', 1.0),
            '.png': ('图片', '类型', 1.0),
            '.gif': ('图片', '类型', 1.0),
            '.zip': ('压缩包', '类型', 1.0),
            '.rar': ('压缩包', '类型', 1.0),
            '.7z': ('压缩包', '类型', 1.0),
            '.mp3': ('音视频', '类型', 1.0),
            '.mp4': ('音视频', '类型', 1.0),
            '.wav': ('音视频', '类型', 1.0),
            '.txt': ('文本', '类型', 0.8),
            '.md': ('文本', '类型', 0.8),
        }
        if ext in type_map:
            name, category, confidence = type_map[ext]
            if not any(t['name'] == name for t in tags):
                tags.append({'name': name, 'category': category, 'confidence': confidence})

    def generate_tags(self, file_name: str, file_path: str,
                     content_preview: str = None) -> Dict[str, Any]:
        """
        为单个文件生成标签
        优化策略：路径推理优先（省GPU算力），AI只补充主题/场景标签
        返回: {"tags": [{"name": "党建", "category": "主题", "confidence": 0.95}, ...], "summary": "..."}
        """
        # 路径推理：领域标签直接从路径判断，不消耗GPU
        path_tags = self.domain_generator.infer_from_path(file_path)

        # 内容预览：减少到200字，降低token处理量
        if content_preview is None:
            content_preview = self.get_content_preview(file_path, max_chars=200)

        # 只用AI推断主题和场景标签（领域标签已从路径获取）
        system_prompt = self.prompt_builder.build_system_prompt(topic_only=True)
        user_prompt = self.prompt_builder.build_user_prompt(file_name, file_path, content_preview)

        response = self.ai_provider.chat(system_prompt, user_prompt)

        if response.startswith("错误:"):
            return {"tags": [], "summary": "", "error": response}

        result = None
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            json_str = self._extract_json(response)
            if json_str:
                try:
                    result = json.loads(json_str)
                except:
                    pass

        if result is None:
            return {"tags": [], "summary": "", "error": "JSON解析失败"}

        tags = result.get('tags', [])

        for pt in path_tags:
            if not any(t.get('name') == pt['name'] for t in tags):
                tags.insert(0, pt)

        self._add_file_type_tag(file_name, tags)

        result['tags'] = tags[:8]

        return result

    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取JSON"""
        text = text.strip()

        if text.startswith('{'):
            depth = 0
            start = 0
            for i, ch in enumerate(text):
                if ch == '{':
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return text[start:i+1]
        return None

    def batch_generate_tags(self, files: List[Dict],
                           progress_callback=None) -> List[Dict]:
        """
        批量生成标签
        files: [{"id": 1, "name": "文件名", "path": "/path/to/file"}, ...]
        返回: [{"file_id": 1, "tags": [...], "summary": "..."}, ...]
        """
        results = []
        total = len(files)

        for idx, f in enumerate(files):
            file_id = f.get('id')
            file_name = f.get('name', '')
            file_path = f.get('path', '')

            content_preview = f.get('content_preview')
            if content_preview is None and file_path:
                content_preview = self.get_content_preview(file_path)

            result = self.generate_tags(file_name, file_path, content_preview)
            result['file_id'] = file_id
            results.append(result)

            if progress_callback:
                progress_callback({
                    'type': 'progress',
                    'current': idx + 1,
                    'total': total,
                    'file_name': file_name,
                    'percentage': int((idx + 1) * 100 / total)
                })

        return results


def save_tags_to_db(db, file_id: int, tags_data: Dict[str, Any], source: str = 'ai'):
    """将标签保存到数据库"""
    from database import FileEntry, Tag, FileTag

    file_entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if not file_entry:
        return False

    for tag_info in tags_data.get('tags', []):
        tag_name = tag_info.get('name', '').strip()
        if not tag_name:
            continue

        category = tag_info.get('category', '其他')
        confidence = tag_info.get('confidence', 1.0)

        existing_tag = db.query(Tag).filter(Tag.name == tag_name).first()
        if not existing_tag:
            existing_tag = Tag(name=tag_name, category=category, usage_count=0)
            db.add(existing_tag)
            db.flush()

        existing_tag.usage_count += 1

        existing_file_tag = db.query(FileTag).filter(
            FileTag.file_id == file_id,
            FileTag.tag_id == existing_tag.id
        ).first()

        if not existing_file_tag:
            file_tag = FileTag(
                file_id=file_id,
                tag_id=existing_tag.id,
                confidence=confidence,
                source=source
            )
            db.add(file_tag)

    summary = tags_data.get('summary', '')
    if summary:
        file_entry.content_summary = summary

    file_entry.tag_status = 'ready'
    db.commit()
    return True


def remove_tags_from_db(db, file_id: int, tag_ids: List[int] = None):
    """从数据库删除文件的标签"""
    from database import FileEntry, Tag, FileTag

    query = db.query(FileTag).filter(FileTag.file_id == file_id)

    if tag_ids:
        query = query.filter(FileTag.tag_id.in_(tag_ids))

    file_tags = query.all()

    for ft in file_tags:
        tag = ft.tag
        if tag and tag.usage_count > 0:
            tag.usage_count -= 1

    query.delete()

    remaining = db.query(FileTag).filter(FileTag.file_id == file_id).count()
    if remaining == 0:
        file_entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
        if file_entry:
            file_entry.tag_status = 'pending'

    db.commit()
    return len(file_tags)
