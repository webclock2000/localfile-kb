"""Configuration system using Pydantic v2 models.

Loads from ~/.filekb/config.yaml by default, with env var overrides
for key settings. All models use Pydantic v2 with strict validation.

Env var overrides:
    FILEKB_LLM_URL      → llm.base_url
    FILEKB_LLM_MODEL    → llm.model
    FILEKB_CONFIG       → custom config file path
    FILEKB_DB_PATH      → database.path
    FILEKB_LOG_LEVEL    → logging.level (DEBUG|INFO|WARNING|ERROR)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ============================================================================
# Sub-models
# ============================================================================


class LLMConfig(BaseModel):
    """LLM server connection settings."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default="http://127.0.0.1:8081/v1",
        description="oMLX OpenAI-compatible endpoint",
    )
    model: str = Field(
        default="daily-agent-best-mtp",
        description="Model name/alias to pass to the API (Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-MLX-oQ4-MTP)",
    )
    api_key: str = Field(
        default="not-needed",
        description="API key (not needed for local oMLX)",
    )
    timeout: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Request timeout in seconds",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum tenacity retry attempts",
    )
    context_window: int = Field(
        default=32768,
        description="Model context window size (tokens)",
    )


class EmbeddingConfig(BaseModel):
    """Embedding model settings."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["omlx", "local"] = Field(
        default="omlx",
        description="Embedding backend: 'omlx' (oMLX API, 14ms, no extra RAM) or 'local' (sentence-transformers, ~2GB)",
    )
    model: str = Field(
        default="bge-m3-mlx-fp16",
        description="Model name (omlx: oMLX model name; local: HF model ID)",
    )
    device: Literal["cpu", "mps"] = Field(
        default="cpu",
        description="Device for local backend only",
    )
    normalize: bool = Field(
        default=True,
        description="L2-normalize embedding vectors",
    )
    omxl_url: str = Field(
        default="http://127.0.0.1:8081/v1",
        description="oMLX API URL (omlx backend only)",
    )


class KBInfo(BaseModel):
    """Knowledge base metadata — light metadata keyed by KB name."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="KB description for display")


class DirectoryConfig(BaseModel):
    """Per-directory monitoring configuration."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Directory path to monitor (supports ~)")
    group: str = Field(
        default="默认",
        description="知识库分组: '工作', '生活', '默认', 或自定义名称",
    )
    recursive: bool = Field(default=True, description="Scan subdirectories")
    exclude_patterns: list[str] = Field(
        default_factory=lambda: [".git", "__pycache__", ".DS_Store"],
        description="Glob patterns to exclude",
    )

    @field_validator("path")
    @classmethod
    def expand_path(cls, v: str) -> str:
        return str(Path(v).expanduser())


class ExtractionConfig(BaseModel):
    """Knowledge extraction parameters."""

    model_config = ConfigDict(extra="forbid")

    rounds: int = Field(default=2, ge=1, le=5, description="LLM extraction rounds per chunk")
    max_chars_per_chunk: int = Field(default=24000, ge=1000, le=100000)
    overlap_chars: int = Field(default=500, ge=0, le=5000)
    max_workers: int = Field(default=2, ge=1, le=8, description="Parallel extraction workers")
    dedup_threshold: float = Field(
        default=0.90, ge=0.0, le=1.0, description="Cosine similarity dedup threshold"
    )
    confidence_threshold: int = Field(
        default=30, ge=0, le=100, description="Drop facts below this confidence"
    )


class QueryConfig(BaseModel):
    """Query and retrieval parameters."""

    model_config = ConfigDict(extra="forbid")

    vector_top_k: int = Field(default=20, ge=1, le=500)
    fts_top_k: int = Field(default=10, ge=1, le=100)
    vector_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    graph_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    fts_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    user_score_weight: float = Field(default=0.1, ge=0.0, le=1.0)
    max_context_chunks: int = Field(default=8, ge=1, le=50)
    max_context_facts: int = Field(default=30, ge=1, le=200)
    answer_max_tokens: int = Field(default=1024, ge=64, le=8192)


class RetryConfig(BaseModel):
    """Tenacity retry settings."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_delay: float = Field(default=1.0, ge=0.1, le=60.0, description="Seconds")


class DLQConfig(BaseModel):
    """Dead Letter Queue settings."""

    model_config = ConfigDict(extra="forbid")

    auto_retry: bool = Field(default=True, description="Auto-process DLQ after main batch")
    prune_days: int = Field(default=30, ge=1, le=365, description="Auto-delete entries older than")


class ResilienceConfig(BaseModel):
    """Resilience and fault-tolerance settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_chunk_depth: int = Field(default=3, ge=1, le=5, description="Max adaptive_resplit depth")
    retry: RetryConfig = RetryConfig()
    dlq: DLQConfig = DLQConfig()


class FeedbackConfig(BaseModel):
    """User feedback scoring parameters."""

    model_config = ConfigDict(extra="forbid")

    delta: float = Field(default=0.1, ge=0.01, le=1.0, description="Score change per feedback")
    decay_rate: float = Field(
        default=0.01, ge=0.0, le=0.1, description="Weekly decay rate for user_score"
    )


class EntityResolutionConfig(BaseModel):
    """Entity merge/resolution parameters."""

    model_config = ConfigDict(extra="forbid")

    auto_approve_threshold: float = Field(
        default=0.85, ge=0.0, le=1.0, description="Auto-merge confidence threshold"
    )
    similarity_threshold: float = Field(
        default=0.80, ge=0.0, le=1.0, description="Candidate generation threshold"
    )


class AnalyzerConfig(BaseModel):
    """Preference analyzer settings."""

    model_config = ConfigDict(extra="forbid")

    run_after_index: bool = Field(default=True, description="Run preference analysis post-index")


class PersonalizationConfig(BaseModel):
    """Personalization and feedback loop settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    feedback: FeedbackConfig = FeedbackConfig()
    entity: EntityResolutionConfig = EntityResolutionConfig()
    analyzer: AnalyzerConfig = AnalyzerConfig()


class HotPathConfig(BaseModel):
    """Single-file fast indexing settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    rebuild_graph: bool = Field(
        default=False, description="False=append to NetworkX; True=full rebuild"
    )


class NERFilterConfig(BaseModel):
    """Chinese NER filter settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model_path: str = Field(
        default="~/.filekb/models/deepke-cn",
        description="Path to DeepKE RoBERTa-wwm-ext model",
    )

    @field_validator("model_path")
    @classmethod
    def expand_path(cls, v: str) -> str:
        return str(Path(v).expanduser())


class ChineseDetectionConfig(BaseModel):
    """Chinese content detection settings."""

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(
        default=0.50, ge=0.0, le=1.0, description="CJK character ratio to trigger Chinese prompt"
    )
    ner_filter: NERFilterConfig = NERFilterConfig()


class NotificationConfig(BaseModel):
    """macOS notification settings."""

    model_config = ConfigDict(extra="forbid")

    show_failures: bool = Field(
        default=True, description="Include failure details in notification"
    )


class OCRConfig(BaseModel):
    """Apple Vision OCR settings (macOS only)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable OCR for images and image-based PDFs (macOS only)",
    )
    languages: list[str] = Field(
        default_factory=lambda: ["zh-Hans", "zh-Hant", "en"],
        description="Recognition languages in priority order",
    )
    min_confidence: float = Field(
        default=0.3, ge=0.0, le=1.0,
        description="Minimum text recognition confidence",
    )
    pdf_dpi: int = Field(
        default=200, ge=72, le=600,
        description="DPI for rendering PDF pages to images",
    )


class DatabaseConfig(BaseModel):
    """SQLite database settings."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="~/.filekb/filekb.db", description="SQLite database file path")
    wal_mode: bool = Field(default=True, description="Enable WAL journal mode")

    @field_validator("path")
    @classmethod
    def expand_path(cls, v: str) -> str:
        return str(Path(v).expanduser())


class LoggingConfig(BaseModel):
    """Application logging settings."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    file: str = Field(default="~/.filekb/filekb.log")

    @field_validator("file")
    @classmethod
    def expand_path(cls, v: str) -> str:
        return str(Path(v).expanduser())


# ============================================================================
# Root config
# ============================================================================


class Config(BaseModel):
    """Root configuration for FileKB.

    Load from ~/.filekb/config.yaml by default.
    Override with --config flag or FILEKB_CONFIG env var.
    """

    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    kb_meta: dict[str, KBInfo] = Field(default_factory=dict, description="KB metadata by name")
    directories: list[DirectoryConfig] = Field(default_factory=list)
    extraction: ExtractionConfig = ExtractionConfig()
    query: QueryConfig = QueryConfig()
    resilience: ResilienceConfig = ResilienceConfig()
    personalization: PersonalizationConfig = PersonalizationConfig()
    hot_path: HotPathConfig = HotPathConfig()
    chinese_detection: ChineseDetectionConfig = ChineseDetectionConfig()
    ocr: OCRConfig = OCRConfig()
    notification: NotificationConfig = NotificationConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()


# ============================================================================
# Logging setup
# ============================================================================


def setup_logging(cfg: Config | None = None, force_level: str | None = None) -> None:
    """Configure Python logging based on config.

    Call this early in CLI entry points and FastAPI lifespan.
    Supports FILEKB_LOG_LEVEL env var for ad-hoc debugging.

    Args:
        cfg: Config instance. If None, loads default config.
        force_level: Override log level (e.g., 'DEBUG').
                     Env var FILEKB_LOG_LEVEL also overrides.
    """
    if cfg is None:
        cfg = Config()

    level_str = force_level or os.getenv("FILEKB_LOG_LEVEL", cfg.logging.level)
    level = getattr(logging, level_str.upper(), logging.INFO)

    log_file = str(Path(cfg.logging.file).expanduser())
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers to avoid duplicates
    root.handlers.clear()

    # Formatter
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File handler
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        logging.getLogger(__name__).warning("Cannot write log file %s: %s", log_file, e)

    logging.getLogger(__name__).info("Logging configured: level=%s file=%s", level_str, log_file)


# ============================================================================
# Loader
# ============================================================================

DEFAULT_CONFIG_PATH = "~/.filekb/config.yaml"


def _env_overrides(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides to config dict."""
    if url := os.getenv("FILEKB_LLM_URL"):
        config_dict.setdefault("llm", {})["base_url"] = url
    if model := os.getenv("FILEKB_LLM_MODEL"):
        config_dict.setdefault("llm", {})["model"] = model
    if db_path := os.getenv("FILEKB_DB_PATH"):
        config_dict.setdefault("database", {})["path"] = db_path
    return config_dict


def load_config(config_path: str | None = None) -> Config:
    """Load and validate configuration.

    Resolution order (later overrides earlier):
    1. Default values in Pydantic models
    2. ~/.filekb/config.yaml (or path from FILEKB_CONFIG env var)
    3. Environment variable overrides (FILEKB_LLM_URL, etc.)

    Args:
        config_path: Override config file path. If None, uses FILEKB_CONFIG
                     env var or the default ~/.filekb/config.yaml.

    Returns:
        Validated Config instance.
    """
    if config_path is None:
        config_path = os.getenv("FILEKB_CONFIG", DEFAULT_CONFIG_PATH)

    config_path = str(Path(config_path).expanduser())
    config_dict: dict[str, Any] = {}

    if os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
            config_dict.update(loaded)

    config_dict = _env_overrides(config_dict)

    cfg = Config.model_validate(config_dict)
    setup_logging(cfg)
    return cfg
