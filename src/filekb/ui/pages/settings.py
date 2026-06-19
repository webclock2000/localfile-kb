"""系统设置页面 — LLM、Embedding、OCR 配置。

知识库管理已拆分到独立页面（pages/kb_management.py）。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── Header ──
st.title("⚙️ 系统设置")

# ── Load settings ──
try:
    resp = requests.get(f"{API_BASE}/settings", timeout=5)
    settings = resp.json() if resp.status_code == 200 else {}
except Exception:
    settings = {}

# ── Tabs ──
tab_llm, tab_emb, tab_ocr = st.tabs([
    "🤖 大语言模型", "🧮 嵌入模型", "🔍 OCR 识别",
])

# ═══════════════════════════════════════════════
# Tab 1: LLM
# ═══════════════════════════════════════════════
with tab_llm:
    st.markdown("#### 🤖 大语言模型设置")
    st.caption("配置本地 LLM 服务地址和模型参数。默认使用 oMLX。")

    llm = settings.get("llm", {})
    c1, c2 = st.columns(2)
    with c1:
        llm_url = st.text_input(
            "API 地址",
            value=llm.get("base_url", "http://127.0.0.1:8081/v1"),
            key="llm_url",
        )
        llm_timeout = st.number_input(
            "超时(秒)",
            value=llm.get("timeout", 120),
            min_value=10, max_value=600,
            key="llm_timeout",
        )
    with c2:
        llm_model = st.text_input(
            "模型名称",
            value=llm.get("model", "daily-agent-best-mtp"),
            key="llm_model",
        )
        llm_ctx = st.number_input(
            "上下文窗口 (tokens)",
            value=llm.get("context_window", 32768),
            min_value=4096, max_value=262144,
            key="llm_ctx",
        )

    if st.button("💾 保存 LLM 设置", key="save_llm"):
        try:
            requests.post(f"{API_BASE}/settings", json={
                "section": "llm",
                "data": {
                    "base_url": llm_url, "model": llm_model,
                    "timeout": llm_timeout, "context_window": llm_ctx,
                },
            }, timeout=10)
            st.success("已保存，重启服务后生效。")
        except Exception as e:
            st.error(f"保存失败: {e}")

# ═══════════════════════════════════════════════
# Tab 2: Embedding
# ═══════════════════════════════════════════════
with tab_emb:
    st.markdown("#### 🧮 嵌入模型设置")
    st.caption("嵌入模型将文本转为向量，用于语义搜索。")

    emb = settings.get("embedding", {})
    emb_backend = st.radio(
        "后端选择",
        options=["omlx", "local"],
        index=0 if emb.get("backend") == "omlx" else 1,
        format_func=lambda x: "🚀 oMLX（推荐）" if x == "omlx" else "💻 本地 sentence-transformers",
        key="emb_backend",
    )
    c1, c2 = st.columns(2)
    with c1:
        emb_model = st.text_input(
            "模型名称",
            value=emb.get("model", "bge-m3-mlx-fp16"),
            key="emb_model",
        )
    with c2:
        emb_omxl_url = st.text_input(
            "oMLX 地址",
            value=emb.get("omxl_url", "http://127.0.0.1:8081/v1"),
            key="emb_omxl",
        )

    if st.button("💾 保存 Embedding 设置", key="save_emb"):
        try:
            requests.post(f"{API_BASE}/settings", json={
                "section": "embedding",
                "data": {"backend": emb_backend, "model": emb_model, "omxl_url": emb_omxl_url},
            }, timeout=10)
            st.success("已保存，重启服务后生效。")
        except Exception as e:
            st.error(f"保存失败: {e}")

# ═══════════════════════════════════════════════
# Tab 3: OCR
# ═══════════════════════════════════════════════
with tab_ocr:
    st.markdown("#### 🔍 OCR 文字识别")
    st.caption("Apple Vision 高精度中文 OCR。仅限 macOS。")

    ocr_cfg = settings.get("ocr", {})
    ocr_enabled = st.toggle(
        "启用 OCR",
        value=ocr_cfg.get("enabled", True),
        key="ocr_toggle",
    )
    ocr_confidence = st.slider(
        "最低识别置信度",
        0.1, 1.0,
        value=ocr_cfg.get("min_confidence", 0.3),
        step=0.1,
        key="ocr_conf",
    )

    if st.button("💾 保存 OCR 设置", key="save_ocr"):
        try:
            requests.post(f"{API_BASE}/settings", json={
                "section": "ocr",
                "data": {
                    "enabled": ocr_enabled,
                    "min_confidence": ocr_confidence,
                    "languages": ["zh-Hans", "zh-Hant", "en"],
                },
            }, timeout=10)
            st.success("已保存。")
        except Exception as e:
            st.error(f"保存失败: {e}")

# ═══════════════════════════════════════════════
# Footer: link to KB management
# ═══════════════════════════════════════════════
st.divider()
st.page_link(
    "pages/kb_management.py",
    label="📚 前往知识库管理 →",
    help="创建、重命名、删除知识库，管理监控目录",
)
