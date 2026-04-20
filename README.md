# FileIndexer

文件索引归档工具 - 扫描、索引、查重、归档、智能整理

## 功能特性

### 核心功能
- **目录扫描**：递归扫描指定文件夹
- **文件索引**：记录所有文档、图片、PDF等文件信息
- **MD5 计算**：计算文件哈希用于查重
- **重复检测**：找出内容相同的重复文件
- **归档功能**：复制或移动文件到指定目录（支持 temp 目录中转）

### AI 智能整理
- **项目检测**：自动识别论文、项目、报告、会议等关联文件
- **版本检测**：识别 v1/v2/v3、终稿、草稿等版本信息
- **关键词提取**：智能提取文件名关键词，忽略常用停用词
- **语义分组**：按「项目组 > 版本组 > 关键词组」优先级自动分组
- **智能命名**：根据文件内容自动建议文件夹名称

### 自动模式选择
- **ai_local**：本地 AI（Ollama），完全离线
- **ai_cloud**：云端 AI（OpenAI API），需要网络
- **rule**：纯规则分析，无需网络

### 来源管理
- **状态追踪**：记录文件来源路径（可识别 U盘等可移动设备）
- **挂起/恢复**：将不可用或不需要的文件暂时隐藏
- **批量操作**：按来源路径批量挂起、恢复或删除文件记录

### 支持的文件类型
- 文档: .doc, .docx, .pdf, .txt, .md
- 表格: .csv, .xls, .xlsx
- 图片: .jpg, .jpeg, .png, .gif, .bmp, .tiff, .webp, .ico
- 代码: .py, .js, .java, .cpp, .c, .h, .css, .html, .xml
- 压缩: .zip, .rar

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动服务

```bash
cd backend
python main.py
```

或使用启动脚本：

```bash
start.bat
```

### 3. 访问界面

打开浏览器访问: http://localhost:56789

## AI 模式配置

### 本地模式（推荐，无需网络）
安装 [Ollama](https://ollama.ai/) 后，程序自动检测 localhost:11434

### 云端模式
在界面设置中输入 OpenAI API Key

### 规则模式（完全离线）
无任何外部依赖，使用内置规则进行文件分析

## API 接口

| 方法 | 端点 | 功能 |
|------|------|------|
| POST | `/scan` | 扫描目录 |
| POST | `/duplicate/check` | 检测重复文件 |
| POST | `/ai/analyze` | AI 分析文件 |
| POST | `/ai/archive` | AI 归档整理 |
| POST | `/auto/detect-mode` | 自动检测处理模式 |
| POST | `/check-unavailable` | 检测不可访问文件 |
| GET | `/source-paths` | 获取来源统计 |
| POST | `/source/{path}/suspend` | 挂起来源 |
| POST | `/source/{path}/restore` | 恢复来源 |
| DELETE | `/source/{path}` | 删除来源记录 |

## 技术栈

- 后端: FastAPI + SQLAlchemy + SQLite
- 前端: Vue 3
- AI: 支持 Ollama 本地部署 / OpenAI 云端 API / 纯规则分析