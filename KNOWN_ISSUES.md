# Known Issues & Roadmap

> 本文档记录 v0.1.0 中已知的限制和尚未完成的工作。
> 每解决一项就勾掉一项，保持与代码同步。

---

## P0 — 必须修复 (影响核心功能正确性)

### ~~1. 图谱仅存在于内存，重启即丢失~~ ✅ 已修复 (c698329)

> 现在 server.py 启动时自动调用 `_rebuild_graph()` 从 SQLite 重建。
> `/status` 和 `/graph` 端点均返回真实数据（当前 164 节点/122 边）。

### ~~2. 流式回答是模拟的，并非真正的 SSE~~ ✅ 已修复 (39b5951)

> `llm.py` 新增 `chat_stream()` 方法，消费 oMLX 真实 SSE 事件流。
> `server.py` 的 `/ask/stream` 改为逐 token 转发。

---

## P1 — 重要功能缺失

### ~~3. 集成测试未实现~~ ✅ 已修复 (19f7531)

> 5 个集成测试全部实现并通过（含真实 LLM 调用）。112 个测试通过。

### 4. 前端未对接图谱数据

**现状**: Streamlit 的 graph 页面调用 `GET /graph`，但 server 的 `graph_store` 从未从 SQLite 加载数据。搜索实体会返回空结果。

**需要做**: 依赖 P0 #1 修复后自动解决。

**涉及文件**: `server.py`, `ui/graph.py`

### 5. 前端未显示文件列表详情

**现状**: files 页面只显示统计卡片，没有列出具体文件及其状态。`GET /status` 返回的是聚合数据，没有文件级别的列表 API。

**需要做**:
- 新增 `GET /files` API 端点，返回文件列表（路径、状态、大小、索引时间、fact 数）
- 前端 files 页面展示可排序/筛选的文件表格

**涉及文件**: `server.py`, `store.py`, `ui/files.py`

### 6. 首次启动时没有索引引导

**现状**: 前端启动后如果数据库为空，显示所有值都是 0，聊天返回"知识库中没有相关信息"。

**需要做**:
- 空状态 UI 引导: "还没有索引任何文件。请编辑 `~/.filekb/config.yaml` 配置监控目录，然后点击「立即索引」或在终端运行 `filekb index --watch`"
- 配置引导向导（可选）

**涉及文件**: `ui/chat.py`, `ui/files.py`

---

## P2 — 性能与体验优化

### 7. FAISS 索引不支持增量更新

**现状**: `IndexFlatIP` 只能全量重建。每次索引后调用 `rebuild_from_sqlite()`。

**需要做**:
- < 100K 向量时 `IndexFlatIP` 全量重建 < 2 秒，足够
- > 1M 向量后切 `IndexIVFFlat`
- 跟踪当前运行规模，加入自动切换逻辑

**涉及文件**: `vector_store.py`

### 8. 中文实体消歧未验证

**现状**: `personalization.py` 的实体合并逻辑已实现，但只通过了单元测试（mock 数据），未在真实中文文档上验证合并效果。

**需要做**:
- 用真实知识库（122 事实）运行 `generate_proposals()`
- 验证 LLM 对中文实体对的判断准确率
- 调优 `similarity_threshold` 和 `auto_approve_threshold`

**涉及文件**: `personalization.py`

### 9. 前端缺乏错误状态和加载体验

**现状**: 聊天页面只有一个 `spinner`，API 失败时显示红色错误框。没有优雅的降级。

**需要做**:
- 加载骨架屏
- 网络断开时的自动重连提示
- 长回答的渐进式渲染（目前一次性显示）

**涉及文件**: `ui/*.py`

### 10. 无 macOS 通知推送

**现状**: `notify.py` 模块存在但未被调用。索引完成后没有系统通知。

**需要做**:
- CLI 索引完成后调用 `notify.py` 发送 macOS 通知
- 通知内容: 成功/失败/跳过的文件计数

**涉及文件**: `notify.py`, `cli.py`

---

## P3 — 远期 (v0.3+)

- OCR 对扫描 PDF 的支持（目前仅图片和 PPT）
- 跨语言实体对齐 ("Alice" ↔ "爱丽丝")
- Web UI 嵌入文件浏览器
- 多知识库切换
- 索引进度条

---

## 解决后更新此文件

```
git commit -m "fix: 图谱持久化 (P0 #1)" -- KNOWLEDGE_ISSUES.md graph_store.py server.py
```
