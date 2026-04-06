# FileIndexer

文件索引归档工具 - 扫描、索引、查重、归档

## 功能特性

- 目录扫描：递归扫描指定文件夹
- 文件索引：记录所有文档、图片、PDF等文件信息
- MD5 计算：计算文件哈希用于查重
- 重复检测：找出内容相同的重复文件
- 按日期重命名：对重复文件按修改日期重命名
- 归档功能：复制或移动文件到指定目录

## 支持的文件类型

- 文档: .doc, .docx
- 表格: .csv, .els, .elsx
- 图片: .jpg, .jpeg, .png, .gif, .bmp, .tiff, .webp, .ico
- PDF: .pdf

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

打开浏览器访问: http://localhost:5678

## 技术栈

- 后端: FastAPI + SQLAlchemy + SQLite
- 前端: Vue 3
