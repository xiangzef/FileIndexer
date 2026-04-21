import os
import json
import requests
from typing import Optional, Dict, Any

PROVIDER_CONFIGS = {
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "supports_stream": True
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "supports_stream": False
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-turbo",
        "supports_stream": False
    },
    "wenxin": {
        "base_url": "https://aip.baidubce.com/rpc/2.0/ai_custom/v1",
        "default_model": "ernie-speed",
        "supports_stream": False
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "default_model": "abab6-chat",
        "supports_stream": False
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "supports_stream": False
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "supports_stream": True
    }
}

class AIProvider:
    def __init__(self, provider: str = "ollama", api_key: Optional[str] = None,
                 model: Optional[str] = None, base_url: Optional[str] = None):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

        if provider in PROVIDER_CONFIGS:
            config = PROVIDER_CONFIGS[provider]
            if not self.base_url:
                self.base_url = config["base_url"]
            if not self.model:
                self.model = config["default_model"]

    def analyze_text(self, text: str, task: str = "summarize") -> Optional[str]:
        if self.provider == "local" or self.provider == "rule":
            return self._rule_analyze(text, task)
        else:
            return self._call_api(text, task)

    def _rule_analyze(self, text: str, task: str) -> str:
        if task == "summarize":
            if len(text) > 100:
                return text[:100] + "..."
            return text
        elif task == "classify":
            categories = {
                "工作": ["报告", "会议", "项目", "任务", "计划", "方案", "总结", "文档"],
                "个人": ["日记", "照片", "个人", "家庭", "旅行", "生活", "收藏"],
                "学习": ["学习", "教程", "课程", "作业", "笔记", "教材", "论文"],
                "财务": ["账单", "发票", "报销", "合同", "票据", "工资", "费用"],
                "图片": ["图片", "照片", "截图", "图", ".jpg", ".png", ".gif"],
                "视频": ["视频", "电影", "综艺", ".mp4", ".avi", ".mkv"],
                "音频": ["音乐", "歌曲", "音频", ".mp3", ".wav"],
                "其他": []
            }
            for category, keywords in categories.items():
                for keyword in keywords:
                    if keyword.lower() in text.lower():
                        return category
            return "其他"
        elif task == "keywords":
            import re
            words = re.findall(r'\b\w+\b', text)
            common_words = set(['的', '了', '和', '与', '或', '是', '在', '有', '为', '以', 'the', 'a', 'an', 'is', 'are'])
            keywords = [word for word in words if len(word) > 2 and word.lower() not in common_words]
            return ", ".join(set(keywords[:5]))
        return ""

    def _call_api(self, text: str, task: str) -> Optional[str]:
        if not self.api_key:
            return None

        if task == "summarize":
            prompt = f"请对以下文本进行简要总结，控制在50字以内：\n{text}"
        elif task == "classify":
            prompt = f"请将以下文本分类到合适类别（工作/个人/学习/财务/图片/视频/其他），只返回类别名称：\n{text}"
        elif task == "keywords":
            prompt = f"请从以下文本中提取5个关键词，用逗号分隔：\n{text}"
        else:
            prompt = f"请分析以下文本：\n{text}"

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }

            data = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100
            }

            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            else:
                return f"API错误: {response.status_code} - {response.text[:100]}"
        except Exception as e:
            return f"请求失败: {str(e)}"

    def analyze_files(self, files: list) -> Optional[Dict[str, Any]]:
        return {
            "provider": self.provider,
            "model": self.model,
            "file_count": len(files),
            "analysis": "文件分析完成"
        }

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        """
        使用AI聊天接口生成回复
        """
        if self.provider == "local" or self.provider == "rule":
            return self._rule_chat(user_prompt)

        if not self.api_key:
            return "错误: 缺少API Key"

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }

            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "max_tokens": 4000
            }

            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            else:
                return f"错误: API返回 {response.status_code} - {response.text[:200]}"
        except Exception as e:
            return f"错误: {str(e)}"

    def _rule_chat(self, user_prompt: str) -> str:
        """
        规则模式下的简单回复（用于测试）
        """
        return '{"folders":[{"name":"文档整理","files":[]}],"summary":"规则模式暂不支持AI整理"}'

def get_ai_provider(provider: str = "ollama", api_key: Optional[str] = None,
                     model: Optional[str] = None, base_url: Optional[str] = None) -> AIProvider:
    return AIProvider(provider, api_key, model, base_url)