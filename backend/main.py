from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import func as sql_func
import os
import re
import json
import shutil
import threading
import asyncio
import logging
from datetime import datetime

# 配置日志
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, f'ai_response_{datetime.now().strftime("%Y%m%d")}.log')

# 配置日志记录器
logger = logging.getLogger('ai_response_logger')
logger.setLevel(logging.INFO)

# 创建文件处理器
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setLevel(logging.INFO)

# 设置日志格式
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# 添加处理器到记录器
if not logger.handlers:
    logger.addHandler(file_handler)

def log_ai_response(provider: str, model: str, prompt: str, response: str, success: bool, error: str = None):
    """记录AI响应到日志文件"""
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'provider': provider,
        'model': model,
        'prompt': prompt[:500] + '...' if len(prompt) > 500 else prompt,
        'response': response[:1000] + '...' if len(response) > 1000 else response,
        'success': success,
        'error': error
    }
    logger.info(json.dumps(log_entry, ensure_ascii=False))

from database import engine, SessionLocal, Base, FileEntry, ScanRecord, Tag, FileTag
from scanner import (
    scan_directory, compute_md5_batch, find_duplicates,
    stop_scan, ALL_SUPPORTED, calculate_md5, SCAN_STOP_FLAG
)
from archiver import deduplicate_files, rename_duplicates_by_date, get_unique_filename, archive_files_smart
from ai_analyzer import analyze_files, ai_archive_files
from ai_provider import get_ai_provider
from ai_organizer import OrganizePromptBuilder, LearnedRule
from auto_mode import auto_detect_mode
from file_manager import (
    check_and_update_unavailable_files,
    get_all_source_paths,
    get_source_stats,
    suspend_files_by_source,
    restore_files_by_source,
    delete_files_by_source,
    suspend_files_by_ids,
    restore_files_by_ids
)
from tagger import AITagger, save_tags_to_db, remove_tags_from_db
from embedder import OllamaEmbedder, save_embedding_to_db
from tag_search import TagSearchEngine, get_files_with_tags

app = FastAPI(title="FileIndexer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _fix_missing_keys(text: str) -> str:
    """修复缺失键名的问题，如 {"value", "key": "val"} -> {"name": "value", "key": "val"}"""
    text = re.sub(r'\{\s*"([^"]+)"\s*,\s*"([^"]+)"\s*:', r'{"name": "\1", "\2":', text)
    return text

def _fix_placeholders(text: str) -> str:
    """修复模板占位符，如 "文件夹名" -> "未分类文件夹""" 
    text = text.replace('"文件夹名"', '"未分类文件夹"')
    text = text.replace('"文件名"', '"未知文件"')
    text = text.replace('"文件ID"', '"0"')
    text = text.replace('"数字ID"', '"0"')
    return text

def _sanitize_json(text: str) -> str:
    """修复本地模型常见的JSON格式问题"""
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace('，', ',').replace('：', ':').replace('【', '[').replace('】', ']')
    text = text.replace('{', '{').replace('}', '}')
    text = text.replace('（', '(').replace('）', ')')
    text = _fix_missing_keys(text)
    text = _fix_placeholders(text)
    return text

def _fix_json_brackets(text: str) -> str:
    """修复括号错乱问题：把对象位置的()替换为{}"""
    result = []
    i = 0
    in_string = False
    escape_next = False
    while i < len(text):
        ch = text[i]
        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == '\\':
            result.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if not in_string:
            if ch == '(':
                result.append('{')
                i += 1
                continue
            elif ch == ')':
                result.append('}')
                i += 1
                continue
        result.append(ch)
        i += 1
    return ''.join(result)

def _extract_json_array(text: str):
    """从文本中提取JSON数组"""
    text = _sanitize_json(text)
    text = _fix_json_brackets(text)
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i+1]
    return None

def _extract_json_object(text: str):
    """从文本中提取JSON对象"""
    text = _sanitize_json(text)
    text = _fix_json_brackets(text)
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i+1]
    return None

def _try_fix_json(text: str) -> str:
    """尝试修复不完整的JSON"""
    # 移除解释文字
    text = text.strip()
    if '以下是根据您提供的清单整理的' in text:
        text = text.split('JSON 格式结构:')[-1].strip()
    elif '根据您提供的文件分类结果' in text:
        text = text.split('JSON:')[-1].strip()
    
    # 修复代码块包裹
    if '```' in text:
        lines = text.split('\n')
        code_start = -1
        code_end = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('```'):
                if code_start == -1:
                    code_start = i
                else:
                    code_end = i
                    break
        if code_start >= 0:
            if code_end >= 0:
                text = '\n'.join(lines[code_start+1:code_end])
            else:
                text = '\n'.join(lines[code_start+1:])
    
    # 修复JSON格式
    text = text.strip()
    if not text.startswith('{') and not text.startswith('['):
        if text.startswith('json') or text.startswith('JSON'):
            lines = text.split('\n')
            for idx, line in enumerate(lines):
                line = line.strip()
                if line.startswith('{') or line.startswith('['):
                    text = '\n'.join(lines[idx:])
                    break
            else:
                for idx, line in enumerate(lines):
                    if '{' in line or '[' in line:
                        text = '\n'.join(lines[idx:])
                        break
        else:
            idx = text.find('{')
            if idx >= 0:
                text = text[idx:]
            else:
                idx = text.find('[')
                if idx >= 0:
                    text = text[idx:]
    
    # 应用其他修复
    text = _sanitize_json(text)
    text = _fix_json_brackets(text)
    
    # 修复截断的JSON：补全未闭合的括号和引号
    if text:
        fixed = _complete_json(text)
        if fixed:
            return fixed
    return text

def _complete_json(text: str) -> str:
    """尝试补全被截断的JSON"""
    stack = []
    in_string = False
    escape_next = False
    last_valid = 0
    
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            stack.append((ch, i))
            last_valid = i
        elif ch in ('}', ']'):
            if stack:
                open_ch, _ = stack[-1]
                if (open_ch == '{' and ch == '}') or (open_ch == '[' and ch == ']'):
                    stack.pop()
                    last_valid = i
                else:
                    # 括号不匹配，截断到这里
                    return text[:i] + _close_brackets(stack)
            else:
                # 多余的闭合括号，截断
                return text[:i] + _close_brackets([])
    
    # 如果还有未闭合的括号，尝试补全
    if stack:
        return text + _close_brackets(stack)
    return None

def _close_brackets(stack) -> str:
    """补全未闭合的括号"""
    result = []
    for ch, _ in reversed(stack):
        if ch == '{':
            result.append('}')
        elif ch == '[':
            result.append(']')
    return ''.join(result)

def _ai_organize_chunked(files, learned_rules, include_content, ai_provider, provider, model):
    """
    分批处理大量文件的AI整理
    """
    BATCH_SIZE = 50
    batches = [files[i:i+BATCH_SIZE] for i in range(0, len(files), BATCH_SIZE)]

    prompt_builder = OrganizePromptBuilder(learned_rules)
    system_prompt = prompt_builder.build_system_prompt()

    all_classifications = {}
    total_batches = len(batches)

    for batch_idx, batch in enumerate(batches):
        batch_data = []
        for f in batch:
            fd = {"id": f.id, "name": f.name, "ext": f.extension, "size": f.size}
            if include_content and f.extension in ['.txt', '.csv', '.md', '.log', '.py', '.js', '.java', '.cpp', '.c', '.h', '.css', '.html', '.xml', '.json']:
                try:
                    with open(f.path, 'r', encoding='utf-8', errors='ignore') as fp:
                        fd['text'] = fp.read()[:150]
                except:
                    pass
            batch_data.append(fd)

        user_prompt = f"""这是第{batch_idx+1}/{total_batches}批文件（共{len(batch)}个）。请分析并返回JSON格式的分类结果：

{prompt_builder.format_file_list(batch_data)}

重要规则：
1. 只返回一个JSON数组，不要任何解释文字
2. 确保JSON格式完整，不要用代码块包裹
3. 每个对象必须包含"name"和"folder"字段
4. folder字段必须是具体的分类名称，不能用"文件夹名"等占位符

格式：[{{"name":"文件名","folder":"建议的文件夹名"}}]"""

        response = ai_provider.chat(system_prompt, user_prompt)
        
        # 记录AI响应
        log_ai_response(provider, model, user_prompt, response, success=False)

        if response.startswith("错误:"):
            print(f"第{batch_idx+1}批处理失败: {response}")
            log_ai_response(provider, model, user_prompt, response, success=False, error=response)
            continue

        try:
            classifications = json.loads(response)
            if isinstance(classifications, list):
                for c in classifications:
                    if isinstance(c, dict) and 'name' in c and 'folder' in c:
                        all_classifications[c['name']] = c['folder']
                print(f"第{batch_idx+1}批处理成功，分类{len(classifications)}个文件")
                log_ai_response(provider, model, user_prompt, response, success=True)
                continue
        except Exception as e:
            log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))
            pass

        json_str = _extract_json_array(response)
        if json_str:
            try:
                classifications = json.loads(_sanitize_json(json_str))
                if isinstance(classifications, list):
                    for c in classifications:
                        if isinstance(c, dict) and 'name' in c and 'folder' in c:
                            all_classifications[c['name']] = c['folder']
                    print(f"第{batch_idx+1}批提取成功，分类{len(classifications)}个文件")
                    log_ai_response(provider, model, user_prompt, response, success=True)
                    continue
            except Exception as e:
                print(f"第{batch_idx+1}批解析失败: {e}")
                log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))

        json_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', response)
        if json_match:
            try:
                classifications = json.loads(_sanitize_json(json_match.group(0)))
                if isinstance(classifications, list):
                    for c in classifications:
                        if isinstance(c, dict) and 'name' in c and 'folder' in c:
                            all_classifications[c['name']] = c['folder']
                    print(f"第{batch_idx+1}批正则提取成功，分类{len(classifications)}个文件")
                    log_ai_response(provider, model, user_prompt, response, success=True)
                    continue
            except Exception as e:
                log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))
                pass

        print(f"第{batch_idx+1}批解析失败，跳过。返回内容: {response[:150]}")
        log_ai_response(provider, model, user_prompt, response, success=False, error="解析失败")
        continue

    if not all_classifications:
        return {"error": "AI分类失败，所有批次均未成功返回结果"}

    file_list = "\n".join([f"{f.id}. {f.name} → {all_classifications.get(f.name, '未分类')}" for f in files])

    final_prompt = f"""基于以下文件分类结果，生成最终的整理方案JSON。

重要规则：
1. 每个文件的id必须使用对应的数字ID（文件ID），不能用"文件ID"等占位符
2. 每个文件夹名必须是具体的分类名称，不能用"文件夹名"、"具体文件夹名"等占位符
3. 每个文件名必须是实际的文件名，不能用"文件名"等占位符
4. 只返回JSON，不要任何解释文字，不要用代码块包裹
5. 确保JSON格式完整，不要截断
6. **必须生成至少一个文件夹**，不能为空的folders数组
7. **所有文件都必须被分配到文件夹中**，不能遗漏任何文件
8. **直接返回JSON对象**，格式为：{"folders":[{"name":"具体文件夹名","files":[{"id":数字ID,"name":"实际文件名"}]}]}

输入格式：文件ID. 文件名 → 文件夹名
{file_list}

输出JSON格式：
{{"folders":[{{"name":"具体文件夹名","files":[{{"id":数字ID,"name":"实际文件名"}}]}}]}}"""

    response = ai_provider.chat(system_prompt, final_prompt)
    
    # 记录AI响应
    log_ai_response(provider, model, final_prompt, response, success=False)

    if response.startswith("错误:"):
        log_ai_response(provider, model, final_prompt, response, success=False, error=response)
        return {"error": f"生成最终方案失败: {response}"}

    def _fix_placeholders(plan):
        """修复模板占位符，将'文件ID'等替换为实际ID"""
        name_to_id = {f.name: f.id for f in files}
        # 生成文件夹名称映射，确保每个文件夹有唯一的有意义名称
        folder_counter = {}
        
        def get_unique_folder_name(base_name):
            """生成唯一的文件夹名称"""
            if base_name and base_name != '具体文件夹名' and base_name != '文件夹名':
                return base_name
            # 如果是占位符，生成基于文件内容的文件夹名
            return '未分类文件'
        
        if isinstance(plan, dict):
            if 'folders' in plan:
                for folder in plan['folders']:
                    if isinstance(folder, dict):
                        # 修复文件夹名称
                        folder_name = folder.get('name', '')
                        folder['name'] = get_unique_folder_name(folder_name)
                        
                        # 修复文件ID
                        if 'files' in folder:
                            for file_entry in folder['files']:
                                if isinstance(file_entry, dict):
                                    file_id = file_entry.get('id')
                                    file_name = file_entry.get('name', '')
                                    if not isinstance(file_id, int) or str(file_id) == '文件ID' or str(file_id) == '数字ID' or file_id == 0:
                                        file_entry['id'] = name_to_id.get(file_name, 0)
                        
                        # 修复子文件夹
                        if 'subfolders' in folder:
                            for sub in folder['subfolders']:
                                if isinstance(sub, dict):
                                    sub_name = sub.get('name', '')
                                    sub['name'] = get_unique_folder_name(sub_name)
                                    if 'files' in sub:
                                        for file_entry in sub['files']:
                                            if isinstance(file_entry, dict):
                                                file_id = file_entry.get('id')
                                                file_name = file_entry.get('name', '')
                                                if not isinstance(file_id, int) or str(file_id) == '文件ID' or str(file_id) == '数字ID' or file_id == 0:
                                                    file_entry['id'] = name_to_id.get(file_name, 0)
        return plan

    try:
        plan = json.loads(response)
        if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'folders' in plan[0] and plan[0].get('folders') and len(plan[0].get('folders')) > 0:
            result = _fix_placeholders(plan[0])
            log_ai_response(provider, model, final_prompt, response, success=True)
            return result
        if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
            result = _fix_placeholders(plan)
            log_ai_response(provider, model, final_prompt, response, success=True)
            return result
    except Exception as e:
        log_ai_response(provider, model, final_prompt, response, success=False, error=str(e))
        pass

    json_str = _extract_json_object(response)
    if json_str:
        try:
            plan = json.loads(json_str)
            if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                result = _fix_placeholders(plan)
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
        except Exception as e:
            print(f"提取JSON对象失败: {e}")
            log_ai_response(provider, model, final_prompt, response, success=False, error=str(e))

    json_str = _extract_json_array(response)
    if json_str:
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and 'folders' in parsed[0] and parsed[0].get('folders') and len(parsed[0].get('folders')) > 0:
                result = _fix_placeholders(parsed[0])
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
        except Exception as e:
            log_ai_response(provider, model, final_prompt, response, success=False, error=str(e))
            pass

    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
    if json_match:
        content = json_match.group(1).strip()
        content = _try_fix_json(content)
        try:
            plan = json.loads(content)
            if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'folders' in plan[0] and plan[0].get('folders') and len(plan[0].get('folders')) > 0:
                result = _fix_placeholders(plan[0])
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
            if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                result = _fix_placeholders(plan)
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
        except Exception as e:
            log_ai_response(provider, model, final_prompt, response, success=False, error=str(e))
            pass

    fixed = _try_fix_json(response)
    if fixed:
        try:
            plan = json.loads(fixed)
            if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'folders' in plan[0] and plan[0].get('folders') and len(plan[0].get('folders')) > 0:
                result = _fix_placeholders(plan[0])
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
            if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                result = _fix_placeholders(plan)
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
            # 处理AI返回数组直接作为folders的情况
            if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict) and 'name' in plan[0] and 'files' in plan[0]:
                result = {"folders": plan}
                result = _fix_placeholders(result)
                log_ai_response(provider, model, final_prompt, response, success=True)
                return result
        except Exception as e:
            print(f"最终修复解析失败: {e}")
            log_ai_response(provider, model, final_prompt, response, success=False, error=str(e))
            pass

    error_msg = f"AI返回格式错误。实际返回: {response[:300]}"
    log_ai_response(provider, model, final_prompt, response, success=False, error=error_msg)
    return {"error": error_msg}

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    if isinstance(exc, asyncio.CancelledError):
        return JSONResponse(status_code=499, content={"error": "请求已取消"})
    return JSONResponse(status_code=500, content={"error": f"服务器内部错误: {str(exc)}"})

Base.metadata.create_all(bind=engine)

scan_lock = threading.Lock()
_stop_flags = {}

def _send_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

@app.get("/")
async def root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"))

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/supported-extensions")
async def get_supported_extensions():
    return {
        "doc": list({'.doc', '.docx'}),
        "csv": list({'.csv', '.els', '.elsx'}),
        "image": list({'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}),
        "pdf": list({'.pdf'}),
        "all": list(ALL_SUPPORTED)
    }

@app.get("/ai/models")
async def get_ai_models(provider: str = "ollama", base_url: str = None):
    from ai_provider import PROVIDER_CONFIGS, get_provider_models
    import requests

    config = PROVIDER_CONFIGS.get(provider, {})
    models = get_provider_models(provider)

    default_model = config.get("default_model", "")

    if provider == "ollama":
        ollama_base = base_url or "http://localhost:11434"
        try:
            response = requests.get(f"{ollama_base}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                if models and not default_model:
                    default_model = models[0]
        except Exception as e:
            print(f"获取Ollama模型失败: {e}")

    return {
        "provider": provider,
        "default_model": default_model,
        "models": models
    }

@app.post("/scan")
async def scan_paths(request: Request):
    """
    扫描指定目录，生成文件索引
    """
    import uuid

    body = await request.json()
    paths = body if isinstance(body, list) else body.get("paths", [])

    job_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    from scanner import clear_stop_flag
    clear_stop_flag()
    with scan_lock:
        _stop_flags[job_id] = stop_event

    async def generate():
        db = SessionLocal()

        def on_progress(progress_data):
            return _send_sse(progress_data)

        total_scanned = 0
        for directory in paths:
            if stop_event.is_set():
                break

            if not os.path.isdir(directory):
                yield _send_sse({"type": "error", "directory": directory, "message": "目录不存在"})
                continue

            for result in scan_directory(db, directory, progress_callback=on_progress):
                if stop_event.is_set():
                    yield _send_sse({"type": "stopped", "directory": directory})
                    break
                if result["type"] == "progress":
                    yield _send_sse(result)
                elif result["type"] == "complete":
                    total_scanned += result["total"]
                    yield _send_sse(result)
                elif result["type"] == "error":
                    yield _send_sse(result)
                elif result["type"] == "stopped":
                    yield _send_sse(result)
                    break

        yield _send_sse({
            "type": "done",
            "job_id": job_id,
            "total_scanned": total_scanned
        })

        with scan_lock:
            _stop_flags.pop(job_id, None)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    })

@app.post("/scan/stop")
async def stop_scan_job(job_id: str = None):
    from scanner import SCAN_STOP_FLAG
    if job_id:
        with scan_lock:
            if job_id in _stop_flags:
                _stop_flags[job_id].set()
                SCAN_STOP_FLAG.set()
                return {"success": True, "message": f"已停止任务: {job_id}"}
    else:
        for ev in _stop_flags.values():
            ev.set()
        SCAN_STOP_FLAG.set()
        return {"success": True, "message": "已停止所有任务"}
    return {"success": False, "message": "任务不存在"}

@app.post("/compute-md5")
async def compute_md5_for_files(file_ids: List[int] = None):
    """
    计算文件的 MD5 哈希值
    """
    import uuid

    job_id = str(uuid.uuid4())[:8]
    stop_event = threading.Event()
    with scan_lock:
        _stop_flags[job_id] = stop_event

    async def generate():
        db = SessionLocal()

        if file_ids:
            ids_to_compute = file_ids
        else:
            entries = db.query(FileEntry).filter(FileEntry.md5 == None).all()
            ids_to_compute = [e.id for e in entries]

        if not ids_to_compute:
            yield _send_sse({"type": "done", "job_id": job_id, "message": "没有需要计算 MD5 的文件"})
            return

        for result in compute_md5_batch(db, ids_to_compute):
            if stop_event.is_set():
                yield _send_sse({"type": "stopped"})
                break
            yield _send_sse(result)

        with scan_lock:
            _stop_flags.pop(job_id, None)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no"
    })

@app.get("/files")
async def get_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    extension: str = None,
    is_duplicate: bool = None,
    keyword: str = None,
    status: str = None,
    tag_status: str = None,
    file_type: str = None,
    file_types: str = None
):
    db = SessionLocal()
    query = db.query(FileEntry)

    if extension:
        query = query.filter(FileEntry.extension == extension)
    if is_duplicate is not None:
        query = query.filter(FileEntry.is_duplicate == is_duplicate)
    if keyword:
        query = query.filter(FileEntry.name.contains(keyword))
    if status:
        query = query.filter(FileEntry.status == status)
    if tag_status:
        query = query.filter(FileEntry.tag_status == tag_status)
    if file_type:
        type_map = {
            'doc': ['.doc', '.docx', '.wps', '.wpt'],
            'pdf': ['.pdf'],
            'xls': ['.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'],
            'ppt': ['.ppt', '.pptx', '.pot', '.potx'],
            'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
            'txt': ['.txt', '.md', '.log', '.chm'],
            'ebook': ['.epub', '.mobi', '.azw', '.azw3', '.azw4', '.kf8', '.kfx', '.fb2', '.cbr', '.cbz', '.ibooks']
        }
        exts = type_map.get(file_type, [])
        if exts:
            query = query.filter(FileEntry.extension.in_(exts))
    if file_types:
        import json
        try:
            types = json.loads(file_types)
            all_exts = []
            type_map = {
                'doc': ['.doc', '.docx', '.wps', '.wpt'],
                'pdf': ['.pdf'],
                'xls': ['.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'],
                'ppt': ['.ppt', '.pptx', '.pot', '.potx'],
                'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
                'txt': ['.txt', '.md', '.log', '.chm'],
                'ebook': ['.epub', '.mobi', '.azw', '.azw3', '.azw4', '.kf8', '.kfx', '.fb2', '.cbr', '.cbz', '.ibooks']
            }
            for t in types:
                all_exts.extend(type_map.get(t, []))
            if all_exts:
                query = query.filter(FileEntry.extension.in_(all_exts))
        except:
            pass

    total = query.count()
    items = query.order_by(FileEntry.id.desc()).offset((page-1)*page_size).limit(page_size).all()

    duplicates_count = db.query(FileEntry).filter(FileEntry.is_duplicate == True).count()

    # 预加载tag_count
    file_ids = [e.id for e in items]
    tag_counts = {}
    if file_ids:
        from database import FileTag
        tag_count_rows = db.query(FileTag.file_id, sql_func.count(FileTag.id)).filter(
            FileTag.file_id.in_(file_ids)
        ).group_by(FileTag.file_id).all()
        tag_counts = {fid: count for fid, count in tag_count_rows}

    # 预加载所有标签，并标注是否已显示
    tag_all_data = {}
    if file_ids:
        from database import Tag
        tag_rows = db.query(FileTag.file_id, Tag.name, Tag.category, FileTag.confidence).join(
            Tag, Tag.id == FileTag.tag_id
        ).filter(
            FileTag.file_id.in_(file_ids)
        ).order_by(FileTag.file_id, FileTag.confidence.desc()).all()

        current_file_id = None
        file_tags = []
        for row in tag_rows:
            if row[0] != current_file_id:
                if current_file_id is not None and file_tags:
                    tag_all_data[current_file_id] = file_tags
                current_file_id = row[0]
                file_tags = []
            file_tags.append({'name': row[1], 'category': row[2], 'shown': len(file_tags) < 3})
        if current_file_id is not None and file_tags:
            tag_all_data[current_file_id] = file_tags

    db.close()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "duplicates_count": duplicates_count,
        "items": [{
            "id": e.id,
            "path": e.path,
            "name": e.name,
            "extension": e.extension,
            "size": e.size,
            "md5": e.md5,
            "created_time": e.created_time.isoformat() if e.created_time else None,
            "modified_time": e.modified_time.isoformat() if e.modified_time else None,
            "is_duplicate": e.is_duplicate,
            "duplicate_of_id": e.duplicate_of_id,
            "status": e.status,
            "source_path": e.source_path,
            "tag_status": e.tag_status,
            "tag_count": tag_counts.get(e.id, 0),
            "tag_preview": tag_all_data.get(e.id, []),
            "content_summary": e.content_summary
        } for e in items]
    }

@app.get("/files/all-ids")
async def get_all_file_ids(
    extension: str = None,
    is_duplicate: bool = None,
    keyword: str = None,
    status: str = None,
    tag_status: str = None,
    file_types: str = None
):
    db = SessionLocal()
    query = db.query(FileEntry.id)

    if extension:
        query = query.filter(FileEntry.extension == extension)
    if is_duplicate is not None:
        query = query.filter(FileEntry.is_duplicate == is_duplicate)
    if keyword:
        query = query.filter(FileEntry.name.contains(keyword))
    if status:
        query = query.filter(FileEntry.status == status)
    else:
        query = query.filter(FileEntry.status != 'suspended')
    if tag_status:
        query = query.filter(FileEntry.tag_status == tag_status)
    if file_types:
        import json
        try:
            types = json.loads(file_types)
            all_exts = []
            type_map = {
                'doc': ['.doc', '.docx'],
                'pdf': ['.pdf'],
                'xls': ['.csv', '.xls', '.xlsx', '.els', '.elsx'],
                'ppt': ['.ppt', '.pptx'],
                'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
                'txt': ['.txt', '.md', '.log']
            }
            for t in types:
                all_exts.extend(type_map.get(t, []))
            if all_exts:
                query = query.filter(FileEntry.extension.in_(all_exts))
        except:
            pass

    ids = [row[0] for row in query.all()]
    db.close()
    return {"ids": ids, "total": len(ids)}

@app.get("/duplicates")
async def get_duplicates():
    db = SessionLocal()
    duplicates = db.query(FileEntry).filter(FileEntry.is_duplicate == True).all()
    db.close()

    groups = {}
    for dup in duplicates:
        if dup.duplicate_of_id:
            if dup.duplicate_of_id not in groups:
                groups[dup.duplicate_of_id] = []
            groups[dup.duplicate_of_id].append({
                "id": dup.id,
                "path": dup.path,
                "name": dup.name,
                "size": dup.size,
                "modified_time": dup.modified_time.isoformat() if dup.modified_time else None
            })

    return {"groups": groups, "total_duplicates": len(duplicates)}

@app.post("/deduplicate")
async def deduplicate():
    """
    查找并标记重复文件
    """
    async def generate():
        db = SessionLocal()
        for result in deduplicate_files(db):
            yield _send_sse(result)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })

@app.post("/rename-by-date")
async def rename_by_date():
    """
    将重复文件按日期重命名
    """
    async def generate():
        db = SessionLocal()
        for result in rename_duplicates_by_date(db):
            yield _send_sse(result)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })

@app.post("/archive")
async def archive_files(request: dict):
    """
    智能归档文件到指定目录，按文件类型分类，相似文件归到同一文件夹
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    async def generate():
        db = SessionLocal()
        for result in archive_files_smart(db, file_ids, target_dir, mode):
            yield _send_sse(result)
        db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })

@app.post("/archive-simple")
async def archive_files_simple(request: dict):
    """
    简单归档文件到指定目录（不分类）
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()

    path_updates = []
    results = []
    for entry in entries:
        try:
            temp_name = get_unique_filename(temp_dir, entry.name)
            temp_path = os.path.join(temp_dir, temp_name)

            if mode == "move":
                shutil.move(entry.path, temp_path)
            else:
                shutil.copy2(entry.path, temp_path)

            unique_name = get_unique_filename(target_dir, entry.name)
            target_path = os.path.join(target_dir, unique_name)
            shutil.move(temp_path, target_path)

            path_updates.append((entry, target_path, unique_name))
            results.append({
                "id": entry.id,
                "success": True,
                "new_path": target_path
            })
        except Exception as e:
            results.append({
                "id": entry.id,
                "success": False,
                "error": str(e)
            })

    for entry, new_path, new_name in path_updates:
        entry.path = new_path
        entry.name = new_name

    db.commit()
    db.close()

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    return {"results": results}

@app.delete("/files/{file_id}")
async def delete_file(file_id: int):
    db = SessionLocal()
    entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="文件记录不存在")

    if os.path.exists(entry.path):
        try:
            os.remove(entry.path)
        except Exception as e:
            db.close()
            raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")

    db.delete(entry)
    db.commit()
    db.close()

    return {"success": True, "message": "文件已删除"}

@app.delete("/duplicates/{duplicate_id}")
async def delete_duplicate(duplicate_id: int):
    db = SessionLocal()
    entry = db.query(FileEntry).filter(FileEntry.id == duplicate_id).first()
    if not entry:
        db.close()
        raise HTTPException(status_code=404, detail="文件不存在")

    if not entry.is_duplicate:
        db.close()
        raise HTTPException(status_code=400, detail="该文件不是重复文件")

    if os.path.exists(entry.path):
        try:
            os.remove(entry.path)
        except Exception as e:
            db.close()
            raise HTTPException(status_code=500, detail=f"删除文件失败: {str(e)}")

    db.delete(entry)
    db.commit()
    db.close()

    return {"success": True, "message": "重复文件已删除"}

@app.get("/stats")
async def get_stats():
    db = SessionLocal()
    total_files = db.query(FileEntry).count()
    total_duplicates = db.query(FileEntry).filter(FileEntry.is_duplicate == True).count()
    total_size = sum(e.size for e in db.query(FileEntry).all())

    by_extension = {}
    for ext in ALL_SUPPORTED:
        count = db.query(FileEntry).filter(FileEntry.extension == ext).count()
        if count > 0:
            by_extension[ext] = count

    db.close()

    return {
        "total_files": total_files,
        "total_duplicates": total_duplicates,
        "total_size": total_size,
        "by_extension": by_extension
    }

@app.post("/ai/analyze")
async def ai_analyze_files(request: dict):
    """
    使用AI分析文件，基于文件名和内容
    """
    try:
        file_ids = request.get("file_ids", [])
        provider = request.get("provider", "ollama")
        api_key = request.get("api_key", None)
        model = request.get("model", None)
        base_url = request.get("base_url", None)

        db = SessionLocal()
        try:
            ai_provider = get_ai_provider(provider, api_key, model, base_url)
            result = analyze_files(db, file_ids, ai_provider)
            return result
        finally:
            db.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e), "detail": "AI分析失败，请检查配置和文件路径"}

@app.post("/ai/archive")
async def ai_archive_files_endpoint(request: dict):
    """
    使用AI分析结果归档文件
    """
    file_ids = request.get("file_ids", [])
    target_dir = request.get("target_dir", "")
    mode = request.get("mode", "copy")
    provider = request.get("provider", "ollama")
    api_key = request.get("api_key", None)
    model = request.get("model", None)
    base_url = request.get("base_url", None)

    if not target_dir:
        raise HTTPException(status_code=400, detail="目标目录不能为空")

    db = SessionLocal()
    ai_provider = get_ai_provider(provider, api_key, model, base_url)
    results = ai_archive_files(db, file_ids, target_dir, mode, ai_provider)
    db.close()
    return {"results": results}

@app.post("/auto/detect-mode")
async def auto_detect_mode_endpoint(file_ids: List[int]):
    """
    自动检测文件的处理模式
    """
    db = SessionLocal()
    result = auto_detect_mode(db, file_ids)
    db.close()
    return result

@app.post("/check-unavailable")
async def check_unavailable():
    """
    检测并标记不可访问的文件
    """
    db = SessionLocal()
    result = check_and_update_unavailable_files(db)
    db.close()
    return result

@app.get("/source-paths")
async def get_source_paths():
    """
    获取所有来源路径及其统计信息
    """
    db = SessionLocal()
    stats = get_source_stats(db)
    db.close()
    return {"sources": stats}

@app.post("/source/{source_path}/suspend")
async def suspend_source(source_path: str):
    """
    挂起指定来源路径下的所有文件
    """
    db = SessionLocal()
    result = suspend_files_by_source(db, source_path)
    db.close()
    return result

@app.post("/source/{source_path}/restore")
async def restore_source(source_path: str):
    """
    恢复指定来源路径下的所有文件
    """
    db = SessionLocal()
    result = restore_files_by_source(db, source_path)
    db.close()
    return result

@app.delete("/source/{source_path}")
async def delete_source(source_path: str):
    """
    删除指定来源路径下的所有文件记录
    """
    db = SessionLocal()
    result = delete_files_by_source(db, source_path)
    db.close()
    return result

@app.post("/files/suspend")
async def suspend_files(request: dict):
    """
    挂起指定文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    result = suspend_files_by_ids(db, file_ids)
    db.close()
    return result

@app.post("/files/restore")
async def restore_files(request: dict):
    """
    恢复指定文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    result = restore_files_by_ids(db, file_ids)
    db.close()
    return result

@app.get("/scan-records")
async def get_scan_records():
    """
    获取所有扫描记录及统计
    """
    db = SessionLocal()
    records = db.query(ScanRecord).order_by(ScanRecord.scan_time.desc()).all()
    result = []
    for r in records:
        stats = {}
        if r.stats_json:
            try:
                stats = json.loads(r.stats_json)
            except:
                pass
        result.append({
            "id": r.id,
            "scan_path": r.scan_path,
            "scan_time": r.scan_time.isoformat() if r.scan_time else None,
            "total_files": r.total_files,
            "total_size": r.total_size,
            "status": r.status,
            "stats": stats
        })
    db.close()
    return {"records": result}

@app.post("/scan-records/{record_id}/suspend")
async def suspend_scan_record(record_id: int):
    """
    挂起指定扫描记录下的所有文件
    """
    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    count = 0
    for entry in entries:
        entry.status = 'suspended'
        count += 1
    db.commit()
    db.close()
    return {"suspended_count": count}

@app.post("/scan-records/{record_id}/restore")
async def restore_scan_record(record_id: int):
    """
    恢复指定扫描记录下的所有文件
    """
    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    count = 0
    for entry in entries:
        if entry.status == 'suspended':
            entry.status = 'available'
            count += 1
    db.commit()
    db.close()
    return {"restored_count": count}

@app.delete("/scan-records/{record_id}")
async def delete_scan_record(record_id: int):
    """
    删除指定扫描记录下的所有文件记录
    """
    db = SessionLocal()
    entries = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    count = 0
    for entry in entries:
        db.delete(entry)
        count += 1
    record = db.query(ScanRecord).filter(ScanRecord.id == record_id).first()
    if record:
        db.delete(record)
    db.commit()
    db.close()
    return {"deleted_count": count}

@app.post("/files/batch-delete")
async def batch_delete_files(request: dict):
    """
    批量删除选中的文件记录
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    count = 0
    for fid in file_ids:
        entry = db.query(FileEntry).filter(FileEntry.id == fid).first()
        if entry:
            db.delete(entry)
            count += 1
    db.commit()
    db.close()
    return {"deleted_count": count}

@app.post("/files/batch-suspend")
async def batch_suspend_files(request: dict):
    """
    批量挂起选中的文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    count = 0
    for fid in file_ids:
        entry = db.query(FileEntry).filter(FileEntry.id == fid).first()
        if entry:
            entry.status = 'suspended'
            count += 1
    db.commit()
    db.close()
    return {"suspended_count": count}

@app.post("/files/batch-restore")
async def batch_restore_files(request: dict):
    """
    批量恢复选中的文件
    """
    file_ids = request.get("file_ids", [])
    db = SessionLocal()
    count = 0
    for fid in file_ids:
        entry = db.query(FileEntry).filter(FileEntry.id == fid).first()
        if entry and entry.status == 'suspended':
            entry.status = 'available'
            count += 1
    db.commit()
    db.close()
    return {"restored_count": count}

@app.get("/scan-record/{record_id}/files")
async def get_scan_record_files(record_id: int):
    """
    获取指定扫描记录下的所有文件
    """
    db = SessionLocal()
    files = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()
    result = []
    for f in files:
        result.append({
            "id": f.id,
            "name": f.name,
            "path": f.path,
            "extension": f.extension,
            "size": f.size,
            "md5": f.md5,
            "status": f.status
        })
    db.close()
    return {"files": result}

@app.post("/ai/organize/plan")
async def generate_organize_plan(request: dict):
    """
    使用AI生成文件整理方案
    """
    record_id = request.get("record_id")
    file_ids = request.get("file_ids")
    provider = request.get("provider", "ollama")
    api_key = request.get("api_key")
    model = request.get("model")
    include_content = request.get("include_content", False)
    learn_mode = request.get("learn_mode", True)

    db = SessionLocal()
    try:
        rule_storage_path = os.path.join(os.path.dirname(__file__), "learned_rules.json")
        rule_learner = LearnedRule(rule_storage_path)
        learned_rules = rule_learner.get_recent_rules(limit=10) if learn_mode else []

        ai_provider = get_ai_provider(provider, api_key, model)

        if file_ids:
            if len(file_ids) > 500:
                files = []
                for i in range(0, len(file_ids), 500):
                    batch = file_ids[i:i+500]
                    batch_files = db.query(FileEntry).filter(FileEntry.id.in_(batch)).all()
                    files.extend(batch_files)
            else:
                files = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
        else:
            files = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()

        if not files:
            return {"error": "没有找到文件"}

        MAX_FILES_FOR_AI = 60
        if len(files) > MAX_FILES_FOR_AI:
            return _ai_organize_chunked(files, learned_rules, include_content, ai_provider, provider, model)

        file_data = []
        for f in files:
            fd = {
                "id": f.id,
                "name": f.name,
                "ext": f.extension,
                "size": f.size
            }

            if include_content and f.extension in ['.txt', '.csv', '.md', '.log', '.py', '.js', '.java', '.cpp', '.c', '.h', '.css', '.html', '.xml', '.json']:
                try:
                    with open(f.path, 'r', encoding='utf-8', errors='ignore') as fp:
                        fd['text'] = fp.read()[:300]
                except:
                    pass

            file_data.append(fd)

        prompt_builder = OrganizePromptBuilder(learned_rules)
        system_prompt = prompt_builder.build_system_prompt()
        user_prompt = prompt_builder.build_user_prompt(file_data, learned_rules, include_content)

        response = ai_provider.chat(system_prompt, user_prompt)

        # 记录AI响应
        log_ai_response(provider, model, user_prompt, response, success=False)

        if response.startswith("错误:"):
            log_ai_response(provider, model, user_prompt, response, success=False, error=response)
            return {"error": response}

        try:
            plan = json.loads(response)
        except Exception as e:
            log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))
            pass
        else:
            if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                if learn_mode:
                    for folder in plan.get('folders', []):
                        rule_learner.add_rule(
                            pattern=folder.get('name', ''),
                            action=f"移动到 {folder['name']}",
                            file_count=len(folder.get('files', []))
                        )
                log_ai_response(provider, model, user_prompt, response, success=True)
                return plan

        json_str = _extract_json_object(response)
        if json_str:
            try:
                plan = json.loads(json_str)
                if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                    if learn_mode:
                        for folder in plan.get('folders', []):
                            rule_learner.add_rule(
                                pattern=folder.get('name', ''),
                                action=f"移动到 {folder['name']}",
                                file_count=len(folder.get('files', []))
                            )
                    log_ai_response(provider, model, user_prompt, response, success=True)
                    return plan
            except Exception as e:
                log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))
                pass

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            content = json_match.group(1).strip()
            content = _try_fix_json(content)
            try:
                plan = json.loads(content)
                if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                    if learn_mode:
                        for folder in plan.get('folders', []):
                            rule_learner.add_rule(
                                pattern=folder.get('name', ''),
                                action=f"移动到 {folder['name']}",
                                file_count=len(folder.get('files', []))
                            )
                    log_ai_response(provider, model, user_prompt, response, success=True)
                    return plan
            except Exception as e:
                log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))
                pass

        fixed = _try_fix_json(response)
        if fixed:
            try:
                plan = json.loads(fixed)
                if isinstance(plan, dict) and 'folders' in plan and plan.get('folders') and len(plan.get('folders')) > 0:
                    if learn_mode:
                        for folder in plan.get('folders', []):
                            rule_learner.add_rule(
                                pattern=folder.get('name', ''),
                                action=f"移动到 {folder['name']}",
                                file_count=len(folder.get('files', []))
                            )
                    print(f"AI整理方案生成成功: {len(plan.get('folders', []))}个文件夹")
                    log_ai_response(provider, model, user_prompt, response, success=True)
                    return plan
            except Exception as e:
                print(f"最终修复解析失败: {e}")
                log_ai_response(provider, model, user_prompt, response, success=False, error=str(e))
                pass

        error_msg = f"AI返回格式错误。实际返回: {response[:300]}"
        log_ai_response(provider, model, user_prompt, response, success=False, error=error_msg)
        return {"error": error_msg}

    except Exception as e:
        return {"error": f"服务器错误: {str(e)}"}
    finally:
        db.close()

@app.post("/ai/organize/execute")
async def execute_organize_plan(request: dict):
    """
    执行AI整理方案
    """
    record_id = request.get("record_id")
    plan = request.get("plan", {})
    target_dir = request.get("target_dir", "")
    archive_mode = request.get("archive_mode", "copy")
    archive_smart = request.get("archive_smart", True)

    if not plan or not plan.get("folders"):
        return {"success": False, "message": "无效的整理方案"}

    db = SessionLocal()
    scan_record = db.query(ScanRecord).filter(ScanRecord.id == record_id).first()

    if not scan_record:
        db.close()
        return {"success": False, "message": "未找到扫描记录"}

    if not target_dir:
        target_dir = os.path.join(os.path.dirname(scan_record.scan_path), "AI整理")

    os.makedirs(target_dir, exist_ok=True)
    temp_dir = os.path.join(target_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    results = []
    file_ids_in_plan = set()

    def get_file_ids_from_folder(folder):
        ids = []
        for f in folder.get('files', []):
            if isinstance(f, dict) and 'id' in f:
                ids.append(f['id'])
                file_ids_in_plan.add(f['id'])
        for sub in folder.get('subfolders', []):
            ids.extend(get_file_ids_from_folder(sub))
        return ids

    copy_func = shutil.move if archive_mode == "move" else shutil.copy2

    for folder in plan.get('folders', []):
        if not archive_smart:
            folder_path = target_dir
        else:
            folder_path = os.path.join(target_dir, folder.get('name', '未分类'))
        os.makedirs(folder_path, exist_ok=True)

        for f in folder.get('files', []):
            if isinstance(f, dict) and 'id' in f:
                entry = db.query(FileEntry).filter(FileEntry.id == f['id']).first()
                if entry and os.path.exists(entry.path):
                    unique_path = os.path.join(folder_path, get_unique_filename(folder_path, entry.name))
                    try:
                        copy_func(entry.path, unique_path)
                        results.append(f"✓ {entry.name} → {folder.get('name', '')}")
                        if archive_mode == "move":
                            entry.path = unique_path
                            entry.status = 'archived'
                    except Exception as e:
                        results.append(f"✗ {entry.name}: {str(e)}")

        for sub in folder.get('subfolders', []):
            sub_path = os.path.join(folder_path, sub.get('name', '子文件夹'))
            os.makedirs(sub_path, exist_ok=True)

            for f in sub.get('files', []):
                if isinstance(f, dict) and 'id' in f:
                    entry = db.query(FileEntry).filter(FileEntry.id == f['id']).first()
                    if entry and os.path.exists(entry.path):
                        unique_path = os.path.join(sub_path, get_unique_filename(sub_path, entry.name))
                        try:
                            copy_func(entry.path, unique_path)
                            results.append(f"✓ {entry.name} → {sub.get('name', '')}")
                            if archive_mode == "move":
                                entry.path = unique_path
                                entry.status = 'archived'
                        except Exception as e:
                            results.append(f"✗ {entry.name}: {str(e)}")

    db.commit()

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)

    archived_count = len([r for r in results if r.startswith('✓')])
    db.close()

    return {
        "success": True,
        "message": f"整理完成！已处理 {archived_count} 个文件",
        "details": results
    }

# ==================== 标签与搜索 API ====================

@app.post("/ai/tag/batch")
async def batch_generate_tags(request: dict):
    """
    批量为扫描记录下的文件生成AI标签
    """
    record_id = request.get("record_id")
    file_ids = request.get("file_ids")
    provider = request.get("provider", "ollama")
    api_key = request.get("api_key")
    model = request.get("model")
    base_url = request.get("base_url")

    if not record_id and not file_ids:
        raise HTTPException(status_code=400, detail="必须提供record_id或file_ids")

    db = SessionLocal()
    ai_provider = get_ai_provider(provider, api_key, model, base_url)
    tagger = AITagger(ai_provider)

    async def generate():
        try:
            if file_ids:
                if len(file_ids) > 500:
                    all_files = []
                    for i in range(0, len(file_ids), 500):
                        batch = file_ids[i:i+500]
                        batch_files = db.query(FileEntry).filter(FileEntry.id.in_(batch)).all()
                        all_files.extend(batch_files)
                else:
                    all_files = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
            else:
                all_files = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()

            # 过滤掉已标记的文件，只处理tag_status != 'ready'的文件
            all_files = [f for f in all_files if f.tag_status != 'ready']

            total = len(all_files)
            if total == 0:
                yield _send_sse({"type": "done", "message": "没有文件需要处理"})
                return

            success_count = 0

            # 逐文件处理，实时更新进度
            for idx, file_entry in enumerate(all_files):
                yield _send_sse({
                    "type": "progress",
                    "current": idx + 1,
                    "total": total,
                    "file_name": file_entry.name,
                    "percentage": int((idx + 1) * 100 / total)
                })

                # 检查是否是非工作相关文件（小说、素材等）
                is_non_work = tagger.is_non_work_file(file_entry.name)
                if is_non_work:
                    # 非工作文件：给简单标签，不调用AI
                    simple_tags = tagger.generate_simple_tags(file_entry.name, file_entry.path)
                    save_tags_to_db(db, file_entry.id, simple_tags, source='rule')
                    success_count += 1
                    yield _send_sse({
                        "type": "info",
                        "message": f"快速标记（非工作文件）: {file_entry.name}"
                    })
                    continue

                # 检查是否有相似文件已打标签（复用标签）
                similar_tags = tagger.find_similar_file_tags(db, file_entry.name)
                if similar_tags:
                    # 复用相似文件的标签
                    tagger.apply_tags_from_similar(db, file_entry, similar_tags)
                    success_count += 1
                    yield _send_sse({
                        "type": "info",
                        "message": f"复用标签: {file_entry.name}"
                    })
                    continue

                # 正常AI打标签
                result = await asyncio.to_thread(tagger.generate_tags, file_entry.name, file_entry.path)
                if result and not result.get("error"):
                    save_tags_to_db(db, file_entry.id, result, source='ai')
                    success_count += 1
                else:
                    file_entry.tag_status = 'failed'
                    db.commit()

            yield _send_sse({
                "type": "done",
                "total": total,
                "success": success_count
            })

        except Exception as e:
            yield _send_sse({"type": "error", "message": str(e)})
        finally:
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })


@app.post("/ai/embed/batch")
async def batch_generate_embeddings(request: dict):
    """
    批量为文件生成Embedding向量
    """
    record_id = request.get("record_id")
    file_ids = request.get("file_ids")
    base_url = request.get("base_url", "http://localhost:11434")

    if not record_id and not file_ids:
        raise HTTPException(status_code=400, detail="必须提供record_id或file_ids")

    db = SessionLocal()
    embedder = OllamaEmbedder(base_url)

    if not embedder.is_available():
        db.close()
        return {"error": "Ollama服务不可用，请确保Ollama正在运行"}

    async def generate():
        try:
            if file_ids:
                if len(file_ids) > 500:
                    all_files = []
                    for i in range(0, len(file_ids), 500):
                        batch = file_ids[i:i+500]
                        batch_files = db.query(FileEntry).filter(FileEntry.id.in_(batch)).all()
                        all_files.extend(batch_files)
                else:
                    all_files = db.query(FileEntry).filter(FileEntry.id.in_(file_ids)).all()
            else:
                all_files = db.query(FileEntry).filter(FileEntry.scan_record_id == record_id).all()

            # 过滤掉已有embedding的文件，只处理没有向量的文件
            all_files = [f for f in all_files if not f.embedding_vector]

            total = len(all_files)
            if total == 0:
                yield _send_sse({"type": "done", "message": "没有文件需要处理"})
                return

            success_count = 0
            for idx, file_entry in enumerate(all_files):
                yield _send_sse({
                    "type": "progress",
                    "current": idx + 1,
                    "total": total,
                    "file_name": file_entry.name,
                    "percentage": int((idx + 1) * 100 / total)
                })

                embedding = embedder.generate_file_embedding(
                    file_entry.name,
                    file_entry.path,
                    file_entry.content_summary
                )

                if embedding:
                    save_embedding_to_db(db, file_entry.id, embedding)
                    success_count += 1

            yield _send_sse({
                "type": "done",
                "total": total,
                "success": success_count
            })

        except Exception as e:
            yield _send_sse({"type": "error", "message": str(e)})
        finally:
            db.close()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no"
    })


@app.get("/tags")
async def get_all_tags(category: str = None, min_usage: int = 0):
    """
    获取所有标签
    """
    db = SessionLocal()
    search_engine = TagSearchEngine(db)
    tags = search_engine.get_all_tags(category, min_usage)
    db.close()
    return {"tags": tags}


@app.get("/tags/suggest")
async def get_tag_suggestions(q: str = "", top_k: int = 10):
    """
    获取标签建议
    """
    db = SessionLocal()
    search_engine = TagSearchEngine(db)
    suggestions = search_engine.suggest_tags(q, top_k)
    db.close()
    return {"suggestions": suggestions}


@app.get("/tags/files")
async def get_files_by_tags(
    tags: str = "",
    match_mode: str = "any",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
    """
    按标签搜索文件
    """
    tag_names = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    db = SessionLocal()
    search_engine = TagSearchEngine(db)

    file_ids = search_engine.search_by_tags(tag_names, match_mode=match_mode)

    total = len(file_ids)
    paginated_ids = file_ids[(page-1)*page_size:page*page_size]
    files = get_files_with_tags(db, paginated_ids)

    db.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "files": files
    }


@app.get("/search")
async def search_files(
    q: str = "",
    tags: str = "",
    exclude_tags: str = "",
    file_types: str = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
    """
    混合搜索：关键词 + 标签 + 语义
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    exclude_tag_list = [t.strip() for t in exclude_tags.split(",") if t.strip()] if exclude_tags else []

    db = SessionLocal()
    search_engine = TagSearchEngine(db)

    results = search_engine.hybrid_search(
        q,
        required_tags=tag_list,
        exclude_tags=exclude_tag_list,
        top_k=500
    )

    if file_types:
        try:
            import json
            type_list = json.loads(file_types)
            ext_map = {
                'doc': ['.doc', '.docx', '.wps', '.wpt'],
                'pdf': ['.pdf'],
                'xls': ['.csv', '.xls', '.xlsx', '.xlsb', '.et', '.ets'],
                'ppt': ['.ppt', '.pptx', '.pot', '.potx'],
                'img': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'],
                'txt': ['.txt', '.md', '.log', '.chm']
            }
            allowed_exts = []
            for t in type_list:
                allowed_exts.extend(ext_map.get(t, []))
            results = [r for r in results if r['file']['extension'] in allowed_exts]
        except:
            pass

    total = len(results)
    paginated_results = results[(page-1)*page_size:page*page_size]

    db.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": paginated_results
    }


@app.get("/files/{file_id}/tags")
async def get_file_tags(file_id: int):
    """
    获取文件的所有标签
    """
    db = SessionLocal()
    search_engine = TagSearchEngine(db)
    tags = search_engine.get_file_tags(file_id)
    db.close()
    return {"file_id": file_id, "tags": tags}


@app.put("/files/{file_id}/tags")
async def update_file_tags(file_id: int, request: dict):
    """
    更新文件标签（手动添加标签）
    """
    add_tags = request.get("add_tags", [])
    remove_tag_ids = request.get("remove_tag_ids", [])

    db = SessionLocal()

    file_entry = db.query(FileEntry).filter(FileEntry.id == file_id).first()
    if not file_entry:
        db.close()
        raise HTTPException(status_code=404, detail="文件不存在")

    added = []
    for tag_info in add_tags:
        tag_name = tag_info.get("name", "").strip()
        if not tag_name:
            continue

        category = tag_info.get("category", "其他")

        tag = db.query(Tag).filter(Tag.name == tag_name).first()
        if not tag:
            tag = Tag(name=tag_name, category=category, usage_count=0)
            db.add(tag)
            db.flush()

        tag.usage_count += 1

        existing = db.query(FileTag).filter(
            FileTag.file_id == file_id,
            FileTag.tag_id == tag.id
        ).first()

        if not existing:
            file_tag = FileTag(
                file_id=file_id,
                tag_id=tag.id,
                confidence=1.0,
                source='manual'
            )
            db.add(file_tag)
            added.append(tag_name)

    if remove_tag_ids:
        remove_tags_from_db(db, file_id, remove_tag_ids)

    file_entry.tag_status = 'ready'
    db.commit()
    db.close()

    return {"success": True, "added": added}


@app.delete("/files/{file_id}/tags/{tag_id}")
async def delete_file_tag(file_id: int, tag_id: int):
    """
    删除文件的指定标签
    """
    db = SessionLocal()
    result = remove_tags_from_db(db, file_id, [tag_id])
    db.close()
    return {"success": True, "removed": result}


@app.get("/files/tagged")
async def get_tagged_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    record_id: int = None
):
    """
    获取已标记标签的文件列表
    """
    db = SessionLocal()
    query = db.query(FileEntry).filter(FileEntry.tag_status == 'ready')

    if record_id:
        query = query.filter(FileEntry.scan_record_id == record_id)

    total = query.count()
    files = query.order_by(FileEntry.id.desc()).offset((page-1)*page_size).limit(page_size).all()

    file_ids = [f.id for f in files]
    files_with_tags = get_files_with_tags(db, file_ids)

    db.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "files": files_with_tags
    }

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=56789)