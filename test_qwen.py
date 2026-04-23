import requests
import json

api_key = 'sk-a982b40a306d407ba67fc6d3bebccf5d'
model = 'qwen-turbo'

headers = {
    'Authorization': f'Bearer {api_key}',
    'Content-Type': 'application/json'
}

data = {
    'model': model,
    'messages': [
        {'role': 'system', 'content': 'You are a file organization assistant. Return only JSON.'},
        {'role': 'user', 'content': 'Organize these 3 files: 1.pdf, 2.docx, 3.jpg. Return JSON with folders.'}
    ],
    'max_tokens': 500
}

resp = requests.post('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', headers=headers, json=data, timeout=30)
print('Status:', resp.status_code)
result = resp.json()
print('finish_reason:', result.get('choices', [{}])[0].get('finish_reason'))
content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
print('Content:', content[:500])
