import os
import json
import requests
from typing import List, Dict, Any, Optional
from database import FileEntry

class OllamaEmbedder:
    """Ollama Embedding生成器"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url.rstrip('/')
        self.embedding_model = "nomic-embed-text"

    def is_available(self) -> bool:
        """检查Ollama是否可用"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        生成单条文本的Embedding向量
        """
        try:
            response = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=60
            )
            if response.status_code == 200:
                return response.json().get('embedding')
        except Exception as e:
            print(f"Embedding生成失败: {e}")
        return None

    def generate_embeddings_batch(self, texts: List[str],
                                  progress_callback=None) -> List[Optional[List[float]]]:
        """
        批量生成Embedding向量
        """
        results = []
        total = len(texts)

        for idx, text in enumerate(texts):
            embedding = self.generate_embedding(text)
            results.append(embedding)

            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback({
                    'type': 'progress',
                    'current': idx + 1,
                    'total': total,
                    'percentage': int((idx + 1) * 100 / total)
                })

        return results

    def generate_file_embedding(self, file_name: str, file_path: str,
                                content_preview: str = None) -> Optional[List[float]]:
        """
        为文件生成Embedding向量
        组合文件名、路径和内容预览作为输入文本
        """
        text_parts = [file_name]

        if file_path:
            dir_name = os.path.dirname(file_path)
            if dir_name:
                text_parts.append(dir_name)

        if content_preview:
            text_parts.append(content_preview[:1000])

        combined_text = " | ".join(text_parts)
        return self.generate_embedding(combined_text)


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def save_embedding_to_db(db, file_id: int, embedding: List[float]):
    """将Embedding向量保存到数据库"""
    file_entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if not file_entry:
        return False

    file_entry.embedding_vector = json.dumps(embedding)
    db.commit()
    return True


def get_embedding_from_db(db, file_id: int) -> Optional[List[float]]:
    """从数据库获取Embedding向量"""
    file_entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if not file_entry or not file_entry.embedding_vector:
        return None

    try:
        return json.loads(file_entry.embedding_vector)
    except:
        return None


def search_by_embedding(db, query_embedding: List[float],
                        file_ids: List[int] = None,
                        top_k: int = 20) -> List[Dict[int, float]]:
    """
    使用Embedding向量搜索相似文件
    返回: [(file_id, similarity_score), ...]
    """
    query = db.query(FileEntry).filter(
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
            if score > 0:
                results.append({'file_id': f.id, 'score': score})
        except:
            continue

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]


class TagEmbedder:
    """基于标签的Embedding（备用方案，不依赖Ollama）"""

    TAG_WEIGHTS = {
        '主题': 1.0,
        '类型': 0.8,
        '领域': 0.9,
        '场景': 0.7,
        '其他': 0.5
    }

    def __init__(self):
        pass

    def text_to_vector(self, text: str, vocabulary: Dict[str, int]) -> List[float]:
        """
        将文本转换为固定长度的向量（基于词汇权重）
        """
        words = set(text.lower().split())
        vec = [0.0] * len(vocabulary)

        for word in words:
            if word in vocabulary:
                vec[vocabulary[word]] = 1.0

        return vec

    def tags_to_vector(self, tags: List[Dict], vec_size: int = 512) -> List[float]:
        """
        将标签列表转换为向量
        """
        vec = [0.0] * vec_size
        for tag in tags:
            name = tag.get('name', '')
            category = tag.get('category', '其他')
            weight = self.TAG_WEIGHTS.get(category, 0.5)

            hash_val = hash(name) % vec_size
            vec[hash_val] = max(vec[hash_val], weight)

        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec
