# Changelog

All notable changes to FileKB will be documented in this file.

## [0.1.1] — 2026-06-20

### Added

- **DLQ auto-pruning**: `failed_chunks` records older than `prune_days` (default 30) are automatically cleaned up at the end of each index run, preventing unbounded table growth. The `resilience.dlq.prune_days` config key is now live. (#7)
- **CLI single-file index**: `filekb index --file PATH` hot path for re-indexing one file without a full scan. (#4)
- **Chat history persistence**: Q&A history stored in SQLite `chat_history` table, accessible via API and UI. (#5)
- **Entity quality assessment**: Post-index scan of all entities for boundary integrity and quality issues, using DeepKE RoBERTa-wwm-ext guardrail and jieba segmentation (#6).
- **APP/PPT/JPG parsing**: PowerPoint (.pptx) and JPEG image (via OCR) support added to the parser dispatch table.

### Changed

- **CLI architecture**: All CLI commands (`index`, `ask`, `graph`, `status`) now talk exclusively to the FastAPI backend via HTTP — no direct imports of `store.py` or `llm.py`. This enforces the architectural constraint that Streamlit never imports core engine modules. (#4)
- **Startup**: `filekb ui` now starts the FastAPI backend automatically (previously required manual `uvicorn` launch).
- **Dependency cleanup**: Removed dead dependencies from `pyproject.toml`:
  - `docling` (design doc reference; actual PDF parsing uses PyMuPDF + markitdown fallback)
  - `sse-starlette` (Streamlit natively handles streaming)
  - `sqlite-fts4` (SQLite FTS5 built into Python's sqlite3; no extra package needed)
- **Documentation**: `embed.py` docstring corrected — default backend is oMLX API, not local sentence-transformers. Actual model is `bge-m3-mlx-fp16`.

### Fixed

- FAISS embeddings now correctly persisted to per-KB `~/.filekb/faiss/<kb_name>/` directories.
- Chunk cleanup on file deletion: soft-deleting a file now properly removes its orphaned chunks.
- Foreign key constraint violations in entity merge operations.
- `NameError` from helper function ordering in Web UI entity review page.
- KB switching in Web UI sidebar now properly refreshes all pages.
- `user_score` weighting (`log(user_score)`) now correctly applied in hybrid retrieval ranking.
- CLI now resolves default KB name from the running server's config, not hardcoded `"默认"`.

## [0.1.0] — 2026-06-19

### Initial Release

- File monitoring: SHA256-based three-way diff (add/modify/delete/unchanged)
- Document parsing: Markdown, Plain Text, PDF (PyMuPDF + markitdown fallback + OCR), Word, Excel, Code (Python/JS/TS/Go/Rust AST extraction), CSV, JSON
- Smart chunking: Natural boundaries (paragraph → line → sentence → character), 24K char max, 500 char overlap
- Chinese pipeline: CJK detection (>50% threshold), Chinese-specific extraction prompt, jieba FTS5 tokenizer
- Multi-round LLM extraction (k=2) with incremental JSON parsing
- Semantic deduplication via embedding cosine similarity (threshold 0.90)
- Knowledge graph: NetworkX DiGraph, BFS expansion, entity resolution with LLM validation
- Hybrid retrieval: FAISS vector search + SQLite FTS5 + graph expansion → weighted merge (0.5/0.3/0.2/0.1)
- oMLX integration: LLM and embedding via OpenAI-compatible API (bge-m3-mlx-fp16, 1024-dim)
- Apple Vision OCR for scanned PDFs and images (macOS only)
- User feedback scoring with per-fact `user_score`, delta propagation, and weekly decay
- Resilience: 5-class error taxonomy, tenacity retry with exponential backoff, Dead Letter Queue
- Multi-KB isolation: Independent SQLite DB, FAISS index, and knowledge graph per KB group
- CLI (Click): `index`, `ask`, `graph`, `status`, `ui` commands
- Web UI (Streamlit): Conversational Q&A, interactive knowledge graph explorer, file management, system health panel, entity merge review
- FastAPI REST API: `/ask`, `/ask/stream`, `/graph`, `/index`, `/status`, `/facts`, `/files`, `/feedback`, `/settings`, `/entities/*`
- macOS Notification Center integration
- Full test suite: 65 unit tests + 5 integration tests
