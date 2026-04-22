---
name: "file-indexer"
description: "File Indexer - AI-powered file organization tool. Invoke when user wants to organize files, scan directories, deduplicate files, or needs AI-based file classification."
---

# File Indexer 开发助手

## 项目概述

FileIndexer 是一个基于 AI 的文件整理工具，支持：
- 目录扫描与文件索引
- AI 智能分类整理
- 重复文件检测
- 多 AI 提供商支持 (Ollama, 智谱AI, 通义千问, 文心一言, MiniMax, Kimi, DeepSeek)

## 技术栈

- **后端**: FastAPI + SQLAlchemy + SQLite
- **前端**: Vue 3 (CDN)
- **AI 集成**: REST API 调用多种 AI 服务

## 项目结构

```
backend/
├── main.py           # FastAPI 主应用
├── scanner.py        # 目录扫描模块
├── ai_analyzer.py    # AI 文件分析
├── ai_provider.py    # AI 提供商集成
├── ai_organizer.py   # AI 整理方案执行
├── archiver.py       # 文件归档模块
├── database.py       # 数据库模型
├── models.py         # Pydantic 模型
└── utils.py          # 工具函数

frontend/
├── index.html        # 主界面
└── ai-organize.html   # AI 整理页面
```

## 关键数据库模型

### ScanRecord (扫描记录)
- `id`, `scan_path`, `scan_time`, `total_files`, `total_size`, `status`, `stats_json`

### FileEntry (文件条目)
- `id`, `path`, `name`, `extension`, `size`, `md5`, `created_time`, `modified_time`, `scan_time`, `is_duplicate`, `duplicate_of_id`, `status`, `source_path`, `scan_record_id`

## AI 提示词构建

使用 `OrganizePromptBuilder` 类构建 AI 提示词，包含：
- 系统提示词：定义 AI 角色和输出格式
- 用户提示词：包含文件列表、可选内容预览、学习到的规则

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/scan/start` | 开始扫描 |
| POST | `/scan/stop` | 停止扫描 |
| GET | `/scan/records` | 获取扫描记录 |
| GET | `/scan/records/{id}/files` | 获取记录下的文件 |
| POST | `/ai/organize/generate` | 生成 AI 整理方案 |
| POST | `/ai/organize/execute` | 执行整理方案 |
| POST | `/files/delete` | 删除文件 |

## 开发规范

1. **文档管理**: 所有开发笔记记录在 `note/` 文件夹
2. **版本控制**: 使用 `VERSION.md` 记录版本信息
3. **更新日志**: 按版本创建 `note/CHANGELOG_v*.md`
4. **代码规范**: 不添加注释(除非用户要求)

## 常见任务

### 添加新的 AI 提供商
1. 在 `ai_provider.py` 的 `AVAILABLE_PROVIDERS` 添加配置
2. 在 `DEFAULT_MODELS` 添加默认模型列表
3. 前端 `ai-organize.html` 添加对应选项

### 添加新的文件类型
1. 在 `scanner.py` 的扩展集合中添加 (如 `DOC_EXTENSIONS`)
2. 在 `ai_analyzer.py` 的 `AI_EXT_TYPE_MAP` 中添加类型映射

### 修复数据库问题
- 检查 `database.py` 中的模型定义
- 使用 `db.create_all()` 创建表
- 注意 `unique` 约束避免重复记录

## 错误处理

- AI 超时: 300 秒超时设置
- JSON 解析错误: 增强的解析逻辑，支持 markdown 代码块
- 数据库错误: 检查唯一约束和外键关系
