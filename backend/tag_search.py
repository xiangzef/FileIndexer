import json
import re
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from database import FileEntry, Tag, FileTag


class TagSearchEngine:
    """标签与语义搜索引擎"""

    def __init__(self, db: Session):
        self.db = db

    def search_by_tags(self, tag_names: List[str],
                       file_ids: List[int] = None,
                       match_mode: str = 'all') -> List[int]:
        """
        精确标签搜索
        tag_names: 要匹配的标签名列表
        match_mode: 'all' 全部匹配, 'any' 任意匹配
        """
        if not tag_names:
            return []

        query = self.db.query(FileEntry.id).join(FileTag).join(Tag).filter(
            Tag.name.in_(tag_names)
        )

        if file_ids:
            query = query.filter(FileEntry.id.in_(file_ids))

        results = query.all()
        file_id_counts = {}

        for (file_id,) in results:
            if match_mode == 'any':
                return [r[0] for r in results]
            else:
                file_id_counts[file_id] = file_id_counts.get(file_id, 0) + 1

        if match_mode == 'all':
            matched_ids = [fid for fid, count in file_id_counts.items()
                         if count == len(tag_names)]
            return matched_ids

        return list(file_id_counts.keys())

    def search_by_keywords(self, keyword: str,
                          file_ids: List[int] = None) -> List[int]:
        """
        关键词搜索（文件名、路径、内容摘要）
        """
        pattern = f"%{keyword}%"
        query = self.db.query(FileEntry.id).filter(
            (FileEntry.name.like(pattern)) |
            (FileEntry.path.like(pattern)) |
            (FileEntry.content_summary.like(pattern))
        )

        if file_ids:
            query = query.filter(FileEntry.id.in_(file_ids))

        return [row[0] for row in query.all()]

    def search_by_file_ids(self, file_ids: List[int]) -> List[int]:
        """直接按文件ID列表搜索"""
        if not file_ids:
            return []

        files = self.db.query(FileEntry.id).filter(
            FileEntry.id.in_(file_ids),
            FileEntry.tag_status == 'ready'
        ).all()

        return [f[0] for f in files]

    def hybrid_search(self, query_text: str,
                     required_tags: List[str] = None,
                     exclude_tags: List[str] = None,
                     file_ids: List[int] = None,
                     top_k: int = 50) -> List[Dict[str, Any]]:
        """
        混合搜索：标签 + 关键词 + Embedding
        返回文件列表及匹配信息
        """
        all_matched_ids = set()
        file_scores = {}

        if required_tags:
            tag_matches = self.search_by_tags(required_tags, file_ids)
            for fid in tag_matches:
                file_scores[fid] = file_scores.get(fid, 0) + 10

        if exclude_tags:
            exclude_matches = set(self.search_by_tags(exclude_tags, file_ids))
            file_ids = [fid for fid in (file_ids or []) if fid not in exclude_matches]

        keywords = self._extract_keywords(query_text)
        for kw in keywords:
            kw_matches = self.search_by_keywords(kw, file_ids)
            for fid in kw_matches:
                file_scores[fid] = file_scores.get(fid, 0) + 5

        for fid in file_scores:
            all_matched_ids.add(fid)

        if file_ids:
            untagged = set(file_ids) - all_matched_ids
        else:
            untagged = set()

        results = []
        for fid in all_matched_ids:
            tags = self.get_file_tags(fid)
            file_entry = self.db.query(FileEntry).filter(FileEntry.id == fid).first()

            results.append({
                'file_id': fid,
                'score': file_scores.get(fid, 0),
                'tags': tags,
                'file': {
                    'id': file_entry.id,
                    'name': file_entry.name,
                    'path': file_entry.path,
                    'extension': file_entry.extension,
                    'size': file_entry.size,
                    'content_summary': file_entry.content_summary
                } if file_entry else None
            })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]

    def semantic_search(self, query_embedding: List[float],
                       file_ids: List[int] = None,
                       top_k: int = 20) -> List[Dict[str, Any]]:
        """
        基于Embedding向量的语义搜索
        """
        from embedder import cosine_similarity

        query = self.db.query(FileEntry).filter(
            FileEntry.embedding_vector.isnot(None),
            FileEntry.tag_status == 'ready'
        )

        if file_ids:
            query = query.filter(FileEntry.id.in_(file_ids))

        files = query.all()
        results = []

        for f in files:
            try:
                file_embedding = json.loads(f.embedding_vector)
                score = cosine_similarity(query_embedding, file_embedding)
                if score > 0.3:
                    results.append({
                        'file_id': f.id,
                        'score': score,
                        'file': {
                            'id': f.id,
                            'name': f.name,
                            'path': f.path,
                            'extension': f.extension,
                            'size': f.size,
                            'content_summary': f.content_summary
                        }
                    })
            except:
                continue

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]

    def get_file_tags(self, file_id: int) -> List[Dict[str, Any]]:
        """获取文件的所有标签"""
        file_tags = self.db.query(FileTag).filter(FileTag.file_id == file_id).all()
        tags = []

        for ft in file_tags:
            if ft.tag:
                tags.append({
                    'id': ft.tag.id,
                    'name': ft.tag.name,
                    'category': ft.tag.category,
                    'confidence': ft.confidence,
                    'source': ft.source
                })

        return tags

    def get_all_tags(self, category: str = None,
                    min_usage: int = 0) -> List[Dict[str, Any]]:
        """获取所有标签"""
        query = self.db.query(Tag)

        if category:
            query = query.filter(Tag.category == category)

        if min_usage > 0:
            query = query.filter(Tag.usage_count >= min_usage)

        tags = query.order_by(Tag.usage_count.desc()).all()

        return [{'id': t.id, 'name': t.name, 'category': t.category,
                'usage_count': t.usage_count} for t in tags]

    def suggest_tags(self, text: str, top_k: int = 10) -> List[str]:
        """基于文本建议相关标签"""
        all_tags = self.get_all_tags(min_usage=1)
        text_lower = text.lower()

        suggestions = []
        for tag in all_tags:
            tag_name = tag['name'].lower()
            if tag_name in text_lower:
                suggestions.append((tag['name'], tag['usage_count']))
            elif any(word in text_lower for word in tag_name.split()):
                suggestions.append((tag['name'], tag['usage_count']))

        suggestions.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in suggestions[:top_k]]

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词"""
        stop_words = {'的', '了', '和', '与', '或', '是', '在', '有', '为', '以',
                     '我', '你', '他', '她', '它', '们', '这', '那', '个', '一份',
                     '一份', '关于', '如何', '怎么', '什么', '哪个', '哪些'}

        text = re.sub(r'[^\w\s]', ' ', text)
        words = text.split()

        keywords = [w for w in words if len(w) >= 2 and w not in stop_words]
        return keywords


def get_files_with_tags(db: Session, file_ids: List[int] = None) -> List[Dict[str, Any]]:
    """获取文件列表及其标签"""
    query = db.query(FileEntry).filter(FileEntry.tag_status == 'ready')

    if file_ids:
        query = query.filter(FileEntry.id.in_(file_ids))

    files = query.all()
    results = []

    search_engine = TagSearchEngine(db)

    for f in files:
        results.append({
            'id': f.id,
            'name': f.name,
            'path': f.path,
            'extension': f.extension,
            'size': f.size,
            'content_summary': f.content_summary,
            'tags': search_engine.get_file_tags(f.id)
        })

    return results
