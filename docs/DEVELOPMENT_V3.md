# FileKB — 系统设计文档

> **日期**: 2026-06-19
> **状态**: 最终版本
>
> **文件定位**: 本文件是 FileKB 项目的**唯一权威设计文档**，整合了产品需求分析、架构审查、中文策略调研、个性化方案调研的全部结论。目标读者：
> 1. **人类专家评审** — 可以据此完整理解项目架构、权衡决策和实现策略
> 2. **开发人员/Coding Agent** — 可以据此独立完成系统开发，无需参考其他文档
>
> **阅读指引**:
> - 第 1-3 节：项目概览、设计目标、架构决策（**评审重点**）
> - 第 4-5 节：系统架构图和数据模型（**理解全局结构**）
> - 第 6 节：前端交互设计
> - 第 7-8 节：后端模块设计和 Prompt 工程（**开发实施依据**）
> - 第 9 节：中文实体抽取方案（**核心路径**）
> - 第 10-11 节：可靠性和个性化
> - 第 12-13 节：API 规范和配置参考（**开发和运维**）
> - 第 14-15 节：测试策略和已知限制（**质量保证**）
> - 第 16 节：附录（引用纠正、许可证合规、同类项目陷阱、内存预算）

---

## 目录

1. [项目概览](#1-项目概览)
2. [设计目标与非目标](#2-设计目标与非目标)
3. [架构决策记录 (ADR)](#3-架构决策记录-adr)
4. [系统架构](#4-系统架构)
5. [数据模型](#5-数据模型)
6. [前端交互设计](#6-前端交互设计)
7. [后端模块设计](#7-后端模块设计)
8. [Prompt 工程](#8-prompt-工程)
9. [中文实体抽取方案](#9-中文实体抽取方案)
10. [可靠性工程](#10-可靠性工程)
11. [个性化与反馈闭环](#11-个性化与反馈闭环)
12. [API 规范](#12-api-规范)
13. [配置参考](#13-配置参考)
14. [测试策略](#14-测试策略)
15. [已知限制与路线图](#15-已知限制与路线图)
16. [附录](#16-附录)

---

## 1. 项目概览

### 1.1 一句话定义

FileKB 是一个**文件驱动、全本地、零云依赖**的个人知识库系统。它监控本地目录中的文件变化（增/删/改），自动提取结构化知识（实体-关系三元组），构建知识图谱，并支持自然语言问答，所有答案带源文件追溯。

### 1.2 核心价值主张

| 价值 | 实现方式 |
|------|---------|
| 零手动整理 | 文件放入监控目录 → 自动解析 → 自动提取 → 自动入库 |
| 全可追溯 | 每条知识标注来源文件和原文片段，永不虚构来源 |
| 100% 本地 | SQLite + FAISS + NetworkX + oMLX(Qwen3.6-35B) — 数据永不离开本机 |
| 关系发现 | 知识图谱揭示跨文件隐性关联，grep 做不到的发现能力 |
| 零维护个性化 | 系统从隐性/显性反馈自动学习，不需要用户手动调参 |

### 1.3 硬件基准

- **目标平台**: Apple Silicon M5 Max + 128GB unified memory
- **LLM 服务**: oMLX（基于 MLX，Apple Silicon 原生框架）+ Qwen3.6-35B-A3B-MTP
- **Embedding 模型**: BAAI/bge-m3（1024 维，中英双语，sentence-transformers）
- **内存预算**: Qwen3.6(4-bit ~20GB) + bge-m3(~2GB) + DeepKE(~0.4GB) + 运行时(~2-5GB) = **~26-30GB 总计，占 128GB 的 23%**
- **其他平台**: macOS Intel、Linux 也支持（均通过 faiss-cpu + CPU embedding）

### 1.4 目标用户

- 独立研究者（管理数十到数百篇论文和笔记）
- 知识工作者（十年积累、数千文件，需要跨文件关联查询）
- 内容创作者（管理研究素材、草稿和引用源）
- **核心特征**: >80% 内容为中文，中文支持是核心路径而非锦上添花

---

## 2. 设计目标与非目标

### 2.1 核心设计目标

| 目标 | 实现 | 优先级 |
|------|------|--------|
| 零云依赖 | SQLite + FAISS + NetworkX + 本地 oMLX | P0 |
| 文件系统原生 | 监控真实目录，文件增删改驱动 KB 更新 | P0 |
| 溯源优先 | 每条 fact 标注来源文件和原文片段 | P0 |
| 增量更新 | 夜间批处理只处理变更文件，未改动文件跳过 | P0 |
| 无人值守可靠性 | 一个 chunk 失败不能杀死一个文件，一个文件失败不能杀死一次运行 | P0 |
| 零维护个性化 | 从隐式/显式信号自动学习，无需用户手动管理 | P1 |
| 中文一级支持 | >80% 中文内容的质量得到保障 | P0 |
| 可视化交互 | Web UI 提供聊天、图谱浏览、实体审核 | P1 |
| 开源就绪 | MIT 许可，pip 安装，单配置启动 | P1 |

### 2.2 明确非目标

- 实时文件监控（fsevents）—— 夜间批处理足够个人使用
- 多用户支持 —— 单用户单机
- 云同步 —— 用户自行备份 `~/.filekb/`
- 图片/视频/音频 —— 仅文本文件
- OCR 扫描 PDF —— 需要有文本层的 PDF
- 网页内容抓取 —— 仅本地文件
- 移动端 —— 仅桌面端
- 插件系统 —— 固定管道
- 支持非 OpenAI 兼容 API 以外的 LLM 后端

---

## 3. 架构决策记录 (ADR)

### ADR-1: 为什么用 SQLite 而不是 PostgreSQL/MySQL？

**决策**: SQLite（WAL 模式）。

**原因**: 个人知识库不需要客户端-服务器数据库。SQLite 零配置、零运维、单文件备份，WAL 模式支持并发读写，FTS5 内建全文搜索且支持 jieba 中文分词。10GB 以内的数据库 SQLite 性能不输 PostgreSQL。

---

### ADR-2: 为什么用 FAISS IndexFlatIP 而不是 HNSW？

**决策**: `IndexFlatIP`（内积精确搜索）。

**原因**: 个人 KB 规模不会超过 10 万条 fact。`IndexFlatIP` 在 10 万向量上搜索 <10ms。HNSW 的优势在百万级向量时才显现，且带来近似搜索的不确定性——与溯源优先的设计目标冲突。随着规模增长，切换 `IndexIVFFlat` 或 `IndexHNSWFlat` 是一行代码的改动。

---

### ADR-3: 为什么用 NetworkX DiGraph 而不是 Neo4j/图数据库？

**决策**: NetworkX 内存有向图。

**原因**: 个人 KB 的实体数量和关系密度远未达到需要专用图数据库的程度。NetworkX 纯 Python、零安装、与 FAISS/SQLite 同进程运行。图在内存中重建 <2 秒。如果未来需要持久化图，导出 GraphML 即可。

---

### ADR-4: 为什么用 Streamlit 而不是 React/Next.js？

**决策**: Streamlit 纯 Python Web UI。

**原因**:
- 项目全部使用 Python，Streamlit 消除 JavaScript 技术栈
- 单人可同时维护后端和前端
- Streamlit 有成熟的图可视化组件（streamlit-extras sigma_graph、yfiles-graphs-for-streamlit）
- FastAPI + Streamlit 是社区验证的成熟模式
- RAGFlow/Dify 的 React 前端有数百个组件，对个人 KB 是过度工程

**参考**: LightRAG 最初也是 Streamlit 原型，后来才转向 React。FileKB 不需要 LightRAG 规模的 WebUI。

**限制**: Streamlit 不适合复杂交互（如拖拽图编辑）。如果未来需要，FastAPI 层的 REST API 可以直接对接任何前端框架。

---

### ADR-5: 为什么用 FastAPI 做 REST API 层？

**决策**: FastAPI 作为 Streamlit 和核心引擎之间的中间层。

**原因**:
- 提供明确的 API 契约，前端和后端解耦
- 异步支持（流式回答 `/ask/stream`）
- 自动生成 OpenAPI 文档（Swagger UI）
- 未来如果切换前端框架（如 React），API 层完全不变
- 被 RAGFlow、Dify、LightRAG 等成熟系统普遍采用

**架构模式**: Streamlit UI → `requests` 调用 → FastAPI → 核心引擎（Store/Query/LLM）
- Streamlit 页面不应该直接操作数据库或调用 LLM
- 所有业务逻辑通过 FastAPI 端点暴露
- 流式响应通过 `httpx.stream()` 或 `EventSource` 消费

---

### ADR-6: 为什么 embedding 模型用 CPU/MPS 而不是 GPU/Neural Engine？

**决策**: bge-m3 通过 sentence-transformers 在 CPU 或 MPS 上运行。

**原因**: embedding 推理是轻量计算（1024 维输出），CPU 足够。MPS 在 sentence-transformers v4+ 上稳定性有改善但仍是可选项。与 oMLX 的 LLM 推理隔离，避免 GPU/Neural Engine 资源竞争。128GB 统一内存下完全没有内存压力（bge-m3 仅 ~2GB）。

---

### ADR-7: 为什么增量更新的基础是 SHA256 文件哈希？

**决策**: SHA256 内容哈希 + 三路对比（新增/修改/删除/未变）。

**原因**: 文件修改时间不可靠（git checkout 会重置 mtime），文件名不可靠（重命名不等于内容变化）。SHA256 是确定性的内容身份标识。实现简单，性能足够（10K 文件扫描 <30 秒）。

---

### ADR-8: 为什么集成成熟库而不是手写？

**决策**: 对已知问题的成熟解决方案，直接集成 MIT 许可的库。

**边界**:
- **直接集成**: `tenacity`（指数退避重试）、`pydantic`（配置校验）
- **借用模式但重写**: 反馈加权排序（模式来自 RAGFlow PR #12689，为 SQLite/FAISS 重写）、实体合并工作流（模式来自 sift-kg，为 FileKB 数据模型重写）
- **仅参考**: KAG 的逻辑形式求解器、Mem0 的云端多租户记忆层

---

### ADR-9: 为什么全自动实体消歧而非手动管理？

**决策**: 实体合并由系统自动提议。仅在置信度低于阈值时才请求人工审核。批准的合并批量执行，不阻塞索引。

**原因**: 用户偏好最小干预。默认模式是"有把握就自动合并，不确定才问我"。

---

### ADR-10: 移除预检 token 计数，采用响应式溢出检测

**决策**: FileKB **不进行**任何形式的预检 token 计数（无论是 `tiktoken` 还是 `transformers` 的 Qwen tokenizer）。溢出检测完全依赖 oMLX API 返回的 `finish_reason == "length"`，由韧性层触发 `adaptive_resplit()` 并重试。

**原因**:
1. **tiktoken 与 Qwen tokenizer 不兼容** — 实测证据：同一文本在 Qwen3.6-35B 上消耗 14,445 tokens，在 Qwen3-Coder-Next 上消耗 10,841 tokens（33% 差异）。任何本地 tokenizer 都无法可靠预测 oMLX 的实际 token 消耗。
2. **分裂器已提供结构性保证** — `max_chars_per_chunk = 24000` 确保中文文本的 token 数远低于 Qwen3.6 的 32K 上下文窗口（中文文本 ~12,000–16,000 tokens/chunk）。在正常操作下，chunk 不会溢出。
3. **响应式检测更精确** — `finish_reason == "length"` 是 100% 确定的溢出信号，无误报/漏报。相比之下，本地 tokenizer 的估算总有误差。
4. **消除重量级依赖** — 不依赖 `tiktoken`（OpenAI 专有）或 `transformers`（~2GB），安装体积显著减小。
5. **溢出成本可接受** — 如果罕见地发生溢出，浪费一次 LLM 调用（oMLX 测试数据显示 ~4 秒），远低于引入 2GB 依赖的维护成本。

---

## 4. 系统架构

### 4.1 分层架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        INTERFACE LAYER                                 │
│  ┌──────────────────┐  ┌──────────────────────┐  ┌────────────────┐  │
│  │   CLI (click)    │  │  Web UI (Streamlit)  │  │  REST API      │  │
│  │                   │  │                      │  │  (FastAPI)     │  │
│  │  index/ask/graph  │  │  Chat / Graph /      │  │  /ask /graph   │  │
│  │  /status/ui       │  │  Entities / Status   │  │  /index /facts │  │
│  └────────┬─────────┘  └──────────┬───────────┘  └───────┬────────┘  │
│           │                       │                      │           │
├───────────┼───────────────────────┼──────────────────────┼───────────┤
│           ▼                       ▼                      ▼           │
│                 PERSONALIZATION LAYER                                  │
│     Preference Memory → Feedback Scorer → Entity Resolver             │
│                                                                        │
├───────────────────────────────────────────────────────────────────────┤
│                       QUERY ENGINE (query.py)                          │
│     Vector(FAISS) → Graph(NetworkX) → FTS(SQLite) → Merge → LLM       │
│                                                                        │
├───────────────────────────────────────────────────────────────────────┤
│                        STORAGE LAYER                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐    │
│  │   SQLite     │  │   FAISS      │  │  NetworkX (memory)       │    │
│  │  + user_*    │  │  IndexFlatIP │  │  DiGraph + merge track   │    │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘    │
│         │                 │                       │                   │
├─────────┼─────────────────┼───────────────────────┼───────────────────┤
│         ▼                 ▼                       ▼                   │
│                   RESILIENCE LAYER                                     │
│  ┌─────────────────────────┐  ┌──────────────────────────────────┐     │
│  │ finish_reason=="length"  │  │  Checkpoint / DLQ                │     │
│  │ → adaptive_resplit()    │  │  Dead Letter Queue               │     │
│  └──────────┬──────────────┘  └────────────┬─────────────────────┘     │
│             │                             │                             │
├─────────────┼─────────────────────────────┼─────────────────────────────┤
│         ▼                ▼                       ▼                    │
│                      EXTRACTION LAYER                                  │
│   Multi-round LLM → Incremental JSON → Semantic Dedup →               │
│   Chinese branch: detect_chinese() → extract_zh.txt → DeepKE filter   │
│                                                                        │
├───────────────────────────────────────────────────────────────────────┤
│                         FILE LAYER                                     │
│   Watcher (hash+diff) → Parser (markitdown+Docling) → Splitter        │
│                                                                        │
├───────────────────────────────────────────────────────────────────────┤
│                         MODEL LAYER                                    │
│  ┌────────────────────────┐    ┌────────────────────────────────┐    │
│  │  LLM Client (llm.py)   │    │  Embedding (embed.py)          │    │
│  │  + tenacity retry      │    │  bge-m3 / sentence-transform   │    │
│  │  + finish_reason 检测   │    │  1024-dim normalized vectors   │    │
│  │  + adaptive_resplit    │    │  + MPS/CPU可选                 │    │
│  └────────────────────────┘    └────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────┘
```

### 4.2 数据流：索引（增量）

```
[配置的监控目录]
        │
        ▼
  scan_directories()          ← 遍历所有文件
        │
        ▼
  detect_changes()            ← SHA256 三路对比
        │
  ┌─────┼─────┐
  ▼     ▼     ▼
[ADD]  [MOD]  [DEL]  [未变更: 跳过]
  │     │      │
  │     │      └→ mark_file_deleted()
  │     │
  ▼     ▼
  parse_file()                ← markitdown → Docling → pymupdf
        │
        ▼
  chunk_text()                ← 自然边界 + 指数回退分割
        │
        ▼
  ┌──────────────────┐
  │ detect_chinese()  │      ← >50% CJK → 使用 extract_zh.txt
  └──────┬───────────┘
         ▼
  extract_facts() × k轮       ← 多轮 LLM 提取 + KV-cache 复用
        │
        ▼
  incremental JSON parse      ← 正则增量解析，容错 JSON 截断
        │
        ▼
  semantic_dedup()            ← embedding 余弦相似度去重
        │
        ▼
  commit_chunk()              ← 原子写入 SQLite + FTS5 自动更新
        │
        ▼
  ┌──────────────┐
  │ DeepKE filter │           ← 中文内容实体边界后处理校验
  └──────────────┘
        │
        ▼
  文件级 checkpoint           ← SQLite 事务: UPDATE files SET status='done'
        │
        ▼
  所有文件处理完毕 →
  rebuild_indices()           ← FAISS + NetworkX 内存图重建
        │
        ▼
  entity_resolution_batch()   ← 自动实体合并
        │
        ▼
  notify_index_complete()     ← macOS 通知 + 失败汇总
```

### 4.3 数据流：查询

```
[用户问题]
        │
        ▼
  embed_single(question)      ← bge-m3 嵌入
        │
        ▼
  ┌─────┼─────┐
  ▼           ▼
FAISS搜索    SQLite FTS5搜索
(k×2)        (top 10)
  │           │
  └─────┬─────┘
        ▼
  加权合并 + user_preference boost
  score = α·vector + β·graph + γ·fts + δ·log(user_score)
        │
        ▼
  Top-k 种子 fact
        │
        ▼
  图扩展 (1-hop from seed entities)
        │
        ▼
  收集上下文 + preference memories
        │
        ▼
  LLM.generate_answer()       ← 带源文件引用的自然语言回答
        │
        ▼
  响应 {answer, sources[], related_facts[], feedback_prompt}
```

---

## 5. 数据模型

### 5.1 现有表（V1/V2 保留）

```sql
-- 目录配置
CREATE TABLE directories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    recursive BOOLEAN DEFAULT 1,
    exclude_patterns TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now'))
);

-- 文件注册
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    directory_id INTEGER REFERENCES directories(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    error_msg TEXT,
    file_size INTEGER,
    mtime REAL,
    indexed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 文本块
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);

-- 知识事实（三元组）
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    chunk_id INTEGER REFERENCES chunks(id),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    title TEXT,
    description TEXT,
    evidence_span TEXT,
    confidence INTEGER DEFAULT 50,
    tags TEXT DEFAULT '[]',
    embedding BLOB,
    user_score REAL DEFAULT 1.0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);

-- 全文索引（FTS5）
CREATE VIRTUAL TABLE facts_fts USING fts5(
    subject, predicate, object, title, description,
    content='facts',
    content_rowid='id',
    tokenize='jieba'
);

-- 索引运行记录
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT DEFAULT 'running',
    files_total INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    facts_added INTEGER DEFAULT 0,
    facts_removed INTEGER DEFAULT 0,
    started_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT
);
```

### 5.2 新增表（V2）

```sql
-- 用户反馈
CREATE TABLE user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER REFERENCES facts(id) ON DELETE CASCADE,
    feedback_type TEXT NOT NULL CHECK(feedback_type IN ('positive', 'negative')),
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 用户偏好（自动从查询日志推导）
CREATE TABLE user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pref_key TEXT NOT NULL,
    pref_value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 实体合并提案
CREATE TABLE entity_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    proposed_name TEXT,
    confidence REAL NOT NULL,
    llm_response TEXT,
    status TEXT DEFAULT 'proposed',
    reviewed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 失败块队列（Dead Letter Queue）
CREATE TABLE failed_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id INTEGER REFERENCES chunks(id),
    file_id INTEGER REFERENCES files(id),
    error_class TEXT NOT NULL,
    error_msg TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    next_retry_at TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);
```

### 5.3 修改的列（V2 对已有表的增强）

- **chunks**: 新增 `status` 列 — `pending` / `processing` / `done` / `failed` / `resplit`
- **files**: `status` 扩展枚举 — `pending` / `parsing` / `chunking` / `extracting` / `indexing` / `done` / `failed_partial` / `failed` / `skipped`
- **facts**: 新增 `user_score` 列（FLOAT, 默认 1.0，反馈加权修正）

### 5.4 数据库迁移策略

使用简单的版本号检查：
```python
# store.py 初始化时
PRAGMA user_version;  -- 读取当前版本
# 如果版本 < 2:
#   执行 ALTER TABLE + CREATE TABLE 语句
#   更新 PRAGMA user_version = 2;
```

升级是幂等的——所有 DDL 使用 `IF NOT EXISTS` 或 `try/except` 包裹。

---

## 6. 前端交互设计

### 6.1 前端技术选型

**决策**: Streamlit + streamlit-extras（sigma_graph 组件）+ yfiles-graphs-for-streamlit（备选图可视化）

**原因**:
- 纯 Python，消除 JavaScript 工具链
- 单人可维护全栈
- 成熟的图可视化组件生态
- FastAPI 作为中间层提供 REST API，Streamlit 通过 API 调用后端，前端和后端解耦
- 被社区验证的 FastAPI + Streamlit 架构模式

**备选路径**: 如果未来需要更丰富的交互（拖拽编辑图），FastAPI 层允许直接对接 React/Sigma.js 前端，API 契约不变。

### 6.2 参考系统分析

调研了以下成熟知识库系统的前端设计：

| 系统 | 技术栈 | 核心页面 | 可借鉴之处 |
|------|--------|---------|-----------|
| **LightRAG WebUI** | React 19 + TypeScript + Zustand + Sigma.js | 文档管理、图谱可视化、检索测试、API 文档 | Sigma.js WebGL 图渲染、Zustand 状态管理、节点属性面板 |
| **RAGFlow** | React 18 + UmiJS + Ant Design + Tailwind + React Flow | 知识库管理、文档上传、Agent 画布、Admin UI | Admin 仪表盘（服务状态、用户管理）、批量操作 UI、实时任务监控 |
| **Dify** | Next.js + React + Jotai + Tailwind | Chat、Prompt IDE、工作流画布、知识库 | 流式 Chat 体验、反馈按钮（👍/👎）内联、多模型切换 UI |
| **AnythingLLM** | Vite + React + Tailwind + Node.js | Chat 中心化界面、Workspace 管理、Slash 命令 | 极简 Chat 设计、拖拽上传、工作区隔离、CSS 变量主题系统 |

### 6.3 FileKB 前端页面设计

FileKB 采用 **侧边栏导航 + 多页面** 的 Streamlit 布局。共 5 个页面：

#### 页面 1: Chat（默认首页）

**设计参考**: AnythingLLM 极简 Chat + Dify 流式反馈

**功能**:
- 对话式查询输入框（支持 Enter 发送）
- 流式回答显示（逐 token 渲染）
- 每条回答附带：
  - 来源文件列表（可点击展开/折叠）
  - 相关 fact 卡片（subject → predicate → object）
  - 反馈按钮 `[👍 有帮助] [👎 无帮助]`
- 对话历史（保存在 Streamlit session_state，单会话有效）
- 空状态提示："还没有索引文件。运行 `filekb index --watch` 来开始。"

**技术实现**:
```python
# ui/chat.py
import streamlit as st
import requests

API_BASE = "http://localhost:8000"

st.text_input("Ask anything about your knowledge base...", key="question")

if st.session_state.question:
    with st.chat_message("user"):
        st.write(st.session_state.question)
    with st.chat_message("assistant"):
        # 使用 httpx 流式消费 FastAPI /ask/stream
        with requests.post(f"{API_BASE}/ask/stream",
                          json={"question": question},
                          stream=True) as r:
            # 逐 token 渲染
            ...
    # 反馈按钮
    col1, col2 = st.columns(2)
    with col1:
        if st.button("👍 有帮助"):
            requests.post(f"{API_BASE}/feedback", ...)
    with col2:
        if st.button("👎 无帮助"):
            requests.post(f"{API_BASE}/feedback", ...)
```

#### 页面 2: Knowledge Graph（图谱浏览）

**设计参考**: LightRAG Sigma.js 交互图 + RAGFlow G6 力图

**功能**:
- 实体搜索框（支持模糊搜索）
- 交互式图谱可视化（zoom、pan、节点拖拽）
- 节点染色：按实体类型（人物/组织/项目/日期/指标）
- 边标签：显示 predicate 关系名
- 点击节点：弹出属性面板，显示：
  - 实体名、类型、连接度数
  - 关联的 fact 列表（subject/predicate/object + 来源文件）
  - "扩展到 2-hop" 按钮
- 布局选项：力导向布局（默认）/ 圆形布局
- 过滤：按实体类型、按连接度数阈值

**技术实现**:
```python
# ui/graph.py
from streamlit_extras import sigma_graph

# 从 FastAPI GET /graph 获取节点和边
resp = requests.get(f"{API_BASE}/graph", params={"entity": entity, "hop": 2})
graph_data = resp.json()

# 构建 NetworkX 图
G = nx.DiGraph()
for node in graph_data["nodes"]:
    G.add_node(node["name"], label=node["label"], type=node["type"])
for edge in graph_data["edges"]:
    G.add_edge(edge["source"], edge["target"], label=edge["predicate"])

# 使用 sigma_graph 渲染
sigma_graph.sigma_graph(
    G,
    layout="force",
    node_color=node_colors,
    edge_label="label",
    height=700
)
```

#### 页面 3: Files（文件管理）

**设计参考**: RAGFlow 知识库文档列表

**功能**:
- 文件列表表格：文件名 | 路径 | 状态 | 大小 | 索引时间 | Fact 数
- 状态筛选器：全部 / 已索引 / 失败 / 跳过
- 点击文件行：展开显示该文件的 fact 列表
- 搜索文件（文件名模糊匹配）
- 统计卡片：总文件数 / 已索引数 / 总 Fact 数 / 总实体数
- 手动触发单个文件重索引按钮

#### 页面 4: Entity Review（实体审核）

**设计参考**: sift-kg 的 `sift review` 工作流

**功能**:
- 待审核实体合并提案列表
- 每行显示：实体 A → 实体 B | 提议名称 | 置信度 | LLM 判断 | 操作
- 操作按钮：
  - `[✓ 批准]` — 执行合并
  - `[✗ 拒绝]` — 丢弃提案
  - `[✎ 编辑]` — 修改合并名称后批准
- 一键批量批准（只批准高置信度提案）
- 已处理的提案折叠显示

#### 页面 5: System Status（系统状态）

**设计参考**: RAGFlow Admin UI 服务状态面板

**功能**:
- 服务健康检查状态：
  - oMLX LLM 服务（可达/不可达）
  - SQLite 数据库（路径 + 大小）
  - FAISS 索引（向量数 + 维度）
  - DeepKE 模型（已加载/未加载）
- 最近索引运行记录表：时间 | 状态 | 文件数 | 新增 Fact 数 | 耗时
- DLQ 状态：失败 chunk 数（按 error_class 分组）| 可重试数
- 一键操作：
  - `[立即索引]` — 等同 `filekb index --watch`
  - `[重试 DLQ]` — 等同 `filekb index --dlq`
  - `[重建索引]` — 等同 `filekb index --rebuild`

#### 页面 6: KB Management（知识库管理）

**设计参考**: RAGFlow 知识库管理 + Dify 工作区设置

**功能**:
- 卡片网格展示所有知识库
- 创建/重命名/删除知识库
- 为知识库添加/移除监控目录
- 新建知识库后引导用户触发索引

**知识库创建 → 索引工作流**:

知识库创建和目录分配是**轻量配置操作**（仅写 config.yaml），索引是**重量计算操作**（需调 LLM，可能耗时数小时）。两者分离，但 UI 需做好衔接：

1. 用户创建 KB → 仅写配置，立即生效
2. 用户添加目录 → 仅写配置，不触发索引
3. 添加目录后显示 CTA 提示条：
   ```
   ✅ 已为「{KB名称}」添加目录 {路径}
      该目录尚未索引。要立即索引吗？
      [🚀 立即索引]   [稍后再说]
   ```
4. 点击「立即索引」→ 触发 `POST /index` → 跳转文件管理页查看进度
5. 点击「稍后再说」→ 关闭提示，用户可随时在文件管理页手动触发

**设计理由**:
- 用户可能想先添加多个目录再一次性索引（避免重复扫描）
- 索引可能耗时很长（数千文件 × LLM 提取），不应在用户无感知时自动触发
- 参考 RAGFlow/Dify/AnythingLLM 的通用模式：配置先行，索引用户主动触发

#### 6.3.1 按钮 Tooltip 规范

所有操作按钮 **必须** 提供 `help` 参数（Streamlit `st.button(help="...")`），鼠标悬停时显示按钮功能的详细说明。特别是：

- **会触发计算/网络请求的按钮**：说明具体会做什么操作、影响范围、是否可逆
- **会产生不可逆后果的按钮**：警告用户操作不可恢复
- **歧义按钮**：说明与相似按钮的区别（如「索引」vs「DLQ 重试」）

### 6.4 前端架构模式

```
┌─────────────────────────────────────┐
│         Streamlit Pages             │
│  chat.py / graph.py / files.py     │
│  entity_review.py / status.py       │
└──────────────┬──────────────────────┘
               │ requests (HTTP)
               ▼
┌─────────────────────────────────────┐
│         FastAPI Server              │
│  /ask /ask/stream /graph /index    │
│  /status /facts /feedback          │
│  /entities/proposals /entities/     │
│  merge /entities/reject             │
└──────────────┬──────────────────────┘
               │ 函数调用
               ▼
┌─────────────────────────────────────┐
│         Core Engine                 │
│  query.py / store.py /             │
│  extractor.py / resilience.py      │
└─────────────────────────────────────┘
```

**关键架构约束**:
- Streamlit 页面**不直接**导入 `store.py` 或 `llm.py`
- 所有状态变更通过 FastAPI → 核心引擎完成
- Streamlit 页面通过 `requests` 库调用本地 FastAPI（`http://localhost:8000`）
- 流式回答通过 `httpx.stream()` 消费 `/ask/stream` SSE 端点

### 6.5 CLI 补充

CLI 提供与 Web UI 完全等价的命令行能力：

```
filekb index --watch       # 扫描目录并索引变更文件
filekb index --file PATH   # 热路径：单文件快速索引
filekb index --dlq         # 重试失败队列中的 chunk
filekb ask "question"      # 单次问答
filekb graph ENTITY --hop 2  # 图探索
filekb status              # 索引状态概览
filekb status --skipped    # 列出跳过的文件
filekb review-entities     # 交互式 TUI 审核实体合并
filekb ui                  # 启动 Streamlit Web UI
```

---

## 7. 后端模块设计

### 7.1 模块清单

| 模块 | 文件 | 状态 | 职责 |
|------|------|------|------|
| `config.py` | 配置系统 | 需更新 | Pydantic v2 模型，YAML 加载，环境变量覆盖 |
| `store.py` | SQLite 数据层 | 需更新 | WAL 模式，FK 级联，表创建，迁移，查询方法 |
| `watcher.py` | 文件扫描与变更检测 | 保留 | SHA256 轮询，三路对比（新增/修改/删除/未变） |
| `parser.py` | 文档解析 | 保留 | 扩展派发表，markitdown/Docling/pymupdf 链 |
| `splitter.py` | 文本分块 | 保留 | 自然边界检测 + 指数回退分割 + 重叠 |
| `extractor.py` | 知识抽取管道 | 需更新 | 多轮 LLM + 增量 JSON 解析 + 语义去重 + 中文分支 |
| `dedup.py` | 语义去重 | 保留 | 内存 embedding 累积 + 向量化余弦相似度 |
| `llm.py` | LLM 客户端 | 需更新 | OpenAI 兼容 HTTP + tenacity 重试 + finish_reason 检测 + adaptive_resplit |
| `resilience.py` | 容错编排器 | **新增** | 错误分类 + tenacity 重试 + finish_reason 溢出处理 + checkpoint + DLQ 管理 |
| `personalization.py` | 反馈与偏好 | **新增** | feedback_service + entity_resolver + preference_analyzer |
| `embed.py` | Embedding 模型 | 需更新 | 懒加载 Singleton `SentenceTransformer`，支持 MPS |
| `vector_store.py` | FAISS 向量索引 | 需更新 | `IndexFlatIP`，增量 `add()`，保存/加载/重建 |
| `graph_store.py` | 知识图谱 | 需更新 | NetworkX `DiGraph`，BFS 扩展，实体搜索，合并 rewiring |
| `query.py` | 查询引擎 | 需更新 | 混合检索 + 加权评分 + 图扩展 + user_score boost |
| `server.py` | REST API | 需更新 | FastAPI 端点（参见 §12 API 规范） |
| `ui/chat.py` | Chat 页面 | **新增** | Streamlit 对话界面 + 流式渲染 + 反馈按钮 |
| `ui/graph.py` | 图谱页面 | **新增** | Streamlit 交互图谱 + sigma_graph 渲染 |
| `ui/files.py` | 文件页面 | **新增** | Streamlit 文件列表 + 统计 + 重索引 |
| `ui/entity_review.py` | 实体审核页面 | **新增** | Streamlit 合并提案审核 |
| `ui/status.py` | 状态页面 | **新增** | Streamlit 系统健康 + DLQ + 运行记录 |
| `cli.py` | CLI | 需更新 | Click 命令组，与 Web UI 等价的能力 |
| `notify.py` | 通知 | 需更新 | macOS 通知 + 失败汇总 |

### 7.2 新增模块详细设计

#### `resilience.py` — 容错编排器 + 溢出处理

FileKB **不进行**预检 token 计数。溢出检测完全基于 oMLX API 的 `finish_reason == "length"` 响应。理由：分裂器 `max_chars_per_chunk=24000` 已保证正常 chunk 的 token 数远低于 Qwen3.6 的 32K 上下文，任何本地 tokenizer 都无法可靠预测 oMLX 的实际 token 消耗（实测同一文本在不同 Qwen 模型上的 token 计数差异达 33%）。

```python
def classify_error(exception: Exception) -> str:
    """将异常映射到五类错误分类学."""
    # CONTEXT_LENGTH_EXCEEDED / RATE_LIMIT / TIMEOUT / 
    # INVALID_JSON / MODEL_UNAVAILABLE / UNKNOWN

def check_finish_reason(response: dict) -> bool:
    """检查 LLM 响应中的 finish_reason.
    
    如果 finish_reason == 'length'，说明输出被截断，可能因上下文溢出。
    返回 True 表示需要 adaptive_resplit 并重试。
    这是 100% 确定的溢出信号，无误报。
    """
    choices = response.get("choices", [{}])
    return choices[0].get("finish_reason") == "length"

def adaptive_resplit(chunk_text: str, depth: int = 0) -> list[str]:
    """递归分割超大 chunk，直到每个子块大小安全。
    
    注意：不需要 tokenizer 来估算——而是在每次分割后重新提交，
    由 oMLX 的 finish_reason 自然验证分割是否充分。
    max_depth=3 防止无限递归。超过深度则硬截断。
    """

def run_with_resilience(fn, chunk) -> tuple[Result | None, DLQEntry | None]:
    """外层 try/except，隔离每个 chunk.
    
    1. 首次调用 LLM
    2. 若响应 finish_reason == 'length' → adaptive_resplit(chunk) → 对子块重新调用
    3. tenacity 重试（最多 3 次，指数退避 + jitter）
    4. 超过重试次数 → 写入 DLQ
    5. 不可重试错误 → 直接 DLQ
    """

def commit_checkpoint(file_id: int, chunk_results: list) -> None:
    """原子 SQLite 事务：UPDATE files + UPDATE chunks 状态."""

def process_dlq(batch_size: int = 10) -> int:
    """第二遍重试：处理 pending 状态的 failed_chunks.
    
    - CONTEXT_LENGTH_EXCEEDED → 用 adaptive_resplit 重新分割
    - RATE_LIMIT/TIMEOUT（next_retry_at 已过期的）→ 重试
    - INVALID_JSON → 跳过，进入失败报告
    """

def build_failure_report(run_id: int) -> dict:
    """聚合 failed_chunks 为可读报告。按 error_class 分组。"""
```

#### `personalization.py` — 反馈与偏好

```python
# --- feedback_service ---
def apply_feedback(answer: dict, feedback_type: str, reason: str = None) -> None:
    """解析 answer['sources']，映射到 fact_id，更新 user_score."""

def allocate_delta(cited_facts: list, total_delta: float, mode: str = "relevance") -> dict:
    """按检索相关度比例分配反馈分数."""

# --- entity_resolver ---
def generate_proposals() -> list[dict]:
    """计算所有实体名的 embedding 相似度，生成合并候选。
    
    优化：用 FAISS 预筛每个实体的 top-10 近邻，而非全量 O(n²) 比较。
    """

def validate_with_llm(proposals: list[dict]) -> list[dict]:
    """LLM 验证：'Are A and B the same entity?' 返回 yes/no/uncertain + canonical name."""

def apply_merges(validated: list[dict]) -> int:
    """执行 SQLite rewiring + 图重建。返回合并数。"""

# --- preference_analyzer ---
def analyze_query_logs(since_days: int = 30) -> dict:
    """从查询日志提取主题和动词偏好信号。"""

def boost_score(fact: dict, preferences: dict) -> float:
    """查询时附加偏好加权。"""
```

### 7.3 修改模块的变更清单

#### `llm.py`

**V1 状态**: 最小化 retry（仅连接错误），不检测 finish_reason。

**V2/V3 变更**:
- `extract_facts()` 包裹在 `tenacity` 重试逻辑中
- 检测 `finish_reason == "length"` → 触发 retry-context prompt（续写截断的 JSON）
- 检测 HTTP 429/503 → 指数退避
- 结构化错误 metadata: `error_class`, `raw_response_sample`, `suggested_action`
- 支持 `fallback_fn` 回调：极端情况下截断 chunk 到 50% 再试一次

#### `query.py`

**关键修复**:
- graph_score 必须纳入最终评分（V1 bug：graph_score 计算了但从未使用）
- 评分公式更新为：
  ```
  final = 0.5×vector + 0.3×graph(distance) + 0.2×fts + 0.1×log(user_score)
  ```
- graph(distance) 应该是基于实际图距离的连续值，而非硬编码的 0.3/0.5

#### `config.py`

**新增 V2/V3 配置段**（参见 §13 配置参考）:
- `resilience.*` — 重试、DLQ、checkpoint 相关
- `personalization.*` — 反馈、实体解析、偏好分析
- `chinese_detection.*` — 中文检测和 DeepKE 过滤器
- `hot_path.*` — 热路径索引模式
- `extraction.max_workers` — 并行 extract worker 数
- `query.user_score_weight` — 反馈评分权重

---

## 8. Prompt 工程

### 8.1 英文提取 Prompt (`prompts/extract.txt`)

保留上游 `knowledge-graph-extractor` 项目经过 Qwen3.6-35B 实战验证的 prompt（80 行指令 + few-shot JSON 示例）。核心要求不变：实体必须是规范简称、谓词必须是精确的 snake_case、每条 fact 附带 evidence_span。

### 8.2 中文提取 Prompt (`prompts/extract_zh.txt`) — **P0 待实现**

**激活条件**: `detect_chinese(chunk_text)` 返回 `True`（>50% CJK 字符）。

**与 `extract.txt` 的差异**:

1. **实体边界指令**（prompt 开头追加）:
   ```
   实体名称必须是原子单位，不可拆分：
   - 人名必须包含完整姓氏和名字（如"张三"不可拆为"张"和"三"）。
   - 机构名必须包含完整后缀（如"华为技术有限公司"不可拆为"华为"和"技术有限公司"）。
   - 复合姓氏必须保留（如"欧阳修"是一个人名，不可拆为"欧阳"和"修"）。
   - 带称谓的人名保持完整（如"王教授"、"李总"不可拆为"王"和"教授"）。
   ```

2. **中文 few-shot 示例**:
   ```json
   {"title": "张三毕业于北京大学", "subject": "张三", "predicate": "graduated_from", "object": "北京大学"}
   {"title": "华为技术有限公司于2024年发布Mate70手机", "subject": "华为技术有限公司", "predicate": "released", "object": "Mate70手机"}
   {"title": "欧阳修曾任参知政事", "subject": "欧阳修", "predicate": "position_held", "object": "参知政事"}
   ```

3. **后处理过滤器**: LLM 提取后，用 DeepKE RoBERTa-wwm-ext 做实体边界验证。LLM 和 DeepKE 在实体边界上不一致 → fact.confidence × 0.8 → 标记入 entity_proposals 审核队列。

4. **参考 YAYI-UIE 的实体类型体系**: 28 种中文实体类型（人物、机构、地点、时间、事件、产品、方法、指标等）和 232 种中文关系的 schema 定义可以在编写 `extract_zh.txt` 时参考。

### 8.3 回答 Prompt (`prompts/answer.txt`)

保留 V1 设计，强调：
- 仅基于提供的上下文回答（不虚构）
- 每条声明标注来源文件
- 信息缺失时明确说明
- 高亮跨文件实体关联

### 8.4 续写 Prompt（截断恢复）

当 `finish_reason == "length"` 时，发送续写请求：
```
Your previous response was cut off. Here is the exact text you were in the middle of:
---
{partial_json}
---
Continue from where you stopped, emitting only valid JSON objects matching the FACT_SCHEMA.
```

利用 Qwen3.6-35B 的 `--cache-reuse 256` KV-cache 复用，续写成本约为完整调用的 20%。

---

## 9. 中文实体抽取方案

### 9.1 方案概述

FileKB 的中文实体抽取采用 **"Qwen3.6-35B 主提取 + 中文专用 Prompt + DeepKE 边界过滤 + 可选 SCIR 自纠正框架"** 的四层策略。

### 9.2 为什么是这个方案？

经过对 YAYI-UIE、OneKE、SCIR、DeepKE 的深度调研和部署条件验证，核心结论是：

**YAYI-UIE 和 OneKE 都无法在 Apple Silicon 上原生运行。**

| 候选方案 | Apple Silicon 兼容 | 原因 |
|----------|-------------------|------|
| Qwen3.6-35B (oMLX) | ✅ 已在运行 | oMLX = MLX，Apple Silicon 原生 |
| DeepKE RoBERTa | ✅ 可以 | 400MB，CPU/MPS 足够 |
| YAYI-UIE | ❌ | Baichuan2 + CUDA，需 A100/A800（33GB 显存） |
| OneKE | ❌ | Chinese-Alpaca-2 + BitsAndBytes(CUDA) + vLLM(CUDA) |
| SCIR 框架 | ✅ 可以 | 框架不依赖特定硬件，可包裹在任何 LLM 外层 |

在"零云依赖、100% 本地"的约束下，Qwen3.6-35B 是唯一实际可用的主提取模型。

### 9.3 各组件角色

| 组件 | 角色 | 许可证 |
|------|------|--------|
| Qwen3.6-35B-A3B-MTP | 主提取模型 | Apache 2.0 |
| `extract_zh.txt` | 中文专用提取 Prompt | FileKB 原创 |
| DeepKE RoBERTa-wwm-ext | 实体边界 guardrail | MIT |
| SCIR 框架 | P1 自纠正增强 | AAAI 2025 论文 |
| OneKE | P1 备用高精度后端（需 MLX 转换验证） | Apache 2.0 风格 |
| YAYI-UIE | P2 备用（需 MLX 转换 + 社区成熟） | Apache 2.0 |

### 9.4 实施阶段

**阶段 0 — 开发前准备（P0）**:
1. 编写 `prompts/extract_zh.txt`（完整的 80+ 行，包含实体边界规则、中文 few-shot 示例、复合姓氏/机构后缀约束）
2. 验证 DeepKE RoBERTa-wwm-ext 在 MPS/CPU 上可用

**阶段 1 — V3 核心实现（P0-P1）**:
4. 实现 `detect_chinese()` 函数
5. 实现 DeepKE 边界过滤器（LLM vs DeepKE 不一致 → confidence × 0.8 → entity_proposals）
6. 中文 prompt 路由（extractor.py 中自动切换）

**阶段 2 — 质量提升（P1）**:
7. SCIR 双路径自纠正框架集成（Memory Bank + Self-Correcting）
8. OneKE MLX 转换可行性验证

**阶段 3 — 长期（P2）**:
9. YAYI-UIE MLX 转换（等待社区方案成熟）
10. 中文实体跨语言对齐（"Alice" ↔ "爱丽丝"）
11. bge-m3 中文微调版本评估

### 9.5 已知限制

- `bge-m3` 的 embedding 去重对中文单字符差异敏感度不足（"华为" vs "华为技术" 可能遗漏近重复）
- 实体消歧器（§11.2）对中文内容尤其重要
- "Alice" ↔ "爱丽丝" 的跨语言对齐暂未解决（V2 已知限制，面向未来）

---

## 10. 可靠性工程

### 10.1 错误分类学

每次 LLM 管道失败被归入以下五类之一：

| 错误类型 | 触发条件 | 恢复策略 | 可重试？ |
|---------|---------|---------|---------|
| `CONTEXT_LENGTH_EXCEEDED` | Prompt + chunk > 模型上下文窗口 | 自适应递归分割（max depth 3） | 是（通过分割） |
| `RATE_LIMIT` / `TIMEOUT` | LLM 服务器返回 429/503，网络超时 | 指数退避：1s → 2s → 4s（max 30s），最多 3 次 | 是 |
| `INVALID_JSON` | LLM 输出格式错误/不符合 schema | 记录样本，跳过 chunk，标记人工审核 | 否 |
| `MODEL_UNAVAILABLE` | LLM 服务器离线或返回 5xx | 立即 DLQ 入队；下次调度运行时重试 | 是（延迟） |
| `UNKNOWN` | 任何未捕获异常 | DLQ 入队 + 完整 traceback；冷却后重试一次 | 可能 |

### 10.2 响应式溢出检测与恢复

FileKB **不进行**任何形式的预检 token 计数。溢出检测和恢复策略如下：

**三层防护**:

1. **结构性保证（第一层）**: 分裂器的 `max_chars_per_chunk = 24000` 确保中文文本 token 数远低于 Qwen3.6 的 32K 上下文窗口。对于中文内容（占用户内容 >80%），一个 24000 字符的 chunk 约等于 12,000-16,000 tokens——仅占 32K 上下文的 37-50%。即使是最坏情况的纯英文/代码，也不会超过 ~24,000 tokens。在正常操作下，chunk 不会触发溢出。

2. **响应式检测（第二层）**: 每次 LLM 调用后检查响应的 `finish_reason`。如果 `finish_reason == "length"`，说明输出被截断，极可能是上下文溢出。这是 100% 确定的信号——无误报、无漏报。

3. **自适应恢复（第三层）**: 当检测到 `finish_reason == "length"` 时，韧性层自动触发 `adaptive_resplit()` ——将当前 chunk 递归分割为更小的子块，然后重新提交。分割深度上限为 3。

**为什么不用本地 tokenizer？**
- 实测证据：同一文本在 Qwen3.6-35B 上消耗 14,445 tokens，在 Qwen3-Coder-Next 上消耗 10,841 tokens（33% 差异）
- 任何本地 tokenizer（tiktoken 或 transformers）都无法可靠预测 oMLX 的实际 token 消耗
- 响应式检测的"成本"（溢出时浪费一次 LLM 调用，~4 秒）远低于引入 `transformers`（~2GB）的维护成本
- 分裂器的结构性保证意味着溢出极为罕见

**溢出成本分析**: 从 oMLX 测试数据来看，一次 LLM 调用约 4 秒。如果每 10,000 个 chunk 发生一次溢出（保守估计），浪费的总时间约 4 秒——可以忽略不计。

### 10.3 Tenacity 重试

```python
from tenacity import retry, wait_exponential_jitter, stop_after_attempt

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
    retry=lambda e: isinstance(e, (RateLimitError, TimeoutError)),
    before_sleep=lambda retry_state: logger.warning(
        f"Retry {retry_state.attempt_number}/3 for chunk {retry_state.args[1]}")
)
def llm_extract(chunk):
    ...
```

**为什么用 `tenacity`?** `wait_exponential_jitter` 加全抖防止 oMLX 重启后所有排队 chunk 同时重试。RAGFlow PR #13793 也在采用相同的 tenacity 方案。

**硬止**: 3 次尝试失败 → DLQ 入队。不无限重试。用户醒来发现索引器卡死是最坏场景。

### 10.4 Checkpointing

FileKB 的 checkpoint 是原子 SQLite 事务（非外部快照服务）。

**文件级 checkpoint**（处理完一个文件的所有 chunk 后）:
```sql
BEGIN;
UPDATE files SET status='done' WHERE id = ?;
UPDATE chunks SET status='done' WHERE file_id = ? AND status = 'processing';
COMMIT;
```

**运行级 checkpoint**: `runs` 表在运行结束时更新 `status` 为 `completed` 或 `crashed`。下次启动时自动检测。

**崩溃恢复**: 进程崩溃后，下次运行看到 `chunks.status = 'processing'` → 重置为 `pending` 并重新处理。已完成的 chunk（`status = 'done'`）跳过，不会重复计算。

### 10.5 Dead Letter Queue (DLQ)

实现为 `failed_chunks` 表。生命周期：

1. **入队**: Chunk 用尽重试次数或命中不可重试错误 → INSERT into `failed_chunks`
2. **第二遍**: 主批次完成后 → 处理 DLQ：
   - `CONTEXT_LENGTH_EXCEEDED` → 用更深的递归层次重新分割
   - `RATE_LIMIT`（`next_retry_at` 已过期）→ 重试
   - `INVALID_JSON` → 跳过，入失败报告
3. **通知**: 早间通知明确列出每个 `error_class` 的 chunk 数
4. **清理**: >30 天的条目自动删除

**为什么是 DLQ 而不是日志文件？** 日志是被动的（需要人主动读）。DLQ 是主动的（系统可以自动重试）。

### 10.6 静默跳过防护

1. 每个跳过的文件记录在 `files.status = 'skipped'` + `error_msg`
2. 跳过的文件在通知中可见
3. `filekb status --skipped` 列出所有跳过的文件
4. 解析器返回空文本 → 标记为 `failed_partial`，而不是 `done`

---

## 11. 个性化与反馈闭环

### 11.1 反馈加权排序

**机制**: 每个回答附带 `[👍 helpful] / [👎 not helpful]`。

- **正面反馈**: 答案引用的 fact 的 `user_score` += 0.1（限幅 [0, 2.0]）
- **负面反馈**: `user_score` -= 0.1
- **分配**: 如果回答引用了 4 个 chunk，Delta 按检索相关度比例分配到各 chunk 的 fact
- **查询影响**:
  ```
  final_score = 0.5×vector + 0.3×graph + 0.2×fts + 0.1×log(user_score)
  ```
  `log(user_score)` 保证被严重惩罚的 fact 下沉，被适度肯定的 fact 温和上浮。0.1 的权重故意设得小，防止反馈循环破坏基础排序。

**自动衰减**: `user_score` 每周衰减 1%（无新反馈时），防止过时反馈永久偏斜结果。

### 11.2 自动实体消歧

**问题**: LLM 可能从文件 A 提取 "Alice"，从文件 B 提取 "Alice Smith"。不合并的话，图将它们视为无关节点。

**解决方案**（借鉴 sift-kg 和 LightRAG Issue #1323，但全自动）:

1. **候选生成**（索引后批处理）:
   - 计算所有实体名的 embedding 相似度
   - 余弦相似度 > 0.80 的对 → 生成合并提案 → INSERT into `entity_proposals`
   - **优化**: 用 FAISS 预筛 top-10 近邻，而非全量 O(n²) 比较

2. **LLM 验证**: 将 top-K 提案发给 LLM："Are 'Alice' and 'Alice Smith' the same person?" → yes/no/uncertain

3. **自动应用**: 置信度 > `auto_approve_threshold`（默认 0.85）→ 自动合并

4. **合并执行**:
   - 选择规范名称（通常是更完整的形式）
   - Rewire 所有 fact 边：将旧名称替换为规范名称
   - 更新 NetworkX 图和 FAISS 索引

**人工覆盖**: `filekb review-entities`（TUI）或 Web UI 实体审核页面。

### 11.3 偏好记忆

**从查询日志自动推导**（非人工输入）:
- `preferred_topic_boost`: 高频查询的主题领域
- `frequent_entity`: 频繁被查询的实体
- `answer_verbosity`: 用户偏好的回答详细程度

偏好信号都是加法性的，有限幅，防止失控放大。非实时更新（后台批处理），避免查询延迟。

---

## 12. API 规范

### 12.1 FastAPI 端点

所有 API 在 `http://localhost:{port}` 上暴露（默认 8000）。Content-Type: `application/json`。

| 方法 | 路径 | 描述 | 请求体 | 响应 |
|------|------|------|--------|------|
| POST | `/ask` | 问答 | `{"question": "str", "top_k": int?}` | `{"answer": "str", "sources": ["str"], "related_facts": [...]}` |
| POST | `/ask/stream` | 流式问答 | 同上 | SSE 事件流，每个 event 是 answer token |
| GET | `/graph` | 获取图数据 | `?entity=str&hop=int` | `{"nodes": [...], "edges": [...]}` |
| POST | `/index` | 触发索引 | `{"paths": ["str"]?, "dlq": bool?}` | `{"run_id": int, "status": "str"}` |
| GET | `/status` | 系统状态 | — | `{"files_total": int, "facts_total": int, "entities": int, "last_run": {...}, "health": {...}}` |
| GET | `/facts` | 检索 fact | `?entity=str&limit=int` | `{"facts": [...]}` |
| POST | `/feedback` | 提交反馈 | `{"answer_id": "str", "type": "positive\|negative", "reason": "str?"}` | `{"status": "ok"}` |
| GET | `/entities/proposals` | 合并提案 | `?status=str` | `{"proposals": [...]}` |
| POST | `/entities/merge` | 批准合并 | `{"proposal_id": int, "canonical_name": "str"?}` | `{"status": "ok", "merged_count": int}` |
| POST | `/entities/reject` | 拒绝合并 | `{"proposal_id": int}` | `{"status": "ok"}` |

### 12.2 流式回答格式

`/ask/stream` 使用 Server-Sent Events:

```
event: token
data: {"token": "根据"}

event: token
data: {"token": "你的"}

event: token
data: {"token": "知识库"}

...

event: done
data: {"sources": [...], "related_facts": [...]}
```

### 12.3 健康检查

`GET /status` 附加 `health` 对象：
```json
{
  "health": {
    "llm_server": "ok" | "unreachable",
    "sqlite": {"path": "str", "size_mb": float},
    "faiss": {"vectors": int, "dimension": int},
    "deepke": "loaded" | "not_loaded",
    "graph": {"nodes": int, "edges": int}
  }
}
```

---

## 13. 配置参考

### 13.1 配置结构（Pydantic v2 模型）

```yaml
# ~/.filekb/config.yaml

# === LLM 配置 ===
llm:
  base_url: "http://localhost:8080/v1"   # oMLX OpenAI-compatible endpoint
  model: "Qwen3.6-35B-A3B-MTP"
  api_key: "not-needed"                   # oMLX 无需 API key
  timeout: 120                            # 秒
  max_retries: 3                          # tenacity 重试上限
  context_window: 32768                   # Qwen3.6-35B 的上下文窗口大小

# === Embedding 模型配置 ===
embedding:
  model: "BAAI/bge-m3"
  device: "cpu"                           # "cpu" | "mps"（M5 Max 可选 MPS）
  normalize: true

# === 目录监控 ===
directories:
  - path: "~/Documents/Notes"
    recursive: true
    exclude_patterns: [".git", "__pycache__", ".DS_Store"]
  - path: "~/Documents/Papers"
    recursive: true

# === 提取参数 ===
extraction:
  rounds: 2                               # 每个 chunk 的 LLM 提取轮数
  max_chars_per_chunk: 24000
  overlap_chars: 500
  max_workers: 2                          # 128GB M5 Max 可设 2-4
  dedup_threshold: 0.90                   # 余弦相似度去重阈值
  confidence_threshold: 30                # 低于此置信度的 fact 被丢弃

# === 查询参数 ===
query:
  vector_top_k: 20                        # FAISS 向量搜索返回数
  fts_top_k: 10                           # FTS5 全文搜索返回数
  vector_weight: 0.5
  graph_weight: 0.3
  fts_weight: 0.2
  user_score_weight: 0.1
  max_context_chunks: 8
  max_context_facts: 30
  answer_max_tokens: 1024

# === 可靠性（V2 新增） ===
resilience:
  enabled: true
  max_chunk_depth: 3                      # 自适应分割最大递归深度
  retry:
    max_attempts: 3
    initial_delay: 1                      # 秒
  dlq:
    auto_retry: true                      # 主批次后自动处理 DLQ
    prune_days: 30

# === 个性化（V2 新增） ===
personalization:
  enabled: true
  feedback:
    delta: 0.1                            # 每次反馈的分数变化
    decay_rate: 0.01                      # 每周衰减率
  entity:
    auto_approve_threshold: 0.85          # 自动合并置信度阈值
    similarity_threshold: 0.80            # 候选生成阈值
  analyzer:
    run_after_index: true                 # 索引后运行偏好分析

# === 热路径（V2 新增） ===
hot_path:
  enabled: true
  rebuild_graph: false                    # false=追加到 NetworkX; true=全量重建

# === 中文检测（V2 新增） ===
chinese_detection:
  threshold: 0.50                         # CJK 字符比例触发中文 prompt
  ner_filter:
    enabled: true
    model_path: "~/.filekb/models/deepke-cn"

# === 通知 ===
notification:
  show_failures: true                     # 通知中列出失败详情

# === 数据库 ===
database:
  path: "~/.filekb/filekb.db"
  wal_mode: true

# === 日志 ===
logging:
  level: "INFO"
  file: "~/.filekb/filekb.log"
```

### 13.2 环境变量覆盖

关键设置可通过环境变量覆盖：
- `FILEKB_LLM_URL` — 覆盖 `llm.base_url`
- `FILEKB_LLM_MODEL` — 覆盖 `llm.model`
- `FILEKB_CONFIG` — 指定自定义配置文件路径
- `FILEKB_DB_PATH` — 覆盖 `database.path`

---

## 14. 测试策略

### 14.1 单元测试

| 测试文件 | 覆盖范围 | 数量 |
|---------|---------|------|
| `test_splitter.py` | 分块逻辑，边界检测，重叠 | 8 |
| `test_store.py` | SQLite schema，CRUD，迁移 | 12 |
| `test_watcher.py` | 变更检测，SHA256，三路对比 | 8 |
| `test_parser.py` | 各文件格式解析 | 8 |
| `test_resilience.py` | 错误分类，tenacity 重试，finish_reason 溢出检测，adaptive_resplit，checkpoint，DLQ 生命周期 | 10 |
| `test_feedback.py` | 分数分配，衰减，比例分配 | 8 |
| `test_entity_resolver.py` | 提案生成，合并执行，rewire 正确性 | 7 |
| `test_dedup.py` | 语义去重准确性 | 4 |

**总计: 65 个单元测试**

### 14.2 集成测试

V1 标记为"pending"。V3 要求在发布前必须实现：

| 测试文件 | 证明什么 |
|---------|---------|
| `test_index_pipeline_e2e.py` | 索引测试语料库（md + pdf + code）→ fact 写入 DB → FAISS 搜索可用 → 图连通 |
| `test_fault_isolation.py` | 注入坏 chunk → 运行完成 → 其他文件不受影响 → DLQ 包含坏 chunk → 修复后重试成功 |
| `test_checkpoint_resume.py` | 索引中杀进程 → 重启 → 从上次提交的文件恢复 → 无重复 fact |
| `test_query_feedback.py` | 提问 → 踩 → 重问同一问题 → ranking 有明显变化 |
| `test_chinese_extraction.py` | 中文内容 → 触发 extract_zh.txt → DeepKE filter 校验 → 实体边界正确 |

### 14.3 CI / 覆盖率目标

- 最低覆盖率: **80% 覆盖 storage, parsing, chunking, resilience, feedback 模块**
- 集成测试在 GitHub Actions macOS runner 上 night 运行
- Ruff linting, 100 字符行长限制

---

## 15. 已知限制与路线图

### 15.1 已知限制

1. **FAISS 不支持增量更新**: 每次从 SQLite 全量重建。在 >1M 条向量时切换 `IndexIVFFlat`
2. **跨语言实体不对齐**: "Alice"（英文）和 "爱丽丝"（中文）不会自动合并
3. **不支持扫描 PDF**: 需要有文本层的 PDF
4. **图冷重建**: 每次查询 ~2 秒冷启动。未来优化：pickle 热缓存
5. **bge-m3 中文 embedding 精度**: 对中文实体的单字符差异不够敏感
6. **OneKE/YAYI-UIE 不可用**: 均需 CUDA，无法在 Apple Silicon 上原生运行
7. **无真实时间文件监控**: 仅夜间批处理 + 手动热路径

### 15.2 路线图

**V3.1（发布后第一个月）**:
- SCIR 自纠正框架集成
- OneKE MLX 转换可行性验证
- 图 pickle 热缓存（消除冷启动）
- `max_files_per_run` 首轮索引分夜执行

**V3.2（后续）**:
- FAISS 切换到 `IndexIVFFlat`（百万级向量扩展）
- 中文实体跨语言对齐
- bge-m3 中文微调版评估
- YAYI-UIE MLX 转换（长期观望）

---

## 16. 附录

### 附录 A: 上游项目引用纠正

原 DEVELOPMENT.md 包含三处引用错误，在此勘误：

1. **LightRAG 与 "Leiden community"**: LightRAG **不使用** Leiden 社区检测。LightRAG 的优势之一是不需要社区报告（与 Microsoft GraphRAG 区分）。此处已修正。

2. **"指数回退分块" 来源**: `knowledge-graph-extractor` 仓库的公开 README 和代码中未记载名为 "exponential backoff chunking" 的算法。FileKB 的分块策略是原创设计，参考通用 NLP 分块文献。此处引用已修正。

3. **RAGFlow DeepDoc F1=95%**: 该数据点在 RAGFlow 公开仓库中无法验证。DeepDoc 确实实现了表格结构识别，但无公开基准测试附带的 F1 分数。此处引用已移除。

### 附录 B: 许可证合规

| 集成组件 | 来源 | 许可证 | 角色 |
|---------|------|--------|------|
| `tenacity` | PyPI | Apache 2.0 | LLM 调用重试 |
| `pydantic` | PyPI | MIT | 配置校验 |
| `sentence-transformers` | PyPI | Apache 2.0 | bge-m3 embedding |
| `faiss-cpu` | PyPI | MIT | 向量索引 |
| `networkx` | PyPI | BSD | 知识图谱 |
| `markitdown` | PyPI | MIT | 文档解析 |
| `docling` | PyPI | MIT | PDF 表格解析 |
| `jieba` | PyPI | MIT | 中文分词（FTS5） |
| `streamlit` | PyPI | Apache 2.0 | Web UI |
| `fastapi` | PyPI | MIT | REST API |
| `click` | PyPI | BSD | CLI |
| DeepKE RoBERTa-wwm-ext | zjunlp/DeepKE | MIT | 中文实体边界校验 |
| 实体合并工作流 | sift-kg | MIT | 模式参考（FileKB 原创实现） |
| 反馈加权排序 | RAGFlow PR #12689 | Apache 2.0 | 模式参考（FileKB 原创实现） |

YAYI-UIE（Apache 2.0）和 OneKE（Apache 2.0 风格）仅研究参考，未直接集成。

### 附录 C: 同类项目已知陷阱

来自 RAGFlow、LightRAG 等项目的 Issue 讨论中记录的陷阱，已在 FileKB V3 设计中防范：

| 陷阱 | 来源 | FileKB 对策 |
|------|------|------------|
| 一次 API 超时杀死 72 小时 RAPTOR 索引任务，无恢复 | RAGFlow #11483 | tenacity retry + DLQ + file-level checkpoint |
| 超大文件 OOM 无警告 | RAGFlow #2236 | token preflight + adaptive_resplit + max_files_per_run |
| FAISS 索引损坏后静默返回空结果 | LightRAG #1323 | 启动时检查 + 自动重建 + 健康检查端点 |
| 无重试逻辑导致 transient 错误静默丢数据 | LangExtract PR #385 | tenacity 指数退避 + DLQ |
| 长尾文件处理完但未通知用户 | RAGFlow #5050 | 通知包含成功/失败/跳过明细 + DLQ 分组 |

### 附录 D: 128GB M5 Max 内存预算详解

| 组件 | 4-bit 量化 | 8-bit 量化 | 备注 |
|------|-----------|-----------|------|
| Qwen3.6-35B (oMLX) | ~20 GB | ~35 GB | oMLX 基于 MLX, Apple Silicon 原生 |
| bge-m3 (sentence-transformers) | ~2 GB | ~2 GB | CPU/MPS 均可 |
| DeepKE RoBERTa-wwm-ext | ~0.4 GB | ~0.4 GB | CPU 推理 |
| Python 运行时 + SQLite 缓存 | ~2 GB | ~2 GB | 取决于 worker 数 |
| FAISS + NetworkX 内存图 | ~2-5 GB | ~2-5 GB | 取决于 KB 规模 |
| **总计** | **~26-30 GB** | **~41-45 GB** | |
| **占 128GB 比例** | **23%** | **35%** | |

**资源配置建议**:
- 4-bit: 默认 `max_workers = 2`，可安全调到 4
- 8-bit: 默认 `max_workers = 1`，可安全调到 2
- M5 Max 无风扇设计 → 长时间满负荷需注意热降频 → 夜间批处理天然有利（更凉爽）

---

---

> **本文档是 FileKB 项目的唯一权威设计来源。所有开发决策应以此文档为准。如有疑问或需要修改设计，请更新本文档并标注变更原因。**
