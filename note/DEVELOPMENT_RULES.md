# FileIndexer 开发规范

## 文档管理

### note 文件夹用途
所有开发文档、工作记录、会议纪要等非代码文件都必须保存在 `note` 文件夹中。

### 文件命名规范
```
CHANGELOG_v{版本号}_{日期}.md    # 版本更新日志
TASK_{任务名}_{日期}.md           # 任务文档
MEETING_{主题}_{日期}.md          # 会议纪要
```

### 提交规范
- 每次重要更新后更新 `note/CHANGELOG_v*.md`
- 版本号格式: v主版本.次版本.修订号 (如 v2.3.0)

## 代码规范

### 前端
- 使用 Vue 3 Composition API
- ref 和 computed 需要在 setup() 中定义
- **重要**: 所有在模板中使用的变量和函数必须在 return 语句中导出

### 后端
- 使用 FastAPI
- 数据库操作使用 SQLAlchemy ORM
- 错误处理返回明确的 JSON 错误信息

## Bug 修复记录
记录每个 bug 的：现象、原因、解决方案，以便后续避免类似问题。
