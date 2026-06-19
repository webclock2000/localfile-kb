# FileKB — Product Requirements Document (PRD)

> **版本**: 1.0  
> **状态**: Draft  
> **日期**: 2026-06-18  
> **作者**: FileKB Contributors  

---

## 1. Product Overview

### 1.1 Product Elevator Pitch

FileKB is a **file-driven personal knowledge base** that automatically builds a queryable
knowledge graph from your local files — without sending anything to the cloud.

### 1.2 Problem Statement

Knowledge workers accumulate thousands of documents across their local filesystem —
meeting notes, project specs, research papers, code repositories, personal journals.
Existing solutions fall into two camps:

| Solution | Pain Point |
|----------|------------|
| **Cloud knowledge bases** (Notion, Obsidian Sync) | Privacy risk — all data leaves your machine; subscription cost; vendor lock-in |
| **Local note apps** (Obsidian local, Logseq) | Manual linking — you must curate every connection; no automatic extraction |
| **Enterprise RAG** (RAGFlow, KAG) | Over-engineered for personal use; need Docker, multiple services, cloud components |
| **Manual grep / Spotlight** | Keyword-only; no semantic understanding; no relationship discovery |

**Core insight**: LLMs can now run locally on consumer hardware (M5 Max + oMLX + Qwen3.6-35B).
This makes it feasible to automatically extract structured knowledge from personal files
and build a queryable knowledge graph with zero cloud dependency.

### 1.3 Product Vision

A knowledge base that feels like a **librarian who has read every file on your computer** —
you ask questions in natural language, get answers with source citations, and discover
connections between documents you didn't know existed.

### 1.4 Target Users

| Persona | Description | Primary Use Case |
|---------|-------------|------------------|
| **Independent Researcher** | Reads dozens of papers, takes notes across files | "What does paper X say about method Y? What other papers use similar approaches?" |
| **Software Engineer** | Manages multiple projects, design docs, codebases | "What decisions did we make about the auth system? Where is that documented?" |
| **Writer / Content Creator** | Accumulates research materials, drafts, references | "What sources do I have on topic X? How do they relate?" |
| **Self-Quantified Individual** | Journals, logs, tracks life across files | "What patterns appear in my journal over the last 6 months?" |

### 1.5 Core Value Proposition

1. **Zero effort knowledge extraction** — Drop files in a directory. The system reads, parses, and extracts knowledge automatically.
2. **Provenance-first** — Every answer cites exactly which file and which passage it came from. No hallucinated sources.
3. **100% local** — No cloud API keys. No data leaves your machine. Works offline.
4. **Relationship discovery** — The knowledge graph reveals connections across files that you'd never find with grep.

---

## 2. User Stories

### 2.1 Epic: File Management

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| FM-01 | As a user, I want to add directories for the system to watch | P0 | Configure one or more directory paths; system scans them on next index run |
| FM-02 | As a user, I want newly added files to be automatically indexed | P0 | New file detected → parsed → facts extracted → integrated into KB |
| FM-03 | As a user, I want modified files to be re-indexed with old facts replaced | P0 | Modified file detected → old facts removed → new facts extracted → KB updated |
| FM-04 | As a user, I want deleted files to be reflected in the KB | P1 | Deleted file detected → associated facts marked deleted → no longer appear in queries |
| FM-05 | As a user, I want unchanged files to be skipped during indexing | P1 | SHA256 match → skip; avoid re-parsing files that haven't changed |
| FM-06 | As a user, I want to exclude certain patterns from indexing | P2 | `.git`, `node_modules`, `__pycache__`, and user-configured patterns are skipped |
| FM-07 | As a user, I want to index a single file on demand | P2 | `filekb index --file report.pdf` — indexes immediately without full scan |

### 2.2 Epic: Document Parsing

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| DP-01 | As a user, I want Markdown files to be parsed correctly | P0 | `.md` files → full text extraction |
| DP-02 | As a user, I want PDF files to be parsed including tables | P0 | `.pdf` files → text extraction; complex tables fall back to Docling |
| DP-03 | As a user, I want code files to be summarized structurally | P1 | `.py/.js/.ts/.go/.rs` → docstrings, function signatures, imports extracted; implementation details excluded |
| DP-04 | As a user, I want Office documents to be parsed | P1 | `.docx/.xlsx/.pptx` → text extraction via markitdown |
| DP-05 | As a user, I want structured data files to be readable | P2 | `.csv` → table summary; `.json` → truncated raw text |
| DP-06 | As a user, I want files >50MB to be gracefully skipped | P2 | Large file → logged as skipped, no crash, no partial index |

### 2.3 Epic: Knowledge Extraction

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| KE-01 | As a user, I want entities and relationships extracted from text | P0 | Text → (subject, predicate, object) triples with title and description |
| KE-02 | As a user, I want confidence scores on extracted facts | P1 | Each fact has a 0-100 confidence score from the LLM |
| KE-03 | As a user, I want near-duplicate facts to be merged | P1 | "Alice works at Acme" and "Alice is employed by Acme" → one fact kept |
| KE-04 | As a user, I want every fact to trace back to its source | P0 | Each fact has source file path, chunk reference, and evidence span |
| KE-05 | As a user, I want entity names to be consistent across the KB | P2 | "Alice" and "Alice" in different files → same graph node |
| KE-06 | As a user, I want extraction to cover multiple rounds for completeness | P2 | k=2 rounds with different seeds → 30-40% higher fact recall |

### 2.4 Epic: Query & Answer

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| QA-01 | As a user, I want to ask natural language questions | P0 | Input: "What did I decide about the API design?" → Output: answer + sources |
| QA-02 | As a user, I want answers to cite their source files | P0 | Every claim in the answer cites a file path and passage |
| QA-03 | As a user, I want to search by keyword as well as semantics | P1 | Question matches both semantically similar AND keyword-similar facts |
| QA-04 | As a user, I want the system to be honest when it doesn't know | P1 | No relevant context → "The knowledge base doesn't have information about this" |
| QA-05 | As a user, I want to discover related entities and connections | P1 | Answer highlights cross-file relationships between entities |
| QA-06 | As a user, I want streaming answers for faster feedback | P2 | Tokens appear incrementally rather than waiting for full generation |

### 2.5 Epic: Knowledge Graph

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| KG-01 | As a user, I want to explore an entity's connections | P1 | Input: entity name → Output: related entities with predicate-labeled edges |
| KG-02 | As a user, I want to expand multiple hops from an entity | P2 | `--hop 2` shows entity → direct connections → connections of connections |
| KG-03 | As a user, I want fuzzy entity search | P1 | "alice" matches "Alice", "Alice Smith" |

### 2.6 Epic: Interfaces

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| IF-01 | As a CLI user, I want a simple command-line interface | P0 | `filekb {index, ask, graph, status, ui}` with `--help` |
| IF-02 | As a web user, I want a browser-based chat interface | P1 | Streamlit UI with chat history, source expanders, and graph explorer |
| IF-03 | As a developer, I want a REST API for integration | P1 | FastAPI endpoints: POST /ask, GET /graph, POST /index, GET /status, GET /facts |
| IF-04 | As a macOS user, I want system notifications after indexing | P2 | Notification Center popup with files added/modified/deleted counts |

### 2.7 Epic: Scheduling

| ID | Story | Priority | Acceptance Criteria |
|----|-------|----------|---------------------|
| SC-01 | As a user, I want indexing to run automatically at night | P1 | launchd job runs `filekb index --watch` at 2:07 AM local time |
| SC-02 | As a user, I want to trigger indexing manually when needed | P0 | `filekb index --watch` runs immediately |

---

## 3. Functional Requirements

### 3.1 FR-01: Directory Configuration

The system SHALL accept a YAML configuration file specifying:
- One or more directory paths to monitor
- Per-directory recursive flag (default: true)
- Per-directory exclusion patterns (glob syntax)
- File type filter (list of extensions)

### 3.2 FR-02: Change Detection

The system SHALL:
- Compute SHA256 hash for each file in monitored directories
- Compare against stored hashes in the database
- Classify each file as: `added`, `modified`, `deleted`, or `unchanged`
- Process only changed files during indexing

### 3.3 FR-03: Document Parsing

The system SHALL parse the following file formats to plain text:

| Format | Parser | Priority |
|--------|--------|----------|
| `.md`, `.txt`, `.rst` | Built-in UTF-8 reader | P0 |
| `.py`, `.js`, `.ts`, `.go`, `.rs` | Code structure extractor (docstrings + signatures) | P1 |
| `.pdf` | markitdown → Docling fallback (table detection) → pymupdf | P0 |
| `.docx`, `.xlsx`, `.pptx`, `.html` | markitdown | P1 |
| `.csv` | Built-in table summarizer | P2 |
| `.json` | Built-in (truncated raw) | P2 |

The system SHALL skip files larger than 50 MB and files with unsupported extensions.

### 3.4 FR-04: Text Chunking

The system SHALL:
- Split parsed text into chunks at natural boundaries (paragraph → line break → sentence → character)
- Enforce a configurable maximum chunk size (default: 24,000 characters)
- Add overlap between consecutive chunks (default: 500 characters)
- Preserve chunk ordering within each file

### 3.5 FR-05: Knowledge Extraction

The system SHALL:
- Send each chunk to the configured LLM for fact extraction
- Run k rounds per chunk with different random seeds (default k=2)
- Parse JSON responses into structured `(subject, predicate, object)` triples
- Each fact SHALL include: title, description, evidence span, confidence score, and tags
- Apply semantic deduplication using embedding cosine similarity (threshold: 0.90)
- Store facts with source file path and chunk reference for provenance

### 3.6 FR-06: Storage

The system SHALL maintain:
- A SQLite database with tables: `directories`, `files`, `chunks`, `facts`, `facts_fts`, `runs`
- A FAISS vector index for semantic search
- A NetworkX directed graph for entity relationship traversal
- Soft deletes: `deleted` status rather than row removal

### 3.7 FR-07: Query

The system SHALL:
- Accept a natural language question
- Embed the question using bge-m3
- Retrieve candidate facts via:
  - FAISS vector similarity (top 2k)
  - SQLite FTS5 full-text search (top 10)
- Merge results with weighted scoring: α=0.5×vector + β=0.3×graph + γ=0.2×fts
- Expand through knowledge graph (1-hop from seed entities)
- Collect relevant source chunks as context
- Generate an answer via LLM with source citations

### 3.8 FR-08: Answer Format

Every answer SHALL:
- Be based only on the provided context (no hallucination)
- Cite source files for each claim
- State clearly when information is missing
- Highlight entity connections when relevant

### 3.9 FR-09: Configuration

The system SHALL:
- Load configuration from `~/.filekb/config.yaml` by default
- Accept override via `--config` flag and `FILEKB_CONFIG` env var
- Support environment variable overrides for key settings (`FILEKB_LLM_URL`, etc.)
- Validate configuration on load (Pydantic schema validation)

---

## 4. Non-Functional Requirements

### 4.1 Performance

| Metric | Target | Rationale |
|--------|--------|-----------|
| File scanning (10K files) | < 30 seconds | SHA256 computation is I/O bound |
| Document parsing (100-page PDF) | < 60 seconds | Docling is the bottleneck |
| Fact extraction (per chunk) | < 30 seconds | LLM inference is the bottleneck |
| Full index run (100 files) | < 10 minutes | Nightly batch — acceptable |
| Query latency (end-to-end) | < 30 seconds | Embedding + retrieval + LLM generation |
| FAISS vector search (100K vectors) | < 10 ms | IndexFlatIP exact search |
| Graph rebuild (100K facts) | < 2 seconds | NetworkX from SQLite |

### 4.2 Reliability

| Requirement | Target |
|-------------|--------|
| Crash recovery | Last run marked as `crashed` on next startup; next run processes all files |
| Partial write safety | SQLite WAL mode ensures atomic commits |
| LLM failure handling | Failed chunk extraction skips chunk, continues with remaining chunks |
| Disk space safety | Warn at 50MB DB; delete old run records above 100 entries |

### 4.3 Privacy & Security

| Requirement | Implementation |
|-------------|---------------|
| Zero data exfiltration | All processing is local; no network calls except to local LLM server |
| No analytics | No telemetry, no usage tracking, no phone-home |
| File permissions respected | Only reads files accessible to the running user |
| Config isolation | `FILEKB_CONFIG` env var allows per-project KB instances |

### 4.4 Maintainability

| Requirement | Target |
|-------------|--------|
| Test coverage | >80% on storage, parsing, chunking, and change detection |
| Code style | Ruff linting, 100-char line length |
| Python version | 3.12+ (modern syntax) |
| Dependencies | Pinned with minimum versions in pyproject.toml |

### 4.5 Compatibility

| Platform | Status |
|----------|--------|
| macOS (Apple Silicon) | Primary target; Metal-accelerated FAISS |
| macOS (Intel) | Supported via faiss-cpu |
| Linux | Supported |
| Windows (WSL) | Supported |

---

## 5. Scope Boundaries

### 5.1 In Scope

- Monitoring local directories for file changes
- Parsing text-based and common office document formats
- Extracting entity-relationship triples via local LLM
- Building and querying a knowledge graph
- Natural language Q&A with source provenance
- Nightly batch indexing via launchd
- CLI, REST API, and Streamlit web UI
- pip-installable Python package

### 5.2 Out of Scope (v1)

- **Real-time file watching**: Nightly batch only. Real-time adds complexity (fsevents, partial write handling) with marginal benefit for personal KB.
- **Multi-user support**: Single-user, single-machine. Multi-user requires auth, permissions, concurrent write handling.
- **Cloud sync**: No built-in sync mechanism. Use your own backup solution for `~/.filekb/`.
- **Image/video/audio**: Only text-based documents. Multimodal support requires a vision-capable LLM.
- **OCR for scanned PDFs**: Text-layer PDFs only. Scanned documents require Tesseract/Docling OCR integration.
- **Web content ingestion**: Files only, no URL fetching. Could be a future plugin.
- **Email/messaging integration**: No direct ingestion from Mail.app or messaging apps.
- **Collaborative editing**: No shared KB, no real-time collaboration.
- **Mobile access**: Desktop only. No iOS/Android client.
- **Plugin system**: Fixed parser/extractor pipeline. Plugin architecture adds complexity without v1 user need.

---

## 6. User Scenarios

### 6.1 Scenario: Researcher's First Use

1. Alice is a PhD student with 200 papers in `~/Papers/` and notes in `~/Notes/`.
2. She installs FileKB: `pip install filekb`
3. She edits `~/.filekb/config.yaml`:
   ```yaml
   directories:
     - path: ~/Papers
     - path: ~/Notes
   ```
4. She runs `filekb index --watch`
5. System scans 200 PDFs + 50 Markdown files, parses them, extracts facts, builds indices
6. She receives a macOS notification: "Indexing complete. 250 files, 4,200 facts, 1,800 entities."
7. She asks: "Which papers discuss transformer attention mechanisms?"
8. System responds with a list of papers, key findings, and direct quotes with source file citations

### 6.2 Scenario: Daily Incremental Update

1. Bob adds 3 new meeting notes to `~/Notes/` and edits 1 existing document.
2. At 2:07 AM, launchd triggers `filekb index --watch`
3. System scans directories, detects 4 changed files (3 new, 1 modified), skips 247 unchanged files
4. New facts are extracted; old facts from the modified file are replaced
5. Indices are rebuilt; Bob wakes up to a notification: "Indexed 4 files. 57 new facts added."

### 6.3 Scenario: Cross-File Discovery

1. Carol asks: "What do I know about the Q3 budget?"
2. System finds:
   - A spreadsheet in `~/Finance/budget-2026.xlsx` with Q3 figures
   - Meeting notes in `~/Notes/2026-07-15.md` discussing budget cuts
   - A project plan in `~/Projects/alpha/plan.md` referencing budget constraints
3. Answer connects all three sources: "Per budget-2026.xlsx, Q3 budget is $450K. The July 15 meeting notes indicate a proposed 10% cut. The Alpha project plan cites budget as a schedule risk."
4. Carol discovers a connection she hadn't explicitly made: the Alpha project risk and the July meeting are about the same budget discussion.

### 6.4 Scenario: Graph Exploration

1. Dave wants to understand everything related to "Project Phoenix" in his KB.
2. He runs: `filekb graph "Project Phoenix" --hop 2`
3. System shows:
   - Direct connections: team members, deadline, tech stack, related documents
   - 2-hop connections: each team member's other projects, shared technologies across projects
4. Dave discovers that two team members previously worked together on a related project — a connection he hadn't noticed.

---

## 7. Success Metrics

### 7.1 Quantitative

| Metric | Target | Measurement |
|--------|--------|-------------|
| Fact extraction precision | >80% manually verified as correct | Spot-check 100 random facts |
| Fact extraction recall | >70% of human-identifiable facts extracted | Compare against manual extraction on 10 test documents |
| Answer relevance | >80% of answers rated "useful" | User self-assessment |
| Source citation accuracy | >95% of cited sources are correct | Manual verification |
| Dedup effectiveness | <5% near-duplicate facts in KB | Sample-based counting |
| Index throughput | >100 files/hour on M5 Max | Wall-clock measurement |
| Query latency (median) | <15 seconds end-to-end | Instrumented timing |

### 7.2 Qualitative

| Metric | Signal |
|--------|--------|
| User trust | User stops verifying every citation; accepts answers at face value |
| Discovery value | User reports finding connections they didn't know existed |
| Daily use | User asks questions daily rather than using grep/Spotlight |
| Knowledge accumulation | User adds more directories over time as trust grows |

---

## 8. Glossary

| Term | Definition |
|------|------------|
| **Fact** | A (subject, predicate, object) triple extracted from a document, with metadata (title, description, confidence, evidence span, source file) |
| **Entity** | A canonical name for a real-world thing (person, organization, project, concept) — used as subject or object in a fact |
| **Predicate** | A relationship type in snake_case, e.g. `works_at`, `authored_by`, `depends_on` |
| **Chunk** | A segment of document text, split at natural boundaries, that fits within the LLM's context window |
| **Knowledge Graph** | A directed graph where nodes are entities and edges are facts (with predicate labels) |
| **Hybrid Retrieval** | Combining vector similarity, full-text search, and graph proximity to rank facts for a query |
| **Semantic Dedup** | Using embedding cosine similarity to identify and merge near-duplicate facts |
| **Provenance** | The traceability of every fact back to its source file and text passage |
| **Indexing Run** | A single execution of the index pipeline: scan → detect → parse → extract → rebuild indices |
| **oMLX** | Apple MLX-based local LLM serving with OpenAI-compatible API |
| **RRF** | Reciprocal Rank Fusion — a method for merging ranked lists from different retrieval methods |

---

## 9. References

| Document | Description |
|----------|-------------|
| [DEVELOPMENT.md](DEVELOPMENT.md) | Technical design: architecture, ADRs, module specs, data models |
| [README.md](README.md) | User-facing quick start: installation, configuration, usage |
| [config.example.yaml](config.example.yaml) | Full configuration template with documentation |
| [knowledge-graph-extractor](https://github.com/hanxiao/knowledge-graph-extractor) | Upstream project for extraction prompt and chunking algorithm |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Reference architecture for dual-level graph retrieval |
| [KAG](https://github.com/OpenSPG/KAG) | Reference architecture for logical-form guided reasoning |

---

## 10. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-06-18 | FileKB Contributors | Initial draft |
