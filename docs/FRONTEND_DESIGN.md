# FileKB 前端开发设计文档

> **状态**: 待实施  
> **版本**: 1.0  
> **最后更新**: 2025-06-19  
> **依赖**: DEVELOPMENT_V3.md §6（前端交互设计，原规范）

---

## 目录

1. [设计目标与原则](#1-设计目标与原则)
2. [视觉设计系统](#2-视觉设计系统)
3. [参考系统调研](#3-参考系统调研)
4. [导航架构](#4-导航架构)
5. [全局侧边栏设计](#5-全局侧边栏设计)
6. [页面详细设计](#6-页面详细设计)
   - [5.1 对话问答](#51-对话问答-chat)
   - [5.2 知识图谱](#52-知识图谱-graph)
   - [5.3 文件管理](#53-文件管理-files)
   - [5.4 实体审核](#54-实体审核-entity-review)
   - [5.5 知识库管理](#55-知识库管理-kb-management)
   - [5.6 系统设置](#56-系统设置-settings)
   - [5.7 系统状态](#57-系统状态-status)
7. [状态管理与数据流](#7-状态管理与数据流)
8. [KB 切换交互协议](#8-kb-切换交互协议)
9. [API 契约](#9-api-契约)
10. [实施计划](#10-实施计划)

---

## 1. 设计目标与原则

### 1.1 核心目标

FileKB 前端是为**单人用户**设计的本地知识库管理界面。它需要在 Streamlit 纯 Python 技术栈的约束下，提供完整的多知识库操作体验。

### 1.2 设计原则

| # | 原则 | 说明 |
|---|------|------|
| **P1** | 知识库上下文始终可见 | 用户在任何页面都能看到当前操作的是哪个 KB，避免"我在操作哪个库？"的困惑 |
| **P2** | 操作前确认破坏性行为 | KB 切换（清空对话）、KB 删除等操作需要二次确认 |
| **P3** | 单一入口管理 KB | 知识库的 CRUD 集中在独立页面，不在多个页面分散管理 |
| **P4** | 后端 API 是唯一数据源 | 所有页面不直接导入 `store.py`/`llm.py`，通过 FastAPI 调用后端 |
| **P5** | 渐进式披露 | 常用功能一眼可见，高级操作用折叠/展开控制信息密度 |
| **P6** | 空状态友好引导 | 无数据时显示操作引导，而非空白页面 |

### 1.3 参考系统定位

FileKB 前端介于以下系统之间：

| 维度 | AnythingLLM | RAGFlow | FileKB 定位 |
|------|------------|---------|-------------|
| 复杂度 | 极简，Chat 中心 | 企业级多功能 | **中等** — 有图/文件/实体审核，但无多租户/权限 |
| KB 模型 | Workspace（线程+文档） | Dataset（文档集合） | **KB = 目录组**，每个 KB 有独立 SQLite/FAISS/Graph |
| 用户数 | 单用户 | 多用户+RBAC | **单用户**，无登录/权限 |
| 前端栈 | React + Vite | React + UmiJS | **Streamlit 纯 Python** |

---

## 2. 视觉设计系统

### 2.1 设计哲学

FileKB 前端视觉设计遵循 **Linear.app 的"不可见的设计"** 哲学：字体和排版本身就是界面，色彩极度克制，装饰元素趋近于零。目标是让用户专注于知识内容，而非 UI chrome。

**参考对比**:

| 维度 | 当前 FileKB UI | Linear.app 做法 | FileKB 目标 |
|------|---------------|----------------|------------|
| 字体 | 系统默认（SF Pro / 微软雅黑混排） | 单一 Inter，全应用一致 | **Inter**，单一字体，Google Fonts 加载 |
| 字重 | 无层次差异 | 仅 400/500/600/700 四种 | 正文 400，标题 600-700 |
| 正文字号 | 默认 16px（各组件大小不一） | 17px 正文 + 严格比例 | 16px body，标题用 rem 比例尺 |
| 间距 | 组件默认间距，无网格系统 | 4px 基准网格 | 依赖 Streamlit 默认 + CSS 微调 |
| 色彩 | 蓝色主色 + Streamlit 默认灰 | ≤10 色，极度克制 | 主色 #2563EB + 中性灰阶 |
| 行高 | 默认 | 1.5-1.6 | CSS 设置 `line-height: 1.6` |
| 字间距 | 默认 | 绝不手动调整 Inter 原生度量 | **不动** letter-spacing |
| 代码字体 | 等宽默认 | 无（Linear 无代码区域） | JetBrains Mono |

### 2.2 字体配置

#### 2.2.1 config.toml 主题

```toml
# .streamlit/config.toml
[theme]
baseFontSize = 16
baseFontWeight = "400"

font = "Inter:https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,100..900&display=swap, sans-serif"
headingFont = "Inter:https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,100..900&display=swap, sans-serif"
codeFont = "'JetBrains Mono':https://fonts.googleapis.com/css2?family=JetBrains+Mono:ital,wght@0,400;0,600;1,400&display=swap, monospace"
codeFontSize = "0.875rem"

headingFontSizes = { h1 = "2rem", h2 = "1.5rem", h3 = "1.25rem" }
headingFontWeights = { h1 = 700, h2 = 600, h3 = 600 }

primaryColor = "#2563EB"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F8FAFC"
textColor = "#1E293B"

[server]
enableStaticServing = true
```

#### 2.2.2 全局 CSS 注入

在 `app.py` 中注入精炼的 CSS 微调，遵循 Linear 的克制原则：

```python
st.markdown("""
<style>
    /* ── Typography baseline ── */
    p, li, label, .stMarkdown, .stCaption {
        line-height: 1.6;
    }

    /* ── Heading rhythm ── */
    h1, h2, h3 { letter-spacing: -0.02em; text-wrap: balance; }

    /* ── Code blocks ── */
    code, .stCodeBlock code {
        font-feature-settings: "liga" 1, "calt" 1;
    }

    /* ── Metric numbers: tabular alignment ── */
    [data-testid="stMetricValue"] {
        font-variant-numeric: tabular-nums;
    }

    /* ── Chat message spacing ── */
    [data-testid="stChatMessage"] {
        padding: 1rem 1.25rem;
    }

    /* ── Card containers: rounded, subtle border ── */
    [data-testid="stExpander"] details, div[data-testid="stVerticalBlock"] {
        border-radius: 8px;
    }

    /* ── Buttons: pill shape ── */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
    }

    /* ── Primary button ── */
    .stButton > button[kind="primary"] {
        background-color: #2563EB;
    }
</style>
""", unsafe_allow_html=True)
```

#### 2.2.3 字号比例尺

基于 16px 根字号，使用 1.25 模数比例：

| Token | 字号 | 用途 |
|-------|------|------|
| h1 | 2rem (32px) | 页面标题 |
| h2 | 1.5rem (24px) | 区域标题 |
| h3 | 1.25rem (20px) | 卡片/面板标题 |
| body | 1rem (16px) | 正文 / widget label |
| caption | 0.875rem (14px) | 辅助说明 |
| code | 0.875rem (14px) | 代码块 |
| metric | 继承 | st.metric 数值由 Streamlit 控制 |

### 2.3 知识图谱可视化

#### 2.3.1 技术选型

**决策**: 用 **yFiles Graphs for Streamlit** 替代当前的 graphviz DOT 渲染。

**对比**:

| 维度 | graphviz DOT（当前） | yFiles Graphs for Streamlit |
|------|---------------------|---------------------------|
| 渲染 | 静态图片（PNG/SVG） | **WebGL** 实时渲染 |
| 交互 | 无（只能看） | ✅ 拖拽节点、滚轮缩放、画布平移 |
| 点击 | 无 | ✅ 点击节点 → 展示属性 + 邻域 |
| 布局 | 固定单向 | ✅ 有机/层次/圆形/树形/力导向 5 种 |
| 搜索 | 无 | ✅ 内置搜索框 |
| 大图性能 | 差（>50 节点难以阅读） | WebGL 加速，轻松处理 500+ 节点 |
| 安装 | 内置 | `pip install yfiles_graphs_for_streamlit` |
| 许可证 | 内置 | **免费**（yWorks Apache-2.0 风格） |
| NetworkX 集成 | 手动转 DOT | `StreamlitGraphWidget.from_networkx(G)` |

#### 2.3.2 集成代码

```python
# pages/graph.py
from yfiles_graphs_for_streamlit import StreamlitGraphWidget, Layout
import networkx as nx
import requests

API_BASE = "http://localhost:9494"
kb = st.session_state.get("global_kb", "默认")

# 搜索实体
entity_query = st.text_input("搜索实体", placeholder="例如：遥感、邓磊")
hop = st.selectbox("扩展跳数", [1, 2, 3], index=0)

if entity_query:
    resp = requests.get(
        f"{API_BASE}/graph",
        params={"entity": entity_query, "hop": hop, "kb": kb},
        timeout=30,
    )
    if resp.status_code == 200:
        data = resp.json()
        G = nx.DiGraph()
        for n in data.get("nodes", []):
            G.add_node(n["name"], degree=n.get("degree", 0))
        for e in data.get("edges", []):
            G.add_edge(e["source"], e["target"], predicate=e.get("predicate", ""))

        if G.number_of_nodes() > 0:
            # yFiles 可视化
            widget = StreamlitGraphWidget.from_networkx(G)
            widget.show(
                directed=True,
                graph_layout=Layout.ORGANIC,          # 有机布局 — 比力导向更美观
                sidebar={"enabled": True, "start_with": "Neighborhood"},
                overview=True,
            )

            # 实体列表（侧边辅助）
            with st.expander(f"📋 {G.number_of_nodes()} 个实体, {G.number_of_edges()} 条关系"):
                for n in sorted(data["nodes"], key=lambda x: x.get("degree", 0), reverse=True):
                    st.markdown(f"**{n['name']}** (连接数: {n.get('degree', 0)})")
```

#### 2.3.3 节点样式映射

按实体类型染色 + 按连接度调大小：

```python
# 定义实体类型 → 颜色映射
TYPE_COLORS = {
    "人物": "#4F46E5",   # Indigo
    "组织": "#0891B2",   # Cyan
    "地点": "#059669",   # Emerald
    "时间": "#D97706",   # Amber
    "事件": "#DC2626",   # Red
    "方法": "#7C3AED",   # Violet
    "概念": "#2563EB",   # Blue（默认）
}

def node_color_mapping(node_data):
    entity_type = node_data.get("type", "概念")
    return TYPE_COLORS.get(entity_type, "#2563EB")

def node_size_mapping(node_data):
    degree = node_data.get("degree", 1)
    return max(20, min(80, degree * 12))
```

---

## 3. 参考系统调研

调研了 6 个成熟系统的前端设计，提取可借鉴的模式：

### 3.1 AnythingLLM — 工作区隔离 + 极简 Chat

**技术栈**: Vite + React + Tailwind + CSS Modules

**核心借鉴**:

| 模式 | 描述 | FileKB 适配 |
|------|------|------------|
| **容器/呈现者分离** | Container 管状态+数据，Presenter 纯渲染 | Streamlit 中通过 session_state 集中管理状态，页面只做渲染 |
| **工作区侧边栏列表** | Sidebar 显示所有 Workspace，可搜索、拖拽排序 | KB 选择器 + 统计信息放在 sidebar 顶部 |
| **自动恢复上次工作区** | 启动时自动打开上次使用的 Workspace | 启动时从 `session_state` 恢复上次 KB 选择 |
| **空状态落地页** | 无工作区时展示引导 CTA | 无 KB/无数据时显示操作引导（"尚未创建知识库 → 前往知识库管理"） |
| **Tooltip 批处理** | 避免数百个监听器 | Streamlit 自带 widget 管理，不成问题 |

### 3.2 Dify — 流式 Chat + 反馈系统

**技术栈**: Next.js + React + Jotai + Tailwind

**核心借鉴**:

| 模式 | 描述 | FileKB 适配 |
|------|------|------------|
| **流式 token 渲染** | 逐字显示 LLM 回答 | 消费 `/ask/stream` SSE 端点，逐 token 渲染 |
| **内联反馈按钮** | 每条回答下方 `[👍]` `[👎]` | 已回答实现，保持 |
| **工作区选择器（顶栏）** | 固定在页面左上角 | 改为固定在 sidebar 顶部（更符合 Streamlit 布局） |
| **引用来源折叠面板** | 回答末尾展开显示源文件 | 已回答实现，保持 |

### 3.3 Open WebUI — 模型选择器 + 侧边栏设计

**技术栈**: SvelteKit + Tailwind + Svelte Stores

**核心借鉴**:

| 模式 | 描述 | FileKB 适配 |
|------|------|------------|
| **侧边栏分区布局** | 用户区 / 聊天列表 / 工作区 / 设置 分区 | sidebar 分为：KB 区 / 导航区 / 统计区 |
| **折叠式聊天列表** | 按时间分组（今天/昨天/更早） | FileKB 目前无多会话，暂不需要 |
| **响应式移动端** | <768px 时侧边栏覆盖 + 滑动关闭 | FileKB 定位桌面端（macOS），移动端为 P2 |

### 2.4 RAGFlow — 知识库卡片 + 文档管理

**技术栈**: React 18 + UmiJS + Ant Design + React Flow

**核心借鉴**:

| 模式 | 描述 | FileKB 适配 |
|------|------|------------|
| **KB 卡片网格** | 每个 KB 一张卡片（名称、文档数、更新时间） | 知识库管理页使用卡片列表 |
| **搜索 + 筛选** | 顶部搜索框实时过滤 KB 列表 | 知识库管理页顶部搜索 |
| **创建弹窗** | 点击"创建"弹出 Modal，而非页面跳转 | Streamlit 无 Modal，使用容器内表单 + `st.container(border=True)` |
| **批量操作** | 删除确认 + 进度反馈 | 单个操作即可（个人 KB 数量少） |
| **Admin 仪表盘** | 服务状态、任务监控 | 系统状态页已有 |

### 2.5 LightRAG — 图谱可视化

**技术栈**: React 19 + TypeScript + Zustand + Sigma.js

**核心借鉴**:

| 模式 | 描述 | FileKB 适配 |
|------|------|------------|
| **多布局切换** | 力导向 / 圆形 / 层级 | 使用 `st.graphviz_chart` 或 NetworkX + pyvis 输出 |
| **节点点击 → 属性面板** | 点击节点显示详情 + 关联 fact | Streamlit 中改用侧边栏显示选中节点信息 |
| **社区检测染色** | Louvain 算法自动分组着色 | P2 需求，当前用实体类型着色即可 |
| **渐进式边显示** | 默认隐藏边，可切换显示 | P2 需求 |

### 2.6 Streamlit 多页面最佳实践

**来源**: Streamlit 官方文档 + 社区讨论

| 模式 | 描述 | FileKB 适配 |
|------|------|------------|
| **`st.navigation` + entrypoint** | app.py 定义所有页面，sidebar 在入口统一管理 | ✅ **采用** — 本次重构的核心架构 |
| **`session_state` 全局状态** | KB 选择、对话历史等跨页面状态 | ✅ 采用 |
| **`on_click` 回调模式** | 修改 session_state 的逻辑放在回调中，避免 widget key 冲突 | ✅ 已在设置页验证，推广到所有页面 |
| **`@st.cache_resource` 数据缓存** | API 响应缓存（如 KB 列表） | ✅ 用于缓存 settings 等低频数据 |

---

## 3. 导航架构

### 3.1 技术方案：`st.navigation` API

**决策**: 从传统 `pages/` 目录迁移到 `st.navigation` API。

**对比**:

| 方面 | 传统 `pages/` 目录 | `st.navigation` API |
|------|-------------------|---------------------|
| 共享 sidebar 组件 | ❌ 每页独立 sidebar，需手动同步 | ✅ entrypoint 定义一次 |
| 全局 KB 选择器 | ❌ 每页需各自实现 | ✅ sidebar 定义一次，所有页面可见 |
| 页面顺序控制 | 文件名编号（`01_💬_`） | `st.Page` 对象顺序 |
| 自定义导航样式 | ❌ 固定样式 | ✅ `st.page_link` 灵活 |
| URL 路由 | 文件名 | Page 对象 `url_path` |
| 代码重复 | 每页都需初始化 API_BASE、检测连接 | 入口统一处理 |

### 3.2 页面结构

```
src/filekb/ui/
├── app.py                    # ⭐ 入口文件 — 侧边栏 + 导航
├── pages/
│   ├── chat.py               # 💬 对话问答（默认首页）
│   ├── graph.py              # 🔗 知识图谱
│   ├── files.py              # 📁 文件管理
│   ├── entity_review.py      # 🔍 实体审核
│   ├── kb_management.py      # 📚 知识库管理（NEW — 从设置页拆出）
│   ├── settings.py           # ⚙️ 系统设置（仅 LLM/Embedding/OCR）
│   └── status.py             # 📊 系统状态
└── .streamlit/
    └── config.toml           # Streamlit 配置
```

### 3.3 app.py 入口设计

```python
# app.py — FileKB entrypoint
import streamlit as st

st.set_page_config(
    page_title="FileKB",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════
# 全局状态初始化
# ═══════════════════════════════════════════
def init_global_state():
    """Initialize all cross-page session_state keys once."""
    defaults = {
        "global_kb": "默认",          # 当前选中的知识库
        "pending_kb": None,           # 待确认的 KB 切换
        "kb_names": ["默认"],         # 可用 KB 列表（缓存）
        "kb_meta": {},                # KB 元数据缓存
        "kb_stats": {},               # 当前 KB 统计缓存
        "api_available": False,       # 后端 API 可用性
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_global_state()

# ═══════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════
def load_kb_data():
    """Load KB list and metadata from API. Cached for 30s."""
    import requests
    try:
        resp = requests.get("http://localhost:9494/settings", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            st.session_state.kb_names = data.get("kb_names", ["默认"])
            st.session_state.kb_meta = data.get("kb_meta", {})
            st.session_state.api_available = True
            return
    except Exception:
        pass
    st.session_state.api_available = False

def load_kb_stats(kb_name: str):
    """Load stats for a specific KB."""
    import requests
    try:
        resp = requests.get(f"http://localhost:9494/status?kb={kb_name}", timeout=5)
        if resp.status_code == 200:
            st.session_state.kb_stats = resp.json()
            return
    except Exception:
        pass
    st.session_state.kb_stats = {}

# Load on first run
if not st.session_state.kb_names or st.session_state.kb_names == ["默认"]:
    load_kb_data()

# Ensure current KB is valid
current_kb = st.session_state.global_kb
if current_kb not in st.session_state.kb_names:
    st.session_state.global_kb = st.session_state.kb_names[0]

# ═══════════════════════════════════════════
# 定义页面
# ═══════════════════════════════════════════
chat_page = st.Page("pages/chat.py", title="对话问答", icon="💬", default=True)
graph_page = st.Page("pages/graph.py", title="知识图谱", icon="🔗")
files_page = st.Page("pages/files.py", title="文件管理", icon="📁")
entity_page = st.Page("pages/entity_review.py", title="实体审核", icon="🔍")
kb_mgmt_page = st.Page("pages/kb_management.py", title="知识库管理", icon="📚")
settings_page = st.Page("pages/settings.py", title="系统设置", icon="⚙️")
status_page = st.Page("pages/status.py", title="系统状态", icon="📊")

# ═══════════════════════════════════════════
# 全局侧边栏
# ═══════════════════════════════════════════
with st.sidebar:
    st.title("📚 FileKB")
    st.caption("文件驱动 · 100% 本地")

    st.divider()

    # ── KB 选择器 ──
    st.subheader("📁 知识库")
    kb_names = st.session_state.kb_names
    current_kb = st.session_state.global_kb

    new_kb = st.selectbox(
        "当前知识库",
        options=kb_names,
        index=kb_names.index(current_kb) if current_kb in kb_names else 0,
        key="sidebar_kb_selector",
        label_visibility="collapsed",
    )

    # KB 切换确认协议（见 §7）
    if new_kb != current_kb and not st.session_state.get("_kb_switch_confirmed"):
        st.session_state.pending_kb = new_kb
    else:
        st.session_state.pending_kb = None

    if st.session_state.pending_kb:
        st.warning(f"⚠️ 切换到「{st.session_state.pending_kb}」将清空当前对话。")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ 确认", key="kb_confirm", use_container_width=True):
                st.session_state.global_kb = st.session_state.pending_kb
                st.session_state.messages = []  # 清空对话
                st.session_state.pending_kb = None
                load_kb_stats(st.session_state.global_kb)
                st.rerun()
        with c2:
            if st.button("❌ 取消", key="kb_cancel", use_container_width=True):
                st.session_state.pending_kb = None
                st.rerun()
    else:
        if new_kb != st.session_state.get("_last_kb"):
            st.session_state._last_kb = new_kb
            st.session_state.global_kb = new_kb
            load_kb_stats(new_kb)

    st.divider()

    # ── KB 统计卡片 ──
    stats = st.session_state.kb_stats
    if stats:
        health = stats.get("health", {})
        st.metric("文件数", stats.get("files_total", 0))
        st.metric("事实数", stats.get("facts_total", 0))
        st.metric("实体数", health.get("graph", {}).get("nodes", 0))
        st.metric("向量数", health.get("faiss", {}).get("vectors", 0))
    else:
        st.caption("加载统计中...")

    st.divider()

    # ── 快捷链接 ──
    if not st.session_state.api_available:
        st.error("⚠️ 后端 API 不可用")

# ═══════════════════════════════════════════
# 导航
# ═══════════════════════════════════════════
pg = st.navigation(
    [chat_page, graph_page, files_page, entity_page, kb_mgmt_page, settings_page, status_page]
)
pg.run()
```

### 3.4 页面文件模板

所有页面遵循统一模板，从 `st.session_state` 读取 KB 上下文：

```python
"""页面描述。"""
import streamlit as st
import requests

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 页面名", page_icon="💬", layout="wide")

# 从全局状态获取当前 KB
kb = st.session_state.get("global_kb", "默认")

# 页面标题
st.title("💬 页面标题")
st.caption("页面描述")

# ── 页面内容（使用 kb 参数调用 API）──
# resp = requests.get(f"{API_BASE}/endpoint?kb={kb}")
```

**关键规则**:
- 每页通过 `st.session_state.global_kb` 获取当前 KB
- 所有 API 调用带上 `?kb={kb}` 或 `{"kb": kb}` 参数
- **绝不** `from filekb.store import Store` 或其他核心引擎模块

---

## 4. 全局侧边栏设计

### 4.1 视觉布局

```
┌─────────────────────────┐
│  📚 FileKB              │  ← 系统 Logo + 名称
│  文件驱动 · 100% 本地    │  ← 副标题
│                         │
│ ─────────────────────── │
│                         │
│  📁 知识库               │  ← 分区标题
│  ┌─────────────────┐    │
│  │ 工作测试    ▾   │    │  ← KB 下拉选择器
│  └─────────────────┘    │
│                         │
│  （切换确认区域）         │  ← 仅在 pending_kb 时显示
│  ⚠️ 切换到「XX」将清空   │
│  当前对话。              │
│  [✅ 确认] [❌ 取消]     │
│                         │
│ ─────────────────────── │
│                         │
│  📊 当前知识库           │  ← 统计分区
│  文件数:    128         │
│  事实数:  3,452         │
│  实体数:    891         │
│  向量数:  4,210         │
│                         │
│ ─────────────────────── │
│                         │
│  💬 对话问答             │  ← 自动生成的导航
│  🔗 知识图谱             │     (st.navigation)
│  📁 文件管理             │
│  🔍 实体审核             │
│  📚 知识库管理           │
│  ⚙️ 系统设置             │
│  📊 系统状态             │
│                         │
└─────────────────────────┘
```

### 4.2 分区职责

| 分区 | 内容 | 更新时机 |
|------|------|---------|
| **系统标识** | Logo + 名称 + 副标题 | 静态 |
| **KB 选择器** | 下拉框 + 切换确认 | KB 创建/删除/重命名后刷新 |
| **KB 统计** | 4 个 metric（文件/事实/实体/向量） | KB 切换时重新加载；索引完成后应刷新（P2） |
| **导航菜单** | 7 个页面链接 | 静态（由 `st.navigation` 自动生成） |
| **状态指示** | API 可用性 | 每次页面加载时检测 |

### 4.3 KB 统计刷新策略

| 触发条件 | 刷新方式 |
|---------|---------|
| KB 切换（用户确认后） | 立即调用 `GET /status?kb=X` |
| 手动点击"刷新统计"按钮 | P2 需求，当前可加一个 `🔄` 小按钮 |
| 索引完成后 | P2 需求，需要后端推送或前端轮询 |

---

## 5. 页面详细设计

### 5.1 对话问答 (Chat)

**文件**: `pages/chat.py`  
**设计参考**: AnythingLLM 极简 Chat + Dify 流式反馈  
**优先级**: P0（核心功能）

#### 5.1.1 布局

```
┌─────────────────────────────────────────────┐
│  💬 知识库问答          当前: 工作测试 [📊]  │  ← 标题栏 + KB 标签
│  向你的知识库提问...                         │
├─────────────────────────────────────────────┤
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │ 用户: 遥感原理是什么？               │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │ 助手: 遥感是通过传感器...            │    │
│  │                                     │    │
│  │ 📎 参考来源                    [展开]│    │
│  │  · docs/遥感导论.md                 │    │
│  │  · docs/遥感原理笔记.md             │    │
│  │                                     │    │
│  │ 🔗 相关事实                   [展开]│    │
│  │  遥感 → 定义 → 非接触式探测技术     │    │
│  │  遥感 → 分类 → 光学遥感/微波遥感    │    │
│  │                                     │    │
│  │ [👍 有帮助]  [👎 没帮助]            │    │
│  └─────────────────────────────────────┘    │
│                                             │
├─────────────────────────────────────────────┤
│  ┌─────────────────────────────────────┐    │
│  │ 输入你的问题...                  📎 │    │
│  └─────────────────────────────────────┘    │
│  [🗑 清空对话]  [🔄 刷新统计]              │
└─────────────────────────────────────────────┘
```

#### 5.1.2 功能规格

| 功能 | 描述 | 状态 |
|------|------|------|
| 对话式问答 | `st.chat_input` + `st.chat_message` | ✅ 已实现 |
| 流式回答 | 逐 token 渲染 | ⚠️ 需实现（目前是阻塞式 `/ask`） |
| 来源引用 | 折叠面板显示文件列表 | ✅ 已实现 |
| 相关事实 | 折叠面板显示 fact 卡片 | ✅ 已实现 |
| 反馈按钮 | `[👍]` `[👎]` 调用 `/feedback` | ✅ 已实现 |
| KB 上下文 | 通过 `st.session_state.global_kb` 传递 | ⚠️ 已部分实现 |
| 空状态引导 | 无事实时显示 "尚未索引文件" | ⚠️ 需改进 |
| 清空对话 | 清除 `session_state.messages` | ✅ 已实现 |

#### 5.1.3 KB 切换时的行为

- 用户通过 sidebar 切换 KB → 弹出确认 → 确认后 `st.session_state.messages = []` → 页面重绘
- 对话历史与 KB 绑定，不同 KB 间隔离

#### 5.1.4 关键代码结构

```python
# pages/chat.py
import streamlit as st
import requests

API_BASE = "http://localhost:9494"
kb = st.session_state.get("global_kb", "默认")

st.title("💬 知识库问答")

# 初始化消息
if "messages" not in st.session_state:
    st.session_state.messages = []

# 渲染历史
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # ... sources, related_facts, feedback

# 输入
if question := st.chat_input("输入你的问题..."):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("搜索中..."):
            resp = requests.post(
                f"{API_BASE}/ask",
                json={"question": question, "kb": kb},
                timeout=120,
            )
            # ... 渲染回答
```

### 5.2 知识图谱 (Graph)

**文件**: `pages/graph.py`  
**设计参考**: LightRAG Sigma.js + NetworkX graphviz  
**优先级**: P0

#### 5.2.1 布局

```
┌─────────────────────────────────────────────┐
│  🔗 知识图谱浏览               当前: 工作测试 │
├─────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────┐ ┌──────┐ ┌──────┐   │
│  │ 搜索实体  │ │ 跳数 │ │ 布局 │ │ 过滤 │   │  ← 工具栏
│  │ [遥感    ]│ │ [1 ▾]│ │[力导向]│ │[全部▾]│  │
│  └──────────┘ └──────┘ └──────┘ └──────┘   │
│  ┌──────────────────────────────────────┐   │
│  │                                      │   │
│  │        [知识图谱可视化区域]           │   │
│  │        graphviz / pyvis              │   │
│  │                                      │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ┌─────────────┐  ┌───────────────────┐    │
│  │ 📋 实体列表  │  │ 📋 关系列表        │    │
│  │ 遥感原理 (5) │  │ 遥感 → 包含 → 光学 │    │
│  │ 光学遥感 (3) │  │ 遥感 → 包含 → 微波 │    │
│  │ 邓磊     (2) │  │ 邓磊 → 讲授 → 遥感 │    │
│  └─────────────┘  └───────────────────┘    │
└─────────────────────────────────────────────┘
```

#### 5.2.2 功能规格

| 功能 | 描述 | 状态 |
|------|------|------|
| 实体搜索 | 模糊搜索，输入时实时建议 | ⚠️ 已有，需加 KB 参数 |
| 跳数选择 | 1/2/3 hop BFS | ✅ 已实现 |
| KB 选择 | 从 `global_kb` 读取 | ❌ 需要加 |
| 图可视化 | graphviz DOT 渲染 | ✅ 已实现 |
| 节点染色 | 中心节点黄色，其他蓝色 | ⚠️ 可按实体类型增强 |
| 实体列表 | 按连接度排序 | ✅ 已实现 |
| 关系列表 | 三元组形式展示 | ✅ 已实现 |
| 布局切换 | 力导向 / 圆形 / 层级 | ❌ P2 需求 |
| 节点点击 | 显示属性 + 关联 fact | ❌ P2 需求 |
| 空状态 | 提示输入实体名称 | ✅ 已实现 |

#### 5.2.3 KB 关联

- API 调用加 `?kb={kb}` 参数
- 图谱数据按 KB 隔离（每个 KB 有独立的 NetworkX 图）

### 5.3 文件管理 (Files)

**文件**: `pages/files.py`  
**设计参考**: RAGFlow 文档列表 + 仪表盘卡片  
**优先级**: P0

#### 5.3.1 布局

```
┌─────────────────────────────────────────────┐
│  📁 文件管理                  当前: 工作测试 │
├─────────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐       │
│  │ 128  │ │ 120  │ │ 891  │ │ 4210 │       │  ← 统计卡片
│  │ 总文件│ │ 已索引│ │ 实体 │ │ 向量 │       │
│  └──────┘ └──────┘ └──────┘ └──────┘       │
│                                             │
│  筛选: [全部 ▾]  搜索: [___________] 🔍     │  ← 工具栏
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │ 文件列表（表格）                      │   │
│  │ 文件名    │ 路径    │ 状态 │ 大小 │...│   │
│  │ readme.md │ ~/Docs  │ ✅   │ 2KB  │   │  │
│  │ 遥感.md   │ ~/mmkv  │ ✅   │ 45KB │   │  │
│  │ old.pdf   │ ~/Docs  │ ⚠️   │ 1MB  │   │  │
│  └──────────────────────────────────────┘   │
│                                             │
│  [🔄 立即索引]  [📋 重试 DLQ]              │  ← 操作按钮
└─────────────────────────────────────────────┘
```

#### 5.3.2 功能规格

| 功能 | 描述 | 状态 |
|------|------|------|
| 统计卡片 | 文件数/事实数/实体数/向量数 | ❌ 当前无 KB 参数 |
| 文件列表表格 | 文件名/路径/状态/大小 | ❌ 需要新 API |
| 状态筛选 | 全部/已索引/失败/跳过 | ❌ 新功能 |
| 文件搜索 | 文件名模糊匹配 | ❌ 新功能 |
| 手动索引 | 触发全量索引 | ❌ 当前无 KB 参数 |
| DLQ 重试 | 重试失败队列 | ❌ 当前无 KB 参数 |
| 单文件操作 | 重索引/移除 | ❌ P2 需求 |

#### 5.3.3 需要的 API 增强

当前 `GET /status` 返回汇总数据，但文件列表需要新端点：

```
GET /files?kb={kb}&status={status}&search={q}&limit={n}&offset={m}
→ { files: [{id, path, status, size, indexed_at, fact_count}], total, has_more }
```

### 5.4 实体审核 (Entity Review)

**文件**: `pages/entity_review.py`  
**设计参考**: sift-kg review 工作流  
**优先级**: P1

#### 5.4.1 布局

```
┌─────────────────────────────────────────────┐
│  🔍 实体合并审核               当前: 工作测试 │
├─────────────────────────────────────────────┤
│  共 3 条提案待审核              [全部批准]   │  ← 批量操作
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │ ▼ "遥感原理" ↔ "遥感原理与方法"       │   │
│  │   提议: 遥感原理与方法  置信度: 92%   │   │
│  │   [✓ 批准] [✗ 拒绝] [✎ 编辑]        │   │
│  └──────────────────────────────────────┘   │
│  ┌──────────────────────────────────────┐   │
│  │ ▼ "邓磊" ↔ "邓磊（教授）"            │   │
│  │   提议: 邓磊  置信度: 88%            │   │
│  │   [✓ 批准] [✗ 拒绝] [✎ 编辑]        │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ── 已处理 ──                               │
│  ┌──────────────────────────────────────┐   │
│  │ ✓ "遥感概论" → "遥感原理与方法"       │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

#### 5.4.2 功能规格

| 功能 | 描述 | 状态 |
|------|------|------|
| 待审核列表 | 从 `/entities/proposals?kb={kb}` 获取 | ⚠️ 需加 KB 参数 |
| 批准 | 调用 `/entities/merge` | ✅ 已实现 |
| 拒绝 | 调用 `/entities/reject` | ✅ 已实现 |
| 编辑 | 修改 canonical_name 后批准 | ❌ P2 需求 |
| 批量批准 | 一键批准所有高置信度提案 | ❌ P2 需求 |
| 已处理折叠 | 已批准/拒绝的提案折叠显示 | ❌ 新功能 |
| 空状态 | "没有待审核的合并提案" | ✅ 已实现 |

### 5.5 知识库管理 (KB Management)

**文件**: `pages/kb_management.py`  
**设计参考**: RAGFlow 知识库卡片 + AnythingLLM Workspace 管理  
**优先级**: P0（从设置页拆出）

#### 5.5.1 布局

```
┌─────────────────────────────────────────────┐
│  📚 知识库管理                               │
│  创建、重命名、删除知识库，管理监控目录        │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────────────┐ ┌──────────────────┐  │
│  │ 📁 工作测试       │ │ 📁 遥感课程       │  │
│  │ 3 个目录         │ │ 1 个目录         │  │
│  │ 最后更新: 昨天   │ │ 最后更新: 3天前  │  │
│  │ [管理] [删除]    │ │ [管理] [删除]    │  │
│  └──────────────────┘ └──────────────────┘  │
│  ┌──────────────────┐ ┌──────────────────┐  │
│  │ 📁 遥感课程2      │ │ ➕ 新建知识库     │  │
│  │ 0 个目录         │ │                  │  │
│  │ 尚未添加目录     │ │                  │  │
│  │ [管理] [删除]    │ │                  │  │
│  └──────────────────┘ └──────────────────┘  │
│                                             │
│  ════════════════════════════════════════   │
│  选中知识库: 工作测试                        │  ← 选中后的详情面板
│  ┌──────────────────────────────────────┐   │
│  │ 📂 监控目录                          │   │
│  │ · ~/Documents/Codex          [移除]  │   │
│  │ · ~/Documents/每周总结       [移除]  │   │
│  │ · ~/Documents/财务           [移除]  │   │
│  │                                      │   │
│  │ ➕ 添加目录: [__________] [📁…]     │   │
│  │             ☑ 递归子目录  [添加]     │   │
│  └──────────────────────────────────────┘   │
│  [🗑 删除此知识库]                          │
└─────────────────────────────────────────────┘
```

#### 5.5.2 功能规格

| 功能 | 描述 | 状态 |
|------|------|------|
| KB 卡片列表 | 网格布局，显示名称/目录数/更新时间 | ❌ 新页面 |
| 创建 KB | 卡片中 `➕` 或顶部按钮 → 内联表单 | ⚠️ 逻辑已实现（在设置页） |
| 重命名 KB | 选中 KB → 内联编辑 | ⚠️ 逻辑已实现 |
| 删除 KB | 选中 KB → 二次确认 → 删除 | ⚠️ 逻辑已实现 |
| 目录列表 | 当前 KB 的目录清单 | ⚠️ 逻辑已实现 |
| 添加目录 | 路径输入 + 文件夹选择 + 递归选项 | ⚠️ 逻辑已实现 |
| 移除目录 | 单目录移除 | ⚠️ 逻辑已实现 |
| KB 搜索 | 卡片列表顶部搜索过滤 | ❌ 新功能 |
| 空状态 | "尚未创建知识库 → 点击新建" | ❌ 新功能 |
| 刷新侧边栏 | KB 变更后通知 sidebar 刷新 | ⚠️ 需要 st.rerun() |

#### 5.5.3 与设置页的关系

设置页原本的「📁 知识库管理」tab 移除，改为链接跳转：

```python
# 在 settings.py 中
st.page_link("pages/kb_management.py", label="📚 前往知识库管理")
```

#### 5.5.4 数据流

```
kb_management.py
  ├── GET /settings → kb_meta + directories
  ├── POST /settings {section: "knowledge_bases", data: {action: "create"}}
  ├── POST /settings {section: "knowledge_bases", data: {action: "rename"}}
  ├── POST /settings {section: "knowledge_bases", data: {action: "delete"}}
  ├── POST /settings {section: "directories", data: {action: "add"}}
  └── POST /settings {section: "directories", data: {action: "remove"}}
```

### 5.6 系统设置 (Settings)

**文件**: `pages/settings.py`  
**设计参考**: RAGFlow Admin 配置页  
**优先级**: P0

#### 5.6.1 变化概述

从设置页中**移除**「📁 知识库管理」tab。设置页仅保留：

| Tab | 内容 |
|-----|------|
| 🤖 大语言模型 | LLM API 地址、模型名、超时、上下文窗口 |
| 🧮 嵌入模型 | oMLX/Local 后端、模型名、地址 |
| 🔍 OCR 识别 | 启用/禁用、置信度、语言 |

#### 5.6.2 布局

```
┌─────────────────────────────────────────────┐
│  ⚙️ 系统设置                                │
├─────────────────────────────────────────────┤
│  [🤖 大语言模型] [🧮 嵌入模型] [🔍 OCR]     │  ← 3 个 tab
│                                             │
│  ── 🤖 大语言模型设置 ──                    │
│  API 地址: [http://127.0.0.1:8081/v1]      │
│  模型名称: [daily-agent-best-mtp]           │
│  超时(秒): [120]                             │
│  上下文窗口: [32768]                         │
│  [💾 保存 LLM 设置]                         │
│                                             │
│  📚 知识库管理已移至 → [知识库管理页面]      │  ← 链接
└─────────────────────────────────────────────┘
```

### 5.7 系统状态 (Status)

**文件**: `pages/status.py`  
**设计参考**: RAGFlow Admin UI 服务状态面板  
**优先级**: P1

#### 5.7.1 变化

增强为**KB 感知**：统计数据按当前选中的 KB 过滤。

#### 5.7.2 布局

```
┌─────────────────────────────────────────────┐
│  📊 系统状态                  当前: 工作测试 │
├─────────────────────────────────────────────┤
│  服务健康                                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐       │
│  │LLM ✅│ │SQLite│ │FAISS │ │Graph │       │
│  │在线  │ │2.3MB │ │4210  │ │891N  │       │
│  └──────┘ └──────┘ └──────┘ └──────┘       │
│                                             │
│  最近索引运行                                │
│  ┌──────────────────────────────────────┐   │
│  │ 2025-06-18 14:30  completed  128 文件│   │
│  └──────────────────────────────────────┘   │
│                                             │
│  [🔄 立即索引]  [📋 DLQ 重试]  [🔄 刷新]   │
│                                             │
│  📋 运行日志                   [INFO ▾] [50]│
│  ┌──────────────────────────────────────┐   │
│  │ 2025-06-18 14:30:01 [INFO] Scan...  │   │
│  │ 2025-06-18 14:30:05 [INFO] 128 files│   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

#### 5.7.3 KB 关联

- `GET /status?kb={kb}` → 所有统计按 KB 过滤
- 索引操作 `POST /index {"kb": kb}` → 仅索引当前 KB 的目录
- 日志仍然是全局的（所有 KB 共享一个 log 文件）

---

## 6. 状态管理与数据流

### 6.1 Session State 键规范

| Key | 类型 | 作用域 | 描述 |
|-----|------|--------|------|
| `global_kb` | `str` | 全局 | 当前选中的知识库名称 |
| `pending_kb` | `str\|None` | 全局 | 待确认切换的 KB |
| `_last_kb` | `str` | 全局 | 上一次确认的 KB（用于检测变更）|
| `kb_names` | `list[str]` | 全局（缓存） | 可用 KB 名称列表 |
| `kb_meta` | `dict` | 全局（缓存） | KB 元数据 |
| `kb_stats` | `dict` | 全局（缓存） | 当前 KB 统计信息 |
| `api_available` | `bool` | 全局 | API 是否可达 |
| `messages` | `list[dict]` | Chat 页面 | 对话历史 |
| `kb_*` | 各种 | KB 管理页面 | KB 管理表单状态 |

### 6.2 数据流图

```
                        ┌──────────────────┐
                        │   FastAPI (:9494) │
                        └────────┬─────────┘
                                 │ HTTP
                        ┌────────▼─────────┐
                        │    app.py         │
                        │  ┌─────────────┐  │
                        │  │ 初始化       │  │
                        │  │ session_state│  │
                        │  └──────┬──────┘  │
                        │         │         │
                        │  ┌──────▼──────┐  │
                        │  │ 加载 KB 列表 │  │ GET /settings
                        │  │ + 统计信息   │  │ GET /status?kb=
                        │  └──────┬──────┘  │
                        │         │         │
                        │  ┌──────▼──────┐  │
                        │  │ 渲染侧边栏   │  │
                        │  │ KB 选择器    │  │
                        │  │ + 统计卡片   │  │
                        │  └──────┬──────┘  │
                        └─────────┼─────────┘
                                  │ st.navigation
                    ┌─────────────┼──────────────┐
                    │             │              │
              ┌─────▼────┐ ┌─────▼────┐ ┌──────▼─────┐
              │ chat.py  │ │graph.py  │ │ files.py   │ ...
              │ read     │ │ read     │ │ read       │
              │ global_kb│ │ global_kb│ │ global_kb  │
              │ from     │ │ from     │ │ from       │
              │ session  │ │ session  │ │ session    │
              └──────────┘ └──────────┘ └────────────┘
```

### 6.3 数据刷新协议

```
用户操作 → API 调用 → 更新 session_state → st.rerun()

具体场景：
1. KB 切换确认 → global_kb 更新 → load_kb_stats() → st.rerun()
2. KB 创建     → KB 列表重新加载 → global_kb 更新 → st.rerun()
3. KB 删除     → global_kb 回退到"默认" → 重新加载 → st.rerun()
4. 目录添加    → 设置保存 → KB 统计刷新 → st.rerun()
5. 索引完成    → 手动刷新统计 → st.rerun()
```

---

## 7. KB 切换交互协议

### 7.1 完整流程

```
用户在 sidebar 选择新 KB
        │
        ▼
┌──────────────────────────┐
│ new_kb ≠ current_kb?     │─── No ──→ 无操作
└──────────────────────────┘
        │ Yes
        ▼
┌──────────────────────────┐
│ 设置 pending_kb = new_kb │
│ 触发 st.rerun()          │
└──────────────────────────┘
        │
        ▼
┌──────────────────────────┐
│ Sidebar 渲染确认区域     │
│ ⚠️ 切换到「XX」将清空    │
│    当前对话。             │
│ [✅ 确认]  [❌ 取消]     │
└──────────────────────────┘
        │
   ┌────┴────┐
   ▼         ▼
 确认       取消
   │         │
   ▼         ▼
 global_kb  pending_kb = None
 = pending  st.rerun()
 messages
 = []
 load_stats
 st.rerun()
```

### 7.2 受 KB 切换影响的页面状态

| 页面 | 状态 Key | 切换行为 |
|------|---------|---------|
| Chat | `messages` | **清空**（不同 KB 间对话独立） |
| Graph | 搜索结果 | **清空**（图数据源变化） |
| Files | 筛选状态 | **重置**（文件列表变化） |
| Entity Review | 展开状态 | **重置** |
| KB Management | 选中 KB | **不变**（管理页不依赖 global_kb） |
| Settings | 表单状态 | **不变**（全局设置） |
| Status | 日志选择 | **不变**，仅统计刷新 |

### 7.3 边缘情况

| 场景 | 行为 |
|------|------|
| 当前页是 KB 管理页时切换 KB | 不确认，直接切换（管理页不依赖 global_kb） |
| API 不可用时切换 KB | 仍允许切换，但统计显示为 "--" |
| 切换到已被删除的 KB | `global_kb` 自动回退到 "默认" |
| 快速连续切换 | 只处理最后一次 |

---

## 8. API 契约

### 8.1 KB 参数约定

所有涉及知识库数据的端点**必须**接受 `kb` 参数：

| 端点 | kb 参数位置 | 默认值 |
|------|-----------|--------|
| `POST /ask` | Body: `{"kb": "..."}` | `"默认"` |
| `POST /ask/stream` | Body: `{"kb": "..."}` | `"默认"` |
| `GET /graph` | Query: `?kb=...` | `"默认"` |
| `POST /index` | Body: `{"kb": "..."}` | `"默认"` |
| `GET /status` | Query: `?kb=...` | `"默认"` |
| `GET /facts` | Query: `?kb=...` | `"默认"` |
| `POST /feedback` | Body: `{"kb": "..."}` | `"默认"` |
| `GET /entities/proposals` | Query: `?kb=...` | `"默认"` |
| `POST /entities/merge` | Body: `{"kb": "..."}` | `"默认"` |
| `POST /entities/reject` | Body: `{"kb": "..."}` | `"默认"` |
| `GET /settings` | 无（全局配置） | — |
| `POST /settings` | 无（全局配置） | — |
| `GET /logs` | 无（全局日志） | — |

### 8.2 需要新增的 API（P1）

```yaml
# 文件列表 — 目前无此端点
GET /files?kb={kb}&status={status}&search={q}&limit=50&offset=0
Response:
  files:
    - id: int
      path: str
      status: str         # active | skipped | deleted
      size_bytes: int
      indexed_at: str     # ISO datetime or null
      fact_count: int
      error_msg: str | null
  total: int
  has_more: bool
```

### 8.3 需要增强的 API（P0）

- `GET /status?kb={kb}` — 已支持，但前端未使用 kb 参数
- `GET /graph?kb={kb}` — 已支持，但前端未传递 kb 参数
- `GET /entities/proposals?kb={kb}` — 已支持，前端需传递 kb
- `POST /index {"kb": kb}` — 已支持，前端需传递 kb

---

## 9. 实施计划

### Phase 1: 架构迁移（P0，核心变更）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1.1 | 创建 `app.py` 入口 + 全局侧边栏 + `st.navigation` | `app.py` |
| 1.2 | 移除旧的编号前缀，迁移页面到 `pages/` | 全部页面文件 |
| 1.3 | 实现 KB 切换确认协议 | `app.py` |
| 1.4 | 删除 `.streamlit/config.toml` 中旧配置（如有） | `.streamlit/config.toml` |

### Phase 2: KB 感知改造（P0）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 2.1 | Chat 页面从 `global_kb` 读取，API 调用加 `kb` | `pages/chat.py` |
| 2.2 | Graph 页面加 KB 参数 | `pages/graph.py` |
| 2.3 | Files 页面加 KB 参数 + API 调用 | `pages/files.py` |
| 2.4 | Entity Review 页面加 KB 参数 | `pages/entity_review.py` |
| 2.5 | Status 页面加 KB 参数 | `pages/status.py` |

### Phase 3: 知识库管理独立（P0）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 3.1 | 创建 KB 管理页面（卡片列表 + KB CRUD + 目录管理） | `pages/kb_management.py` |
| 3.2 | 从设置页移除 KB 管理 tab，替换为链接 | `pages/settings.py` |
| 3.3 | 实现 KB 变更后 sidebar 自动刷新 | `app.py` + `pages/kb_management.py` |

### Phase 4: 增强与测试

| 步骤 | 内容 |
|------|------|
| 4.1 | 流式回答实现（Chat 页面） |
| 4.2 | 文件列表 API + 前端（Files 页面） |
| 4.3 | 空状态引导完善（所有页面） |
| 4.4 | 端到端手动测试（KB 创建 → 添加目录 → 索引 → 搜索 → 问答） |

### 兼容性说明

- **后端 API 不变**：所有端点已有 `kb` 参数支持，无需修改 `server.py`
- **配置文件不变**：`config.yaml` 中 `kb_meta` + `directories.group` 结构保持
- **数据不变**：已有的 SQLite DB、FAISS 索引不受影响

---

## 附录 A: 页面清单与路由

| 路由 | 标题 | 图标 | 优先级 |
|------|------|------|--------|
| `/` (默认) | 对话问答 | 💬 | P0 |
| `/graph` | 知识图谱 | 🔗 | P0 |
| `/files` | 文件管理 | 📁 | P0 |
| `/entity_review` | 实体审核 | 🔍 | P1 |
| `/kb_management` | 知识库管理 | 📚 | P0 |
| `/settings` | 系统设置 | ⚙️ | P0 |
| `/status` | 系统状态 | 📊 | P1 |

## 附录 B: 调研参考链接

| 项目 | URL | 借鉴内容 |
|------|-----|---------|
| AnythingLLM | https://github.com/Mintplex-Labs/anything-llm | 工作区隔离、极简 Chat、侧边栏设计 |
| Dify | https://github.com/langgenius/dify | 流式 Chat、反馈按钮、工作区选择器 |
| Open WebUI | https://github.com/open-webui/open-webui | 侧边栏布局、模型选择器、Svelte Stores 模式 |
| RAGFlow | https://github.com/infiniflow/ragflow | KB 卡片管理、Admin 仪表盘、文档列表 |
| LightRAG | https://github.com/HKUDS/LightRAG | 图谱可视化、多布局切换、节点染色 |
| Streamlit Docs | https://docs.streamlit.io/develop/concepts/multipage-apps | `st.navigation`、widget 持久化、session_state |
