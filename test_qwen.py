import requests
import json

api_key = 'sk-a982b40a306d407ba67fc6d3bebccf5d'
model = 'qwen-turbo'

headers = {
    'Authorization': f'Bearer {api_key}',
    'Content-Type': 'application/json'
}

system_prompt = """你是一个专业的文件整理助手。你的任务是根据文件特征分析并生成最优的整理方案。

## 整理规则（按优先级）

1. 版本文件合并
2. 后缀不同同系列
3. 按文件类型整理

## 输出格式

必须返回JSON格式：
```json
{
  "folders": [
    {"name": "文件夹", "files": [{"id": 1, "name": "文件名.pdf"}]}
  ]
}
```

## 重要提醒
- 输出务必简洁！
- 只返回必要的JSON，不要包含任何解释性文字"""

user_prompt = """整理以下3个文件:
1. 年度报告.pdf [pdf] 2.5MB
2. 数据分析.docx [docx] 1.2MB
3. 图片.png [png] 500KB

返回JSON格式的整理方案。"""

data = {
    'model': model,
    'messages': [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt}
    ],
    'max_tokens': 500
}

resp = requests.post('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', headers=headers, json=data, timeout=60)
print('Status:', resp.status_code)
result = resp.json()
print('finish_reason:', result.get('choices', [{}])[0].get('finish_reason'))
content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
print('Content:', content)
print('---')
print('Is valid JSON?', end=' ')
try:
    json.loads(content)
    print('Yes')
except:
    print('No')
