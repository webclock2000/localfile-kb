"""FileKB — 文件驱动个人知识库系统。

100% 本地运行，零云依赖。监控本地目录，
自动提取结构化知识，构建知识图谱，
支持自然语言问答，所有答案带源文件追溯。

入口文件：全局侧边栏（KB 选择器 + 统计）+ st.navigation。
"""

from __future__ import annotations

from pathlib import Path

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

def _resolve_initial_kb() -> str:
    """Resolve the initial KB on session start.

    Priority:
    1. URL query param ``?kb=xxx`` (survives browser refresh)
    2. ``~/.filekb/last_active_kb``  file (survives restart / new tab)
    3. ``"默认"`` (first-time user)
    """
    # 1) URL query param
    qp_kb = st.query_params.get("kb")
    if qp_kb:
        return qp_kb

    # 2) Persisted last-active file
    last_kb_path = Path("~/.filekb/last_active_kb").expanduser()
    try:
        if last_kb_path.is_file():
            persisted = last_kb_path.read_text(encoding="utf-8").strip()
            if persisted:
                return persisted
    except Exception:
        pass

    return "默认"


def _persist_active_kb(kb_name: str) -> None:
    """Write the current KB name so it survives restart / new tab."""
    last_kb_path = Path("~/.filekb/last_active_kb").expanduser()
    try:
        last_kb_path.parent.mkdir(parents=True, exist_ok=True)
        last_kb_path.write_text(kb_name, encoding="utf-8")
    except Exception:
        pass


def _init_state():
    """Initialize all cross-page session_state keys once."""
    initial_kb = _resolve_initial_kb()
    defaults: dict[str, object] = {
        "global_kb": initial_kb,
        "pending_kb": None,
        "_last_kb": initial_kb,
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

    /* ── Typography: use system native fonts (no Google Fonts fetches) ── */
    html, body, p, li, label, .stMarkdown, .stCaption, input, textarea, select, button {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC",
                     "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC",
                     sans-serif;
        line-height: 1.6;
    }

    /* ── Heading rhythm ── */
    h1, h2, h3 { letter-spacing: -0.02em; text-wrap: balance; }

    /* ── Code blocks: ligatures on ── */
    code, .stCodeBlock code {
        font-family: "SF Mono", "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
        font-feature-settings: "liga" 1, "calt" 1;
    }

    /* ── Metric numbers: tabular alignment ── */
    [data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }

    /* ── Chat message spacing ── */
    [data-testid="stChatMessage"] { padding: 1rem 1.25rem; }

    /* ── Buttons: rounded ── */
    .stButton > button { border-radius: 8px; font-weight: 500; }
    .stButton > button[kind="primary"] { border-radius: 8px; }

    /* ── Section dividers ── */
    hr { margin: 0.75rem 0; }

    /* ── Tab headers: larger ── */
    button[data-baseweb="tab"] p {
        font-size: 1.5rem !important;
        font-weight: 600 !important;
    }

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

# ── Sidebar data refresh ──
# Pages signal changes via ``_sidebar_stale = True`` so the sidebar
# re-fetches KB list + stats instead of relying on cached session state.
# When stale, also reset the selectbox widget so it picks up global_kb
# via the ``index`` parameter (Streamlit selectbox ignores index if its
# session_state key already exists).
_sb_stale: bool = st.session_state.pop("_sidebar_stale", False)

if _sb_stale:
    st.session_state.pop("sidebar_kb_widget", None)
    st.session_state.pop("pending_kb", None)
    _load_kb_list()
elif st.session_state.kb_names == ["默认"] and not st.session_state.kb_meta:
    _load_kb_list()

# Ensure current KB exists in list (may have been deleted by another page)
current_kb: str = st.session_state.global_kb
if current_kb not in st.session_state.kb_names:
    st.session_state.global_kb = st.session_state.kb_names[0]
    current_kb = st.session_state.kb_names[0]

# Reload stats on first load, KB switch, or when data is stale
if _sb_stale or not st.session_state.kb_stats or st.session_state.kb_stats.get("kb") != current_kb:
    _load_kb_stats(current_kb)

# ═══════════════════════════════════════════════════════════════════
# Define pages
# ═══════════════════════════════════════════════════════════════════

chat_page = st.Page("views/chat.py", title="对话问答", icon="💬", default=True)
graph_page = st.Page("views/graph.py", title="知识图谱", icon="🔗")
files_page = st.Page("views/files.py", title="文件管理", icon="📁")
entity_page = st.Page("views/entity_review.py", title="实体审核", icon="🔍")
kb_mgmt_page = st.Page("views/kb_management.py", title="知识库管理", icon="📚")
settings_page = st.Page("views/settings.py", title="系统设置", icon="⚙️")
status_page = st.Page("views/status.py", title="系统状态", icon="📊")

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
                _persist_active_kb(pending)
                st.query_params["kb"] = pending
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
# Theme toggle — top-right of main content, 3-way cycle
# ═══════════════════════════════════════════════════════════════════
# None (跟随系统) → "light" → "dark" → None ...

current_theme = st._config.get_option("theme.base")  # None=跟随系统, "light", "dark"

# Map: current → (icon, next_value, help_text)
THEME_CYCLE: dict[str | None, tuple[str, str | None, str]] = {
    None:    ("🖥️", "light", "当前：跟随系统。点击切换为浅色模式。"),
    "light": ("☀️", "dark",  "当前：浅色模式。点击切换为深色模式。"),
    "dark":  ("🌙", None,    "当前：深色模式。点击切换为跟随系统。"),
}

icon, next_theme, help_text = THEME_CYCLE.get(current_theme, THEME_CYCLE[None])

_, _, theme_btn_col = st.columns([8, 1, 1])
with theme_btn_col:
    if st.button(icon, key="theme_switch", help=help_text):
        if next_theme is None:
            # 回到跟随系统 — 删除显式设置，让 config.toml 的 base=null 生效
            st._config.set_option("theme.base", None)
        else:
            st._config.set_option("theme.base", next_theme)
        st.rerun()

# ═══════════════════════════════════════════════════════════════════
# Navigation
# ═══════════════════════════════════════════════════════════════════

pg = st.navigation(
    [chat_page, graph_page, files_page, entity_page, kb_mgmt_page, settings_page, status_page]
)
pg.run()
