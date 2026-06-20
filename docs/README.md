# FileKB — 设计文档

> **项目**: FileKB — 文件驱动的个人知识库系统  
> **日期**: 2026-06-20  
> **硬件基准**: M5 Max + 128GB unified memory + oMLX

---

## 文档清单

| # | 文档 | 说明 |
|---|------|------|
| 1 | [PRD.md](PRD.md) | 产品需求文档 — 产品愿景、用户故事、成功指标 |
| 2 | [DEVELOPMENT_V3.md](DEVELOPMENT_V3.md) | 系统设计文档 — 架构、ADR、数据模型、API、prompt 工程 |
| 3 | [../INSTALL.md](../INSTALL.md) | 安装与配置指南 — 基于真实运行环境 |
| 4 | [../CHANGELOG.md](../CHANGELOG.md) | 版本更新日志 |
| 5 | [../README.md](../README.md) | 项目首页（GitHub 展示） |

## ⚠️ 设计文档与实际系统的差异

DEVELOPMENT_V3.md 是 v0.1.0 初版设计。实际部署和迭代中，以下方面有调整（**代码是事实来源**）：

| 设计文档（旧） | 实际系统（新） | 原因 |
|-------------|-------------|------|
| Embedding 默认 `sentence-transformers` 本地运行 | 默认通过 oMLX API 运行 `bge-m3-mlx-fp16` | 14ms/vec，零额外 RAM |
| LLM model `Qwen3.6-35B-A3B-MTP` | `daily-agent-best-mtp` | 实际 oMLX 部署命名 |
| PDF 解析链 markitdown→Docling→pymupdf | PyMuPDF per-page → markitdown fallback → OCR | Docling 未实际部署 |
| `sqlite-fts4` 包依赖 | SQLite FTS5 已在 Python 标准库中 | 不需要额外包 |
| `sse-starlette` 用于 SSE 流式 | Streamlit 原生 `st.write_stream()` | 不需要额外包 |
| Frontend 独立目录 | `src/filekb/ui/` 在包内 | 简化部署 |

**DEVELOPMENT_V3.md 中仍然有效的部分**：架构决策记录 (ADR §3)、数据模型 (§5)、prompt 工程 (§8)、中文抽取方案 (§9)、可靠性设计 (§10)、个性化 (§11)、API 规范 (§12)、测试策略 (§14)。这些是稳定的设计参考。

## 文档维护

1. 代码和 `pyproject.toml` 是事实来源
2. DEVELOPMENT_V3.md 保留为设计参考，但以实际代码为准
3. PRD.md 随需求变更同步更新
