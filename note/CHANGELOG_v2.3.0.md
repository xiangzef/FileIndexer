# FileIndexer 更新日志

## 版本信息
- **版本号**: v2.3.0
- **版本名称**: AI智能整理增强版
- **发布日期**: 2026-04-21

---

## 1. 新增功能

### 1.1 AI模型选择下拉框
- 根据选择的AI提供商动态加载对应的模型列表
- 支持的AI提供商：
  - **Ollama (本地)**: llama3, llama3.1, llama3.2, mistral, codellama, qwen2.5, phi3, gemma2
  - **智谱AI (Zhipu)**: glm-4, glm-4-flash, glm-4-plus, glm-4v, glm-4v-plus, cogview-3
  - **阿里通义千问 (Qwen)**: qwen3.6-max-preview, qwen3-max, qwen3.5-plus, qwen3.5-flash, qwen-plus, qwen-plus-latest, qwen-max, qwen-max-latest, qwen-turbo, qwen-turbo-latest, qwen3-coder-plus, qwen3-coder-flash, qwen-coder-plus, qwen-coder-turbo, qwen-long, qwq-plus
  - **百度文心一言 (Wenxin)**: ernie-4-8k, ernie-4-32k, ernie-4-128k, ernie-speed-128k, ernie-speed-pro-128k, ernie-lite-pro-8k, ernie-lite-8k, ernie-mini, ernie-turbo-pro-128k, ernie-turbo-8k
  - **MiniMax**: abab6.5s-chat, abab6.5-chat, abab5.5s-chat, abab5.5-chat, abab6-chat, speech-01
  - **月之暗面Kimi**: moonshot-v1-8k, moonshot-v1-32k, moonshot-v1-128k, moonshot-v1-4k
  - **DeepSeek**: deepseek-chat, deepseek-coder, deepseek-reasoner

### 1.2 AI智能文件整理功能
- AI分析并生成文件整理方案
- 支持按主题/项目分类
- 支持学习模式（记住规则）
- 支持包含文件内容分析

### 1.3 文件数量限制保护
- AI处理文件数量限制为300个，避免token溢出

---

## 2. 修改的错误

### 2.1 模型下拉框为空问题
- **原因**: `availableModels` 和 `loadModels` 未在Vue的return语句中导出，导致模板无法访问
- **修复**: 在return语句中添加 `availableModels, loadModels` 导出

### 2.2 模型名称错误导致API 404
- **原因**: localStorage中存储了旧模型名"Qwen 3.6 Plus"，但该名称不在下拉列表中，且API不支持该名称
- **修复**:
  - 移除从localStorage读取aiModel的逻辑，改为空值初始化
  - 切换提供商时自动重置模型选择
  - 简化loadModels为同步函数，直接从本地预设加载

### 2.3 千问模型列表过时
- **原因**: 使用了旧版模型名称（如qwen2.5-7b-instruct），不符合阿里云百炼官方API规范
- **修复**: 更新为官方API模型名称

### 2.4 模型错误提示不明确
- **原因**: 400错误返回原始JSON，用户难以理解
- **修复**: 改进错误提示，明确告知"模型不存在或无访问权限"

---

## 3. 错误分析总结

| 错误现象 | 根本原因 | 解决方案 |
|---------|---------|---------|
| 下拉框一直显示"加载中" | availableModels未导出给模板 | 添加到return语句 |
| API返回404模型不存在 | localStorage缓存了旧模型名 | 清空localStorage，移除读取逻辑 |
| 模型列表为空 | 异步加载失败时未设置默认值 | 改用同步函数，直接用预设值 |
| 切换提供商后模型不变 | 未监听提供商变化 | 添加watch监听并重置模型 |

---

## 4. 未来待办事项

### 4.1 模型列表完善
- [ ] 为每个AI提供商补充缺失的最新模型
- [ ] 添加手动输入模型字符串的能力（允许用户自定义模型名）
- [ ] 定期更新模型列表以保持与官方同步

### 4.2 AI整理记录功能
- [ ] 记录AI返回的整理结果
- [ ] 记录绑定时间戳
- [ ] 关联文件检索记录
- [ ] 支持历史记录查看和导出

### 4.3 其他优化
- [ ] 优化大文件数量（>300）的处理策略，支持分批处理
- [ ] 添加AI整理方案预览和手动调整功能
- [ ] 支持自定义分类规则

---

## 5. 技术债务

- 前端localStorage清理机制不完善
- 模型列表硬编码，需要手动同步官方更新
- 缺乏前端错误边界处理

---

*文档创建时间: 2026-04-21*
*最后更新时间: 2026-04-21*
