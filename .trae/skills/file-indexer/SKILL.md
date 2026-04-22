---
name: "file-indexer"
description: "File Indexer - AI-powered file organization tool. Invoke when user wants to organize files, scan directories, deduplicate files, or needs AI-based file classification."
---

# File Indexer 开发助手

## 项目概述

FileIndexer 是一个基于 AI 的文件整理工具，支持：
- 目录扫描与文件索引（支持 doc/docx/pdf/csv/xls/xlsx/ppt/pptx/epub 等格式）
- AI 智能分类整理（支持 Ollama/智谱AI/通义千问/文心一言/MiniMax/Kimi/DeepSeek）
- 重复文件检测与归档（MD5 相似度 + 名称相似度）
- 学习用户整理规则，生成个性化整理方案

## 技术栈

- **后端**: FastAPI + SQLAlchemy + SQLite
- **前端**: Vue 3 (CDN)
- **AI 集成**: REST API 调用多种 AI 服务

## 项目结构

```
FileIndexer/
├── backend/
│   ├── main.py           # FastAPI 主应用，所有 API 端点
│   ├── scanner.py        # 目录扫描模块
│   ├── ai_analyzer.py    # AI 文件分析器 (AIAnalyzer 类)
│   ├── ai_provider.py    # AI 提供商集成 (AIProvider 类, PROVIDER_CONFIGS)
│   ├── ai_organizer.py   # AI 整理方案 (OrganizePromptBuilder, LearnedRule)
│   ├── archiver.py       # 文件归档模块 (归档/去重/重命名)
│   ├── database.py       # 数据库模型 (FileEntry, ScanRecord)
│   ├── file_manager.py   # 文件管理 (状态检查/挂起/恢复)
│   ├── auto_mode.py      # 自动模式检测
│   ├── models.py         # Pydantic 模型
│   └── utils.py          # 工具函数
├── frontend/
│   ├── index.html        # 主界面
│   └── ai-organize.html  # AI 整理页面
├── data/                 # SQLite 数据库目录
└── .trae/skills/file-indexer/SKILL.md
```

## 核心数据库模型

### FileEntry (文件条目)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| path | String | 文件路径 (unique) |
| name | String | 文件名 |
| extension | String | 扩展名 |
| size | BigInteger | 文件大小 |
| md5 | String(32) | MD5 哈希 (有索引) |
| content_hash | String(64) | 内容哈希 |
| created_time | DateTime | 创建时间 |
| modified_time | DateTime | 修改时间 |
| scan_time | DateTime | 扫描时间 |
| is_duplicate | Boolean | 是否重复 |
| duplicate_of_id | Integer | 原件 ID |
| status | String | 状态 (available/suspended/archived) |
| source_path | String | 来源路径 |
| scan_record_id | Integer | 关联的扫描记录 |

### ScanRecord (扫描记录)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| scan_path | String | 扫描路径 |
| scan_time | DateTime | 扫描时间 |
| total_files | Integer | 文件总数 |
| total_size | BigInteger | 总大小 |
| status | String | 状态 (active/completed/stopped) |
| stats_json | String | 统计信息 JSON |

## AI 提供商配置 (ai_provider.py)

```python
PROVIDER_CONFIGS = {
    "ollama": {"base_url": "http://localhost:11434/v1", "default_model": "llama3"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "default_model": "glm-4-flash"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen3.5-flash"},
    "wenxin": {"base_url": "https://aip.baidubce.com/rpc/2.0/ai_custom/v1", "default_model": "ernie-speed-128k"},
    "minimax": {"base_url": "https://api.minimax.chat/v1", "default_model": "abab6.5s-chat"},
    "kimi": {"base_url": "https://api.moonshot.cn/v1", "default_model": "moonshot-v1-8k"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat"}
}
```

## API 端点一览

### 扫描相关
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/scan` | 扫描目录，生成 SSE 流 |
| POST | `/scan/stop` | 停止扫描 |
| GET | `/scan-records` | 获取扫描记录列表 |
| GET | `/scan-record/{id}/files` | 获取扫描记录下的文件 |

### 文件管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/files` | 分页查询文件 (支持 extension/is_duplicate/keyword/status/file_type 筛选) |
| GET | `/files/all-ids` | 获取所有文件 ID |
| DELETE | `/files/{id}` | 删除单个文件 |
| POST | `/files/batch-delete` | 批量删除文件 |
| POST | `/files/suspend` | 挂起文件 |
| POST | `/files/restore` | 恢复文件 |

### MD5 与重复
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/compute-md5` | 批量计算 MD5 |
| GET | `/duplicates` | 获取重复文件组 |
| POST | `/deduplicate` | 执行去重 |
| POST | `/rename-by-date` | 按日期重命名重复文件 |
| DELETE | `/duplicates/{id}` | 删除重复文件 |

### 归档
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/archive` | 智能归档 (SSE 流，按类型分类+相似文件组) |
| POST | `/archive-simple` | 简单归档 |

### AI 整理
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/ai/analyze` | AI 分析文件 |
| POST | `/ai/archive` | AI 归档文件 |
| POST | `/ai/organize/plan` | 生成 AI 整理方案 |
| POST | `/ai/organize/execute` | 执行 AI 整理方案 |

### 来源路径管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/source-paths` | 获取所有来源路径统计 |
| POST | `/source/{path}/suspend` | 挂起来源路径 |
| POST | `/source/{path}/restore` | 恢复来源路径 |
| DELETE | `/source/{path}` | 删除来源路径 |

### 其他
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/supported-extensions` | 支持的文件扩展名 |
| GET | `/ai/models` | 获取 AI 模型列表 |
| GET | `/stats` | 获取统计信息 |
| POST | `/check-unavailable` | 检测不可用文件 |
| POST | `/auto/detect-mode` | 自动检测模式 |

## 核心类与函数

### scanner.py
- `scan_directory(db, directory, extensions, progress_callback)` - 扫描目录，返回 Generator
- `compute_md5_batch(db, file_ids)` - 批量计算 MD5
- `find_duplicates(db, scan_record_id)` - 查找重复文件
- `stop_scan()` - 停止扫描
- `ALL_SUPPORTED` - 支持的扩展名集合

### archiver.py
- `archive_files_smart(db, file_ids, target_dir, mode)` - 智能归档，按类型分类+相似文件组
- `deduplicate_files(db)` - 标记重复文件
- `rename_duplicates_by_date(db)` - 按日期重命名
- `group_similar_files(files)` - 按名称相似度分组
- `get_base_name(name)` - 提取文件名主体

### ai_analyzer.py
- `AIAnalyzer` - AI 分析器类
  - `analyze_file_name(filename)` - 分析文件名
  - `analyze_file_content(file_path)` - 分析文件内容
  - `group_files_by_semantic(files)` - 语义分组
  - `suggest_folder_name(files)` - 建议文件夹名
  - `generate_summary(files)` - 生成摘要
- `analyze_files(db, file_ids, ai_provider)` - 分析文件入口
- `ai_archive_files(db, file_ids, target_dir, mode, ai_provider)` - AI 归档

### ai_organizer.py
- `OrganizePromptBuilder` - 整理方案 Prompt 构建器
  - `build_system_prompt()` - 系统提示词
  - `build_user_prompt(files, learned_rules, include_content)` - 用户提示词
  - `extract_base_name(filename)` - 提取文件名主体
  - `detect_version_group(files)` - 检测版本组
  - `detect_similar_names(files, threshold)` - 检测相似名称
  - `get_file_type(extension)` - 获取文件类型
- `LearnedRule` - 学习规则管理
  - `add_rule(pattern, action, file_count)` - 添加规则
  - `get_recent_rules(limit)` - 获取最近规则

### ai_provider.py
- `AIProvider` - AI 提供商类
  - `chat(system_prompt, user_prompt)` - 调用 AI 对话
  - `_rule_chat()` - 规则模式回复
- `get_ai_provider(provider, api_key, model, base_url)` - 工厂函数

## 开发规范

1. **代码规范**: 不添加注释 (除非用户要求)
2. **文档管理**: 所有开发笔记记录在 `note/` 文件夹
3. **版本控制**: 使用 `VERSION.md` 记录版本信息
4. **更新日志**: 按版本创建 `note/CHANGELOG_v*.md`

## 常见任务

### 添加新的 AI 提供商
1. 在 `ai_provider.py` 的 `PROVIDER_CONFIGS` 添加配置
2. 前端 `ai-organize.html` 添加对应选项

### 添加新的文件类型
1. 在 `scanner.py` 的扩展集合中添加 (如 `DOC_EXTENSIONS`)
2. 在 `ai_analyzer.py` 的 `AI_EXT_TYPE_MAP` 中添加类型映射
3. 在 `main.py` 的 `type_map` 中添加筛选映射

### 修复数据库问题
- 检查 `database.py` 中的模型定义
- 使用 `db.create_all()` 创建表
- 注意 `unique` 约束避免重复记录
- 数据库迁移函数 `migrate_database()` 会自动添加缺失列

## 错误处理

- AI 超时: 300 秒超时设置
- JSON 解析错误: 增强的解析逻辑，支持 markdown 代码块和多种位置变体
- 数据库错误: 检查唯一约束和外键关系
- 文件不存在: 自动标记为 unavailable 状态

## 启动方式

```bash
cd FileIndexer
python run.py
# 或直接
cd backend
python -m uvicorn main:app --reload --host 127.0.0.1 --port 56789
```

## 前端文件

- `frontend/index.html` - 主界面 (文件列表、扫描、统计)
- `frontend/ai-organize.html` - AI 整理页面
