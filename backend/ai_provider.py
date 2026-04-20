import os
import json
import requests
from typing import Optional, Dict, Any

class AIProvider:
    def __init__(self, mode: str = "local", api_key: Optional[str] = None):
        """
        初始化AI提供者
        mode: "local" 或 "cloud"
        api_key: 云端AI服务的API密钥
        """
        self.mode = mode
        self.api_key = api_key
    
    def analyze_text(self, text: str, task: str = "summarize") -> Optional[str]:
        """
        分析文本
        task: "summarize"（摘要）或 "classify"（分类）或 "keywords"（关键词）
        """
        if self.mode == "local":
            return self._local_analyze(text, task)
        elif self.mode == "cloud":
            return self._cloud_analyze(text, task)
        else:
            return None
    
    def _local_analyze(self, text: str, task: str) -> str:
        """
        本地分析文本
        """
        # 简单的本地分析实现
        if task == "summarize":
            # 简单摘要：取前100个字符
            if len(text) > 100:
                return text[:100] + "..."
            else:
                return text
        elif task == "classify":
            # 简单分类：基于关键词
            categories = {
                "工作": ["报告", "会议", "项目", "任务", "计划"],
                "个人": ["日记", "照片", "个人", "家庭", "旅行"],
                "学习": ["学习", "教程", "课程", "作业", "笔记"],
                "其他": []
            }
            
            for category, keywords in categories.items():
                for keyword in keywords:
                    if keyword in text:
                        return category
            return "其他"
        elif task == "keywords":
            # 简单关键词提取
            import re
            words = re.findall(r'\b\w+\b', text)
            # 过滤常见词和短词
            common_words = set(['的', '了', '和', '与', '或', '是', '在', '有', '为', '以'])
            keywords = [word for word in words if len(word) > 2 and word not in common_words]
            # 取前5个
            return ", ".join(keywords[:5])
        else:
            return ""
    
    def _cloud_analyze(self, text: str, task: str) -> Optional[str]:
        """
        云端分析文本
        """
        # 这里使用OpenAI API作为示例，实际项目中可以替换为其他云端AI服务
        if not self.api_key:
            return None
        
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            if task == "summarize":
                prompt = f"请对以下文本进行简要总结：\n{text}"
            elif task == "classify":
                prompt = f"请将以下文本分类到以下类别之一：工作、个人、学习、其他\n{text}"
            elif task == "keywords":
                prompt = f"请从以下文本中提取5个关键词：\n{text}"
            else:
                prompt = f"请分析以下文本：\n{text}"
            
            data = {
                "model": "gpt-3.5-turbo",
                "messages": [{
                    "role": "user",
                    "content": prompt
                }],
                "max_tokens": 100
            }
            
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            else:
                return None
        except Exception:
            return None
    
    def analyze_files(self, files: list) -> Optional[Dict[str, Any]]:
        """
        分析文件列表
        """
        # 这里可以实现更复杂的文件分析逻辑
        # 例如，读取文件内容并使用AI进行分析
        return {
            "mode": self.mode,
            "file_count": len(files),
            "analysis": "文件分析完成"
        }

def get_ai_provider(mode: str = "local", api_key: Optional[str] = None) -> AIProvider:
    """
    获取AI提供者实例
    """
    return AIProvider(mode, api_key)
