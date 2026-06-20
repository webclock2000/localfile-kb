# FileKB

<p align="center">
  <strong>📂 File-driven personal knowledge base — 100% local, zero cloud</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://pypi.org/project/filekb"><img src="https://img.shields.io/pypi/v/filekb" alt="PyPI version"></a>
  <a href="https://pypi.org/project/filekb"><img src="https://img.shields.io/pypi/pyversions/filekb" alt="Python versions"></a>
</p>

---

FileKB monitors local directories, auto-extracts structured knowledge as entity-relationship triples via a **local LLM**, builds a queryable knowledge graph, and supports natural language Q&A with full source provenance.

> **Designed for Mac + oMLX. Works anywhere you can run a local LLM.**

## ✨ What FileKB Does

1. **📁 Drop files in, get a knowledge graph out.** Markdown, PDF, Word, PowerPoint, Excel, Python/JS/Go code, CSV, JSON — parsed automatically.

2. **🔗 Discovers hidden connections.** Entity-relationship extraction surfaces cross-file links that `grep` can't find.

3. **💬 Ask in natural language.** "What decisions did I make about the API design?" → answer with source file citations.

4. **🏷️ Chinese-first.** >80% of authors' content is Chinese. CJK-aware chunking, jieba FTS5 tokenizer, Chinese-specific entity extraction prompts, DeepKE boundary guardrail.

5. **📎 Everything stays on your machine.** SQLite + FAISS + NetworkX — no telemetry, no cloud, no analytics. Your files never leave your Mac.

6. **🖥️ Modern Web UI.** Streamlit native dark mode, knowledge graph explorer, entity merge review, DLQ panel, multi-KB management.

## 💻 System Requirements

FileKB itself is lightweight (~200 MB). The real memory requirement comes from your LLM and embedding model.

### Minimum — a 32 GB Mac or Linux machine

With 32 GB RAM you can comfortably run a 14B-class LLM (4-bit ~9 GB) plus the embedding model (bge-m3 ~1 GB), with enough headroom for OCR, knowledge graph indexing, and normal desktop use.

- **OS**: macOS 13+ (Apple Silicon) or Linux
- **RAM**: 32 GB
- **Disk**: 2 GB free
- **Python**: 3.12+
- **LLM**: Any OpenAI-compatible local server — oMLX, Ollama, llama.cpp, vLLM

### Recommended — a 64 GB Apple Silicon Mac

With 64 GB you can run a 35B-class LLM (4-bit ~17 GB) alongside the embedding model, OCR, and everything else comfortably — no swapping, no waiting.

> **Note**: The developer uses an M5 Max with 128 GB to run multiple large models in oMLX simultaneously. For FileKB alone, that much is not necessary.

## 🚀 Quick Start

### Prerequisites

- **macOS** (Apple Silicon recommended) or Linux
- **Python 3.12+**
- **A local LLM** — oMLX, Ollama, llama.cpp, or any OpenAI-compatible API running on your machine

### Installation

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install FileKB from PyPI
pip install filekb

# 3. Create your config
mkdir -p ~/.filekb
filekb --help   # generates default config on first run
```

### First Index

```bash
# Add a directory to monitor and index it
filekb --kb "我的知识库" index

# Or add specific directories via the Web UI
filekb ui
```

### Ask Questions

```bash
# CLI
filekb ask "这个项目用了什么技术栈？"

# Or use the Web UI at http://localhost:8501
filekb ui
```

## 📋 Commands

| Command | Description |
|---------|-------------|
| `filekb index` | Scan directories and index changed files |
| `filekb index --file FILE` | Re-index a single file (hot path) |
| `filekb index --dlq` | Retry failed chunks in the Dead Letter Queue |
| `filekb index --rebuild` | Full rebuild of all indices |
| `filekb ask "question"` | Natural language Q&A with source citations |
| `filekb status` | Indexing health overview |
| `filekb status --skipped` | List skipped/failed files |
| `filekb graph ENTITY` | Knowledge graph exploration around an entity |
| `filekb ui` | Launch Streamlit Web UI (starts FastAPI backend automatically) |

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│                  INTERFACE                       │
│   CLI (click)  │  Web UI (Streamlit)  │  REST (FastAPI)  │
└──────────────────────┬──────────────────────────┘
┌──────────────────────┴──────────────────────────┐
│              PERSONALIZATION                    │
│   Feedback Scorer → Entity Resolver → QA Scan   │
└──────────────────────┬──────────────────────────┘
┌──────────────────────┴──────────────────────────┐
│                QUERY ENGINE                      │
│   Vector(FAISS) + Graph(NetworkX) + FTS(SQLite)  │
│   → Weighted merge → LLM answer w/ citations     │
└──────────────────────┬──────────────────────────┘
┌──────────────────────┴──────────────────────────┐
│               STORAGE LAYER                      │
│   SQLite(+FTS5+jieba) │ FAISS │ NetworkX DiGraph │
└──────────────────────┬──────────────────────────┘
┌──────────────────────┴──────────────────────────┐
│            EXTRACTION + RESILIENCE               │
│   Multi-round LLM → Semantic Dedup → DLQ         │
│   Error classification → Checkpoint → Retry      │
└──────────────────────┬──────────────────────────┘
┌──────────────────────┴──────────────────────────┐
│                 FILE LAYER                       │
│   SHA256 Watcher → Parser → Smart Splitter       │
└─────────────────────────────────────────────────┘
```

### Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **LLM Backend** | oMLX (Qwen3.6-35B distilled) | Apple Silicon native, 14ms/tok |
| **Embedding** | bge-m3 via oMLX API (1024-dim) | Zero extra RAM, 14ms/vec |
| **Database** | SQLite WAL + FTS5 + jieba | Zero-config, single-file, Chinese tokenizer |
| **Vector Index** | FAISS IndexFlatIP | Exact search, <10ms at 100K vectors |
| **Knowledge Graph** | NetworkX DiGraph | Pure Python, <2s rebuild |
| **Document Parsing** | markitdown + PyMuPDF + RapidOCR | Broad format support |
| **Web UI** | Streamlit | Pure Python, native dark mode |
| **CLI** | Click | Fast, composeable commands |
| **API** | FastAPI + SSE (streaming) | Async, type-safe |

## 📦 Supported File Formats

| Format | Extensions | Parser |
|--------|-----------|--------|
| Markdown | `.md`, `.markdown` | Native |
| Plain Text | `.txt` | Native |
| PDF | `.pdf` | PyMuPDF per-page + RapidOCR fallback |
| Word | `.docx` | markitdown |
| PowerPoint | `.pptx` | markitdown |
| Excel | `.xlsx` | markitdown |
| Code | `.py`, `.js`, `.ts`, `.go`, `.rs` | AST structure extraction |
| Data | `.csv`, `.json` | Native with schema inference |
| Image (OCR) | `.png`, `.jpg`, `.jpeg` | RapidOCR |

## 🔧 Configuration

Default config: `~/.filekb/config.yaml` (auto-created on first run).

### Key Settings

```yaml
llm:
  base_url: "http://127.0.0.1:8081/v1"   # oMLX or any OpenAI-compatible
  model: "daily-agent-best-mtp"          # model name in your LLM server

embedding:
  backend: "omlx"                        # "omlx" (via API) or "local" (sentence-transformers)
  model: "bge-m3-mlx-fp16"              # embedding model name
  omxl_url: "http://127.0.0.1:8081/v1"

directories:
  - path: "/path/to/your/docs"
    group: "工作"                         # KB group name
    recursive: true
    exclude_patterns: [".git", "__pycache__", ".DS_Store"]

database:
  path: "~/.filekb/filekb.db"

resilience:
  dlq:
    prune_days: 30                        # Auto-cleanup old DLQ entries
```

### Environment Variable Overrides

| Variable | Config Path |
|----------|------------|
| `FILEKB_LLM_URL` | `llm.base_url` |
| `FILEKB_LLM_MODEL` | `llm.model` |
| `FILEKB_CONFIG` | Custom config file path |
| `FILEKB_DB_PATH` | `database.path` |
| `FILEKB_LOG_LEVEL` | `logging.level` |

## 🍎 macOS Integration

- **Notification Center**: FileKB sends notifications on index completion, errors, and DLQ warnings.
- **Native Folder Picker**: The Web UI uses native macOS folder selection (AppKit bridge).
- **OCR**: RapidOCR with Chinese/English language support for scanned PDFs and images.

## 🧪 Development

```bash
# Clone and install in editable mode with dev deps
git clone https://github.com/webclock2000/localfile-kb.git
cd localfile-kb
pip install -e ".[dev]"

# Lint
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/filekb/

# Test
pytest                              # all tests
pytest -m "not integration"         # unit tests only
pytest -m integration               # integration tests
pytest --cov=filekb --cov-report=term-missing
```

## 📄 License

MIT License. See [LICENSE](LICENSE).

---

<p align="center">
  <sub>Built for people who want their knowledge to stay on their machine.</sub>
</p>
