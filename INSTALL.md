# FileKB — 安装与配置指南

本指南基于 **macOS + oMLX** 的真实运行环境撰写，所有步骤已验证通过。

## 环境要求

FileKB 本身内存占用很小（~200 MB 含 FAISS + 图谱）。内存需求主要由 LLM 决定。

### 最低配置

| 组件 | 说明 |
|------|------|
| **操作系统** | macOS 13+ 或 Linux（Apple Silicon 推荐，OCR 需 macOS Vision） |
| **内存** | 16 GB — 支持 7B 级 LLM（4-bit ~6 GB）+ embedding + 系统开销 |
| **磁盘** | 2 GB 空闲 |
| **Python** | 3.12+ |
| **LLM 服务** | oMLX / Ollama / llama.cpp / vLLM 等 OpenAI 兼容 API |

### 推荐配置

| 组件 | 说明 |
|------|------|
| **内存** | 64 GB — 舒适运行 35B 级 LLM（4-bit ~17 GB）+ embedding + OCR |
| **磁盘** | 10 GB 空闲（留足索引和模型空间） |

> 开发者运行于 M5 Max + 128GB，但那是为了在 oMLX 中同时加载多个 35B+ 模型。FileKB 只需一个 LLM + 一个 embedding 模型即可。

### 所需运行时

| 组件 | 说明 |
|------|------|
| **LLM** | oMLX 或其他 OpenAI 兼容 API，运行在 `http://127.0.0.1:8081/v1` |
| **Embedding 模型** | bge-m3-mlx-fp16（oMLX API，14ms/vec，内存由 LLM 进程承载），或本地 sentence-transformers（~2 GB，独立内存） |

### 可选

| 组件 | 说明 |
|------|------|
| **DeepKE** | 中文实体边界校验（`pip install filekb[deepke]`，额外 ~400MB） |
| **Apple Vision OCR** | PDF/图片文字识别（macOS 系统自带，无需额外安装） |

## 安装步骤

### 1. 确认 LLM 服务已运行

```bash
# 验证 oMLX 是否正常运行
curl http://127.0.0.1:8081/v1/models
```

应返回可用模型列表。

### 2. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装 FileKB

```bash
# 从 PyPI 安装（推荐）
pip install filekb

# 安装中文实体校验支持（可选）
pip install "filekb[deepke]"

# 或者从源码安装（开发模式）
git clone https://github.com/filekb-org/filekb.git
cd filekb
pip install -e ".[dev]"
```

### 4. 创建配置

首次运行时，FileKB 会自动在 `~/.filekb/` 下生成默认配置文件。

```bash
# 第一次运行会初始化 ~/.filekb/config.yaml
filekb --help
```

### 5. 编辑配置

编辑 `~/.filekb/config.yaml`，至少配置：

```yaml
llm:
  base_url: "http://127.0.0.1:8081/v1"   # LLM API 地址
  model: "daily-agent-best-mtp"          # 模型名称（按你的 oMLX 配置）

embedding:
  backend: "omlx"                        # 默认通过 oMLX API 生成向量
  model: "bge-m3-mlx-fp16"              # 嵌入模型名称

directories:
  - path: "/Users/yourname/Documents/知识库"  # 要监控的目录
    group: "我的知识库"                        # KB 名称
    recursive: true
    exclude_patterns: [".git", "__pycache__", ".DS_Store"]

query:
  vector_weight: 0.5
  graph_weight: 0.3
  fts_weight: 0.2
  user_score_weight: 0.1
```

更多配置项参见 [配置参考](#配置参考)。

## 开始使用

### 首次索引

```bash
# 索引所有配置目录中的文件
filekb index

# 索引完成后检查状态
filekb status
```

### 提问

```bash
# 命令行提问
filekb ask "你们的项目用了什么技术栈？"

# 或在 Web UI 中交互
filekb ui     # 启动 Streamlit 界面 (http://localhost:8501)
```

### Web UI 功能

`filekb ui` 启动后可以访问：

- **Chat** — 自然语言问答，支持流式输出和溯源引用
- **知识图谱** — 交互式图谱浏览，实体搜索，BFS 扩展
- **文件管理** — 文件列表、状态筛选、重索引
- **实体审核** — 实体合并建议的审批界面
- **系统健康** — FAISS 向量数、图谱节点数、DLQ 状态、运行历史

## 配置参考

完整配置项及默认值见下方。环境变量可覆盖对应配置。

```yaml
# === LLM 服务 ===
llm:
  base_url: "http://127.0.0.1:8081/v1"           # FILEKB_LLM_URL
  model: "daily-agent-best-mtp"                   # FILEKB_LLM_MODEL
  api_key: "not-needed"                            # 本地 oMLX 不需要
  timeout: 120
  max_retries: 3
  context_window: 32768

# === 嵌入模型 ===
embedding:
  backend: "omlx"       # "omlx"（通过 API）或 "local"（sentence-transformers）
  model: "bge-m3-mlx-fp16"
  omxl_url: "http://127.0.0.1:8081/v1"
  device: "cpu"         # 仅 backend=local 时生效
  normalize: true

# === 数据库 ===
database:
  path: "~/.filekb/filekb.db"
  wal_mode: true

# === 监控目录 ===
directories:
  - path: "/path/to/docs"
    group: "默认"
    recursive: true
    exclude_patterns: [".git", "__pycache__", ".DS_Store"]

# === 知识提取 ===
extraction:
  rounds: 2                                    # LLM 提取轮数
  max_chars_per_chunk: 24000
  overlap_chars: 500
  dedup_threshold: 0.9
  confidence_threshold: 30
  max_workers: 2

# === 中文处理 ===
chinese_detection:
  threshold: 0.5                               # CJK 字符检测阈值
  ner_filter:
    enabled: true
    model_path: "~/.filekb/models/deepke-cn"

# === 查询引擎 ===
query:
  vector_top_k: 20
  fts_top_k: 10
  max_context_chunks: 8
  max_context_facts: 30
  answer_max_tokens: 1024
  # 混合检索权重
  vector_weight: 0.5
  graph_weight: 0.3
  fts_weight: 0.2
  user_score_weight: 0.1

# === 韧性 ===
resilience:
  enabled: true
  max_chunk_depth: 3
  retry:
    initial_delay: 1.0
    max_attempts: 3
  dlq:
    auto_retry: true
    prune_days: 30                              # 自动清理超过 N 天的 DLQ 记录

# === 个性化 ===
personalization:
  enabled: true
  entity:
    similarity_threshold: 0.8
    auto_approve_threshold: 0.85
  feedback:
    delta: 0.1
    decay_rate: 0.01                            # 每周衰减率
  analyzer:
    run_after_index: true

# === OCR（macOS 专用） ===
ocr:
  enabled: true
  languages: ["zh-Hans", "zh-Hant", "en"]
  min_confidence: 0.3
  pdf_dpi: 200

# === 日志与通知 ===
logging:
  level: "INFO"                                 # FILEKB_LOG_LEVEL
  file: "~/.filekb/filekb.log"

notification:
  show_failures: true
```

## 故障排除

### `Cannot connect to FileKB server`

```bash
# 确认 FastAPI 后端已启动
filekb ui   # 会自动启动后端
```

### LLM 连接失败

```bash
# 验证 oMLX 地址和端口
curl http://127.0.0.1:8081/v1/models
# 检查 ~/.filekb/config.yaml 中 llm.base_url 是否正确
```

### Embedding 报错

如果 oMLX API 返回 404，检查是否加载了 bge-m3 模型：

```bash
curl http://127.0.0.1:8081/v1/models | grep -i "bge"
```

如果 oMLX 中没有 bge-m3，可以将 embedding.backend 改为 `"local"`，让 sentence-transformers 本地运行（约占用 2GB 内存）。

### 中文提取效果差

```bash
# 安装 DeepKE 中文实体校验
pip install "filekb[deepke]"
# 配置中确认 enabled: true
```

### OCR 不可用

- OCR 依赖 macOS Vision 框架，仅在 Apple Silicon / Intel Mac 上可用
- Linux 系统上 OCR 功能自动禁用
- 确认 `ocr.enabled: true` 且 `ocr.min_confidence` 不低于 0.3

### 重启故障恢复

断电或异常退出后重新启动，FileKB 会自动：
- 将 `status='processing'` 的 chunk 重置为 `pending`
- 重新处理未完成的文件
- 重新构建 FAISS 和知识图谱索引
