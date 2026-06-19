"""FileKB — 文件驱动个人知识库系统。

100% 本地运行，零云依赖。监控本地目录，
自动提取结构化知识，构建知识图谱，
支持自然语言问答，所有答案带源文件追溯。

入口文件：全局侧边栏（KB 选择器 + 统计）+ st.navigation。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

# ═══════════════════════════════════════════════════════════════════
# Page config
# ═══════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="FileKB",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════
# Session state initialization
# ═══════════════════════════════════════════════════════════════════

def _init_state():
    """Initialize all cross-page session_state keys once."""
    defaults: dict[str, object] = {
        "global_kb": "默认",
        "pending_kb": None,
        "_last_kb": "默认",
        "kb_names": ["默认"],
        "kb_meta": {},
        "kb_stats": {},
        "api_available": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ═══════════════════════════════════════════════════════════════════
# Global CSS — minimal: hide toolbar, set typography baseline
# ═══════════════════════════════════════════════════════════════════

st.html("""
<style>
    /* ── Hide Streamlit toolbar elements (top-right) ── */
    .stAppDeployButton          { display: none !important; }
    #MainMenu                   { visibility: hidden !important; }
    [data-testid="stStatusWidget"] { display: none !important; }

    /* ── Typography baseline ── */
    p, li, label, .stMarkdown, .stCaption { line-height: 1.6; }

    /* ── Heading rhythm ── */
    h1, h2, h3 { letter-spacing: -0.02em; text-wrap: balance; }

    /* ── Code blocks: ligatures on ── */
    code, .stCodeBlock code { font-feature-settings: "liga" 1, "calt" 1; }

    /* ── Metric numbers: tabular alignment ── */
    [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }

    /* ── Chat message spacing ── */
    [data-testid="stChatMessage"] { padding: 1rem 1.25rem; }

    /* ── Buttons: rounded ── */
    .stButton > button { border-radius: 8px; font-weight: 500; }
    .stButton > button[kind="primary"] { border-radius: 8px; }

    /* ── Section dividers ── */
    hr { margin: 0.75rem 0; }

    /* ── Sidebar nav items: match heading size ── */
    [data-testid="stSidebarNav"] a {
        font-size: 1.1rem;
        font-weight: 500;
    }
    [data-testid="stSidebarNav"] a:hover {
        background-color: rgba(128, 128, 128, 0.1);
    }

    /* ── Theme toggle styling ── */
    .theme-toggle-btn button {
        font-size: 1.25rem;
        padding: 0.25rem 0.5rem;
        line-height: 1;
    }
</style>
""")

# ═══════════════════════════════════════════════════════════════════
# Data loading helpers
# ═══════════════════════════════════════════════════════════════════

def _load_kb_list() -> bool:
    """Fetch KB names + metadata from API. Returns True on success."""
    try:
        resp = requests.get(f"{API_BASE}/settings", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            st.session_state.kb_names = data.get("kb_names", ["默认"])
            st.session_state.kb_meta = data.get("kb_meta", {})
            st.session_state.api_available = True
            return True
    except Exception:
        pass
    st.session_state.api_available = False
    return False

def _load_kb_stats(kb_name: str):
    """Fetch stats for a specific KB."""
    try:
        resp = requests.get(f"{API_BASE}/status?kb={kb_name}", timeout=5)
        if resp.status_code == 200:
            st.session_state.kb_stats = resp.json()
            return
    except Exception:
        pass
    st.session_state.kb_stats = {}

# Load KB list on first run / when stale
if st.session_state.kb_names == ["默认"] and not st.session_state.kb_meta:
    _load_kb_list()

# Ensure current KB exists in list
current_kb: str = st.session_state.global_kb
if current_kb not in st.session_state.kb_names:
    st.session_state.global_kb = st.session_state.kb_names[0]
    current_kb = st.session_state.kb_names[0]

# Load stats for current KB (once per session, or after switch)
if not st.session_state.kb_stats or st.session_state.kb_stats.get("kb") != current_kb:
    _load_kb_stats(current_kb)

# ═══════════════════════════════════════════════════════════════════
# Define pages
# ═══════════════════════════════════════════════════════════════════

chat_page = st.Page("pages/chat.py", title="对话问答", icon="💬", default=True)
graph_page = st.Page("pages/graph.py", title="知识图谱", icon="🔗")
files_page = st.Page("pages/files.py", title="文件管理", icon="📁")
entity_page = st.Page("pages/entity_review.py", title="实体审核", icon="🔍")
kb_mgmt_page = st.Page("pages/kb_management.py", title="知识库管理", icon="📚")
settings_page = st.Page("pages/settings.py", title="系统设置", icon="⚙️")
status_page = st.Page("pages/status.py", title="系统状态", icon="📊")

# ═══════════════════════════════════════════════════════════════════
# Global sidebar
# ═══════════════════════════════════════════════════════════════════

with st.sidebar:
    # ── KB Selector ──
    st.markdown("#### 📁 知识库")

    kb_names: list[str] = st.session_state.kb_names
    current_idx = kb_names.index(current_kb) if current_kb in kb_names else 0

    def _on_kb_select():
        selected = st.session_state.sidebar_kb_widget
        if selected != st.session_state.global_kb:
            st.session_state.pending_kb = selected

    st.selectbox(
        "当前知识库",
        options=kb_names,
        index=current_idx,
        key="sidebar_kb_widget",
        label_visibility="collapsed",
        on_change=_on_kb_select,
    )

    # ── KB switch confirmation ──
    pending: str | None = st.session_state.get("pending_kb")
    if pending and pending != st.session_state.global_kb:
        st.warning(f"⚠️ 切换到「{pending}」将清空当前对话。")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ 确认", key="kb_confirm_btn", use_container_width=True):
                st.session_state.global_kb = pending
                st.session_state.messages = []  # clear chat
                st.session_state.pending_kb = None
                _load_kb_stats(pending)
                st.rerun()
        with c2:
            if st.button("❌ 取消", key="kb_cancel_btn", use_container_width=True):
                st.session_state.pending_kb = None
                st.rerun()

    st.divider()

    # ── KB Stats ──
    st.markdown("#### 📊 当前知识库")

    stats: dict = st.session_state.kb_stats
    if stats:
        health = stats.get("health", {})
        m1, m2 = st.columns(2)
        with m1:
            st.metric("文件数", stats.get("files_total", 0))
        with m2:
            st.metric("事实数", stats.get("facts_total", 0))
        m3, m4 = st.columns(2)
        with m3:
            st.metric("实体数", health.get("graph", {}).get("nodes", 0))
        with m4:
            st.metric("向量数", health.get("faiss", {}).get("vectors", 0))
    else:
        st.caption("加载中...")

    st.divider()

    # ── API status ──
    if not st.session_state.api_available:
        st.error("⚠️ 后端 API 不可用")

# ═══════════════════════════════════════════════════════════════════
# Theme toggle — top-right of main content, uses native Streamlit theme
# ═══════════════════════════════════════════════════════════════════

# 使用 st._config.set_option("theme.base", ...) 调用 Streamlit 原生主题系统，
# 完全避免 CSS 注入导致的白条和不完整覆盖问题。

current_theme = st._config.get_option("theme.base")  # None=跟随系统, "light", "dark"

# Put toggle at the top-right of the main content area
_, _, theme_btn_col = st.columns([8, 1, 1])
with theme_btn_col:
    if current_theme == "dark":
        if st.button("☀️", key="theme_switch", help="切换到浅色模式"):
            st._config.set_option("theme.base", "light")
            st.rerun()
    else:
        # None (跟随系统) or "light" → show moon icon
        if st.button("🌙", key="theme_switch", help="切换到深色模式"):
            st._config.set_option("theme.base", "dark")
            st.rerun()

# ═══════════════════════════════════════════════════════════════════
# Navigation
# ═══════════════════════════════════════════════════════════════════

pg = st.navigation(
    [chat_page, graph_page, files_page, entity_page, kb_mgmt_page, settings_page, status_page]
)
pg.run()
