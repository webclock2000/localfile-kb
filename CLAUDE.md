# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FileKB is a **file-driven, 100% local, zero-cloud personal knowledge base**. It monitors local directories, extracts structured knowledge (entity-relationship triples) via a local LLM, builds a queryable knowledge graph, and supports natural language Q&A with source provenance. Primary target: Apple Silicon Mac (M5 Max + 128GB) running oMLX + Qwen3.6-35B-A3B-MTP.

**Current status**: Design phase. No code written yet. [`docs/DEVELOPMENT_V3.md`](docs/DEVELOPMENT_V3.md) is the sole authoritative design document — all implementation decisions derive from it.

## Project Directory Structure

This project follows the **`src` layout** (recommended by [PyPA](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/), PEP 517/518/621) and the [Hypermodern Python](https://github.com/cjolowicz/cookiecutter-hypermodern-python) conventions. The `src` layout prevents accidental imports of the package source directory, forces `pip install -e .` during development, and ensures tests run against the *installed* package — not raw source files.

### Repository Structure (GitHub)

```
filekb/                              # Git repo root
│
├── .github/
│   └── workflows/
│       ├── tests.yml                # CI: lint + test on macOS/Linux
│       └── release.yml              # CD: build & publish to PyPI
│
├── src/
│   └── filekb/                      # ← The pip-installable package
│       ├── __init__.py
│       ├── __main__.py              # `python -m filekb` entry
│       ├── py.typed                 # PEP 561 type marker
│       │
│       │  # === Core engine ===
│       ├── config.py                # Pydantic v2 models, YAML load, env overrides
│       ├── store.py                 # SQLite WAL, schema, migrations, CRUD
│       ├── watcher.py               # SHA256 scan, three-way file diff
│       ├── parser.py                # Document parsing dispatch (markitdown/Docling)
│       ├── splitter.py              # Natural-boundary text chunking
│       ├── extractor.py             # Multi-round LLM extraction + Chinese routing
│       ├── dedup.py                 # Embedding cosine similarity dedup
│       ├── llm.py                   # OpenAI-compatible HTTP + tenacity retry
│       ├── resilience.py            # Error taxonomy, DLQ, checkpoint, adaptive_resplit
│       ├── personalization.py       # Feedback scorer, entity resolver, preference analyzer
│       ├── embed.py                 # bge-m3 lazy singleton (sentence-transformers)
│       ├── vector_store.py          # FAISS IndexFlatIP, save/load/rebuild
│       ├── graph_store.py           # NetworkX DiGraph, BFS, merge rewiring
│       ├── query.py                 # Hybrid retrieval + weighted ranking
│       ├── server.py                # FastAPI endpoints
│       ├── cli.py                   # Click CLI command group
│       ├── notify.py                # macOS notification center
│       │
│       ├── prompts/                 # Prompt templates (shipped inside package)
│       │   ├── extract.txt          # English entity-relation extraction
│       │   ├── extract_zh.txt       # Chinese extraction (P0, >80% user content)
│       │   └── answer.txt           # Q&A with source citation
│       │
│       └── ui/                      # Streamlit pages
│           ├── __init__.py
│           ├── chat.py              # Conversational Q&A + feedback buttons
│           ├── graph.py             # Interactive knowledge graph explorer
│           ├── files.py             # File list, status filter, stats cards
│           ├── entity_review.py     # Entity merge proposal review
│           └── status.py            # System health, DLQ, run history
│
├── tests/                           # All tests (mirrors src/filekb/ structure)
│   ├── __init__.py
│   ├── conftest.py                  # Shared fixtures, temp DB, test data paths
│   ├── test_splitter.py
│   ├── test_store.py
│   ├── test_watcher.py
│   ├── test_parser.py
│   ├── test_resilience.py
│   ├── test_feedback.py
│   ├── test_entity_resolver.py
│   ├── test_dedup.py
│   └── integration/                 # Integration tests (must pass before release)
│       ├── __init__.py
│       ├── test_index_pipeline_e2e.py
│       ├── test_fault_isolation.py
│       ├── test_checkpoint_resume.py
│       ├── test_query_feedback.py
│       └── test_chinese_extraction.py
│
├── tests/data/                      # Test fixtures — sample files for parsing
│   ├── sample.md
│   ├── sample_zh.md                 # Chinese test content
│   ├── sample.pdf
│   ├── sample.docx
│   ├── sample.py
│   └── sample.json
│
├── docs/                            # Design docs (NOT shipped with package)
│   ├── README.md                    # Document index
│   ├── PRD.md                       # Product requirements
│   ├── DEVELOPMENT_V3.md            # ⭐ Sole authoritative design document
│   └── Word_version/                # Word-format copies (same content as .md)
│
├── pyproject.toml                   # Build config, deps, tool settings
├── LICENSE                          # MIT
├── README.md                        # GitHub landing page (user-facing quick start)
├── CLAUDE.md                        # This file — Claude Code guidance
├── .gitignore
└── .gitattributes
```

### What Gets Published Where

| Target | Includes | Excludes |
|--------|----------|----------|
| **GitHub** | Everything above | `.pytest_cache/`, `__pycache__/`, `.DS_Store`, `dist/`, `*.egg-info/` |
| **PyPI** (`.whl` / `.tar.gz`) | `src/filekb/` + `pyproject.toml` + `README.md` + `LICENSE` | `tests/`, `docs/`, `.github/`, `CLAUDE.md` |

### `.gitignore` Patterns

```gitignore
# Bytecode
__pycache__/
*.pyc
*.pyo

# Virtual environments
.venv/
venv/
.env

# Build artifacts
dist/
build/
*.egg-info/

# Runtime data (never in repo — lives at ~/.filekb/)
*.db
*.log
.faiss_cache/

# OS
.DS_Store
Thumbs.db

# IDE
.vscode/
.idea/

# pytest
.pytest_cache/
.coverage
htmlcov/

# Models (downloaded at runtime, not committed)
models/
```

### Runtime Data Directory (NOT in the repo)

All user data, generated indices, and downloaded models live under `~/.filekb/` — **never inside the repo directory**:

```
~/.filekb/
├── config.yaml                     # User configuration (Pydantic-validated)
├── filekb.db                       # SQLite database (WAL mode)
├── filekb.log                      # Application logs
├── models/                         # Downloaded ML models
│   └── deepke-cn/                  # DeepKE RoBERTa-wwm-ext (~400 MB)
└── faiss/                          # FAISS index files (persisted to disk)
```

The path `~/.filekb/` is the default; it can be overridden via `FILEKB_CONFIG` or `FILEKB_DB_PATH` environment variables.

## Development Commands

This project uses the `src` layout — you **must** install in editable mode before running tests or the app:

```bash
# First-time setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"             # editable install with dev deps

# Lint (zero tolerance for violations)
ruff check src/ tests/
ruff format --check src/ tests/

# Type check (strict mode)
mypy src/filekb/

# Run tests
pytest                              # all tests
pytest tests/test_store.py          # single test file
pytest -k "test_chunk"              # pattern match
pytest -m "not integration"         # unit tests only
pytest -m integration               # integration tests only
pytest --cov=filekb --cov-report=term-missing

# Build for distribution
python -m build                     # produces dist/*.whl and dist/*.tar.gz

# Run the app
filekb index --watch                # scan & index changed files
filekb ask "question"               # single Q&A
filekb graph ENTITY --hop 2         # graph exploration
filekb ui                           # launch Streamlit Web UI (starts FastAPI internally)
```

## Architecture

### Layered Design (top to bottom)

```
INTERFACE LAYER
  CLI (click)  |  Web UI (Streamlit)  |  REST API (FastAPI)
       └─────────────────┬──────────────────┘
PERSONALIZATION LAYER
  Preference Memory → Feedback Scorer → Entity Resolver
QUERY ENGINE (query.py)
  Vector(FAISS) → Graph(NetworkX) → FTS(SQLite) → Merge → LLM
STORAGE LAYER
  SQLite (+FTS5+jieba)  |  FAISS IndexFlatIP  |  NetworkX DiGraph
RESILIENCE LAYER
  finish_reason detection → adaptive_resplit → checkpoint → DLQ
EXTRACTION LAYER
  Multi-round LLM → Incremental JSON → Semantic Dedup → DeepKE filter (Chinese)
FILE LAYER
  Watcher (SHA256 diff) → Parser (markitdown/Docling) → Splitter
MODEL LAYER
  LLM Client (llm.py, tenacity retry)  |  Embedding (bge-m3, sentence-transformers)
```

### Data Flow: Indexing

1. `scan_directories()` → list all files in configured directories
2. `detect_changes()` → SHA256 three-way diff → classify as ADD/MOD/DEL/unchanged
3. `parse_file()` → markitdown → Docling fallback → pymupdf (last resort)
4. `chunk_text()` → natural boundary split (24,000 chars max, 500 char overlap)
5. `detect_chinese()` → if >50% CJK → route to `extract_zh.txt` prompt
6. `extract_facts()` × k rounds (default k=2) → incremental JSON parse → semantic dedup (cosine 0.90)
7. `commit_chunk()` → atomic SQLite write + FTS5 auto-update
8. DeepKE filter (Chinese content entity boundary validation)
9. File-level checkpoint → mark file `done`
10. After all files: `rebuild_indices()` (FAISS + NetworkX graph rebuild)

### Data Flow: Query

1. `embed_single(question)` via bge-m3
2. FAISS vector search (top 2k) + SQLite FTS5 search (top 10)
3. Weighted merge: `score = 0.5×vector + 0.3×graph + 0.2×fts + 0.1×log(user_score)`
4. Graph expansion (1-hop from seed entities)
5. Collect context chunks + preference memories
6. `LLM.generate_answer()` with source citations

### Key Architectural Constraints

- Streamlit pages **never** import `store.py` or `llm.py` directly — all state changes go through FastAPI → Core Engine
- FastAPI runs on `http://localhost:8000`, Streamlit calls it via `requests`/`httpx`
- No pre-flight token counting — rely on splitter's structural guarantee (24K chars) + reactive `finish_reason == "length"` detection
- Privacy: zero network calls except to local oMLX server; no telemetry, no analytics

## Module Map

| Module | Responsibility |
|--------|---------------|
| `config.py` | Pydantic v2 models, YAML loading, env var overrides |
| `store.py` | SQLite WAL mode, schema, migrations, CRUD (`PRAGMA user_version`) |
| `watcher.py` | SHA256 file scanning, three-way diff (add/modify/delete/unchanged) |
| `parser.py` | Dispatch table: `.md/.txt` native, `.pdf` markitdown→Docling→pymupdf, `.docx/.xlsx/.pptx` markitdown, `.py/.js/.ts/.go/.rs` code structure |
| `splitter.py` | Natural boundary chunking (paragraph→line→sentence→char), 24K char max, 500 char overlap |
| `extractor.py` | Multi-round LLM extraction, incremental JSON parse, semantic dedup, Chinese branch routing |
| `dedup.py` | Embedding cosine similarity dedup (threshold 0.90) |
| `llm.py` | OpenAI-compatible HTTP client, tenacity retry, `finish_reason` detection, adaptive_resplit |
| `resilience.py` | Error classification (5 types), tenacity retry orchestration, overflow recovery, checkpoint, DLQ |
| `personalization.py` | Feedback scoring, entity resolution, preference analysis from query logs |
| `embed.py` | Lazy singleton `SentenceTransformer` for bge-m3, CPU/MPS |
| `vector_store.py` | FAISS `IndexFlatIP`, incremental add, save/load/rebuild |
| `graph_store.py` | NetworkX `DiGraph`, BFS expansion, entity search, merge rewiring |
| `query.py` | Hybrid retrieval, weighted scoring, graph expansion, answer generation |
| `server.py` | FastAPI endpoints (see API spec in DEVELOPMENT_V3.md §12) |
| `ui/*.py` | Streamlit pages: chat, graph, files, entity_review, status |
| `cli.py` | Click command group: index, ask, graph, status, review-entities, ui |
| `notify.py` | macOS notification center integration |

## Code Reuse Strategy (from ADR-8)

The design mandates a three-tier reuse policy. Do NOT write from scratch when an upstream source exists.

### Tier 1 — Direct integration (`pip install`)

These are well-known problems with MIT/Apache-2.0-licensed libraries. Install them; do not reinvent.

| Dependency | Role | License |
|-----------|------|---------|
| `tenacity` | LLM call retry with exponential backoff + jitter | Apache 2.0 |
| `pydantic` | Config validation (v2 schema) | MIT |
| `sentence-transformers` | bge-m3 embedding (1024-dim) | Apache 2.0 |
| `faiss-cpu` | Vector index (IndexFlatIP) | MIT |
| `networkx` | In-memory knowledge graph (DiGraph) | BSD |
| `markitdown` | Document parsing (.docx/.xlsx/.pptx/.html) | MIT |
| `docling` | PDF table extraction (fallback when markitdown fails) | MIT |
| `jieba` | Chinese word segmentation (SQLite FTS5 tokenizer) | MIT |
| `streamlit` | Web UI | Apache 2.0 |
| `fastapi` | REST API layer | MIT |
| `click` | CLI command group | BSD |
| `deepke` | Chinese entity boundary guardrail (RoBERTa-wwm-ext) | MIT |

### Tier 2 — Borrow pattern, rewrite for FileKB

These upstream projects provide the **algorithm pattern** — study their code, then write a FileKB-specific implementation that fits our data model (SQLite + FAISS + NetworkX). Do NOT copy-paste entire files; adapt the logic.

| Module | Study This Source | What to Reuse | License |
|--------|------------------|---------------|---------|
| `extractor.py` → `extract.txt` prompt | [hanxiao/knowledge-graph-extractor](https://github.com/hanxiao/knowledge-graph-extractor) | Keep the 80-line English extraction prompt verbatim (Qwen3.6-35B battle-tested). Chunking algorithm is NOT from this repo (see Appendix A correction). | MIT |
| `personalization.py` → feedback scorer | [RAGFlow PR #12689](https://github.com/infiniflow/ragflow/pull/12689) | The `allocate_delta()` pattern: distribute feedback score delta across cited facts proportional to their retrieval relevance. Rewrite for SQLite `user_score` column + `user_feedback` table. | Apache 2.0 |
| `personalization.py` → entity resolver | [sift-kg](https://github.com) | The three-stage merge workflow: (1) FAISS pre-filter top-10 neighbors per entity, (2) LLM validate yes/no/uncertain, (3) bulk-apply rewiring via SQL UPDATE. Rewrite for `entity_proposals` table schema. | MIT |
| `query.py` → hybrid merge | [LightRAG](https://github.com/HKUDS/LightRAG) | Dual-level retrieval pattern (vector + graph). Adapt their scoring but use our weights (0.5×vector + 0.3×graph + 0.2×fts + 0.1×log(user_score)). | MIT |
| `splitter.py` | Original design | Natural boundary chunking (paragraph → line → sentence → character) with 24K char max and 500 char overlap. NOT from knowledge-graph-extractor. | — |

### Tier 3 — Architecture reference only (do NOT copy code)

These projects inform design decisions but their code is NOT directly reusable — different tech stack, different scale, or license incompatibility.

| Reference | What It Informs | Why NOT Reusable |
|-----------|----------------|------------------|
| [KAG](https://github.com/OpenSPG/KAG) | Logical-form guided reasoning concept | Over-engineered for personal KB; requires Neo4j + multiple services |
| [Mem0](https://github.com/mem0ai/mem0) | Memory layer architecture | Cloud-native multi-tenant design; incompatible with local-first architecture |
| YAYI-UIE entity type taxonomy | `extract_zh.txt` prompt design (28 entity types, 232 relations) | Requires CUDA (Baichuan2 + 33GB VRAM); Apple Silicon incompatible |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | Minimalist chat UI design | JavaScript/React stack; FileKB is pure Python |
| [Dify](https://github.com/langgenius/dify) | Streaming chat UX, feedback button placement | Next.js/React; reference for interaction patterns only |

### Before writing any module, check:

1. Is there a **Tier 1** pip package that already solves this? → Install it.
2. Is there a **Tier 2** upstream pattern for this logic? → Study their approach, then adapt to our data model.
3. If neither, write original code — but check that the design doesn't already cover it in DEVELOPMENT_V3.md.

## Key Design Decisions (from ADRs)

- **SQLite + WAL** over PostgreSQL/MySQL — zero-config, single-file, FTS5 with jieba tokenizer
- **FAISS IndexFlatIP** over HNSW — exact search sufficient at <100K vectors (<10ms), no approximation risk
- **NetworkX DiGraph** over Neo4j — pure Python, <2s rebuild from SQLite, sufficient for personal scale
- **Streamlit** over React/Next.js — pure Python, single-developer maintainable, FastAPI layer allows future frontend swap
- **SHA256 content hash** over mtime — deterministic, immune to git checkout resetting timestamps
- **Reactive overflow detection** over tiktoken/transformers preflight — local tokenizers can't predict oMLX actual token counts (33% variance observed); splitter structural guarantee makes overflow extremely rare

## Chinese Content Pipeline (P0 priority)

>80% of user content is Chinese. The Chinese pipeline is:

1. `detect_chinese()` — >50% CJK characters → activate Chinese branch
2. `prompts/extract_zh.txt` — Chinese-specific extraction prompt with entity boundary rules (compound surnames, organization suffixes, names with titles)
3. DeepKE RoBERTa-wwm-ext — post-LLM entity boundary guardrail (LLM vs DeepKE disagreement → confidence × 0.8 → entity_proposals queue)
4. SCIR self-correcting framework (P1, future)

YAYI-UIE and OneKE are **not usable** on Apple Silicon (both require CUDA).

## Reliability Model

- **Error classification**: CONTEXT_LENGTH_EXCEEDED | RATE_LIMIT/TIMEOUT | INVALID_JSON | MODEL_UNAVAILABLE | UNKNOWN
- **Recovery**: tenacity exponential backoff with jitter (3 attempts max), DLQ for exhausted retries
- **Checkpointing**: file-level atomic SQLite transactions (`UPDATE files/chunks SET status='done'`)
- **Crash recovery**: on restart, chunks with `status='processing'` → reset to `pending` and re-process
- **No silent skips**: every skipped file recorded with `error_msg`, visible in notifications and `filekb status --skipped`

## Configuration

Default config: `~/.filekb/config.yaml`. Pydantic v2 schema with full validation. Key env var overrides:
- `FILEKB_LLM_URL` → `llm.base_url`
- `FILEKB_LLM_MODEL` → `llm.model`
- `FILEKB_CONFIG` → custom config file path
- `FILEKB_DB_PATH` → `database.path`

See [`docs/DEVELOPMENT_V3.md` §13](docs/DEVELOPMENT_V3.md#13-配置参考) for full configuration reference.

## Important References

- [`docs/DEVELOPMENT_V3.md`](docs/DEVELOPMENT_V3.md) — **Sole authoritative design document**. Read this first for any implementation work.
- [`docs/PRD.md`](docs/PRD.md) — Product requirements, user stories, success metrics
- [`docs/README.md`](docs/README.md) — Document index and reading guide
- Upstream projects (reference only): [knowledge-graph-extractor](https://github.com/hanxiao/knowledge-graph-extractor), [LightRAG](https://github.com/HKUDS/LightRAG), [KAG](https://github.com/OpenSPG/KAG)
