"""设置页面 — LLM、Embedding、OCR、知识库与目录管理。"""

import subprocess
import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 设置", page_icon="⚙️", layout="wide")

# ============================================================
# 样式
# ============================================================
st.markdown("""
<style>
.section-title { font-size: 1.4rem; font-weight: 700; margin-top: 1.5rem; margin-bottom: 0.5rem; }
.kb-card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }
</style>
""", unsafe_allow_html=True)

st.title("⚙️ 系统设置")

try:
    resp = requests.get(f"{API_BASE}/settings", timeout=5)
    settings = resp.json() if resp.status_code == 200 else {}
except Exception:
    settings = {}

dirs = settings.get("directories", [])
kb_map: dict[str, list[dict]] = {}
for d in dirs:
    g = d.get("group", "默认")
    kb_map.setdefault(g, []).append(d)
if not kb_map:
    kb_map["默认"] = []

tab_llm, tab_emb, tab_ocr, tab_kb = st.tabs([
    "🤖 大语言模型", "🧮 嵌入模型", "🔍 OCR 识别", "📁 知识库管理",
])

# ========================================================================
# TAB: LLM
# ========================================================================
with tab_llm:
    st.markdown('<p class="section-title">🤖 大语言模型设置</p>', unsafe_allow_html=True)
    st.caption("配置本地 LLM 服务地址和模型参数。默认使用 oMLX。")
    llm = settings.get("llm", {})
    c1, c2 = st.columns(2)
    with c1:
        llm_url = st.text_input("API 地址", value=llm.get("base_url", "http://127.0.0.1:8081/v1"), key="llm_url")
        llm_timeout = st.number_input("超时(秒)", value=llm.get("timeout", 120), min_value=10, max_value=600, key="llm_timeout")
    with c2:
        llm_model = st.text_input("模型名称", value=llm.get("model", "daily-agent-best-mtp"), key="llm_model")
        llm_ctx = st.number_input("上下文窗口 (tokens)", value=llm.get("context_window", 32768), min_value=4096, max_value=262144, key="llm_ctx")
    if st.button("💾 保存 LLM 设置", key="save_llm"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "llm",
            "data": {"base_url": llm_url, "model": llm_model, "timeout": llm_timeout, "context_window": llm_ctx},
        })
        st.success("已保存，重启服务后生效。")

# ========================================================================
# TAB: Embedding
# ========================================================================
with tab_emb:
    st.markdown('<p class="section-title">🧮 嵌入模型设置</p>', unsafe_allow_html=True)
    st.caption("嵌入模型将文本转为向量，用于语义搜索。")
    emb = settings.get("embedding", {})
    emb_backend = st.radio(
        "后端选择",
        options=["omlx", "local"],
        index=0 if emb.get("backend") == "omlx" else 1,
        format_func=lambda x: "🚀 oMLX（推荐，14ms，不占内存）" if x == "omlx" else "💻 本地 sentence-transformers（~2GB）",
        key="emb_backend",
    )
    c1, c2 = st.columns(2)
    with c1:
        emb_model = st.text_input("模型名称", value=emb.get("model", "bge-m3-mlx-fp16"), key="emb_model")
    with c2:
        emb_omxl_url = st.text_input("oMLX 地址", value=emb.get("omxl_url", "http://127.0.0.1:8081/v1"), key="emb_omxl")
    if st.button("💾 保存 Embedding 设置", key="save_emb"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "embedding",
            "data": {"backend": emb_backend, "model": emb_model, "omxl_url": emb_omxl_url},
        })
        st.success("已保存，重启服务后生效。")

# ========================================================================
# TAB: OCR
# ========================================================================
with tab_ocr:
    st.markdown('<p class="section-title">🔍 OCR 文字识别</p>', unsafe_allow_html=True)
    st.caption("Apple Vision 高精度中文 OCR。仅限 macOS，可随时开关。")
    ocr_cfg = settings.get("ocr", {})
    ocr_enabled = st.toggle("启用 OCR", value=ocr_cfg.get("enabled", True), key="ocr_toggle")
    ocr_confidence = st.slider("最低识别置信度", 0.1, 1.0, value=ocr_cfg.get("min_confidence", 0.3), step=0.1, key="ocr_conf")
    if st.button("💾 保存 OCR 设置", key="save_ocr"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "ocr",
            "data": {"enabled": ocr_enabled, "min_confidence": ocr_confidence, "languages": ["zh-Hans", "zh-Hant", "en"]},
        })
        st.success("已保存。")

# ========================================================================
# TAB: 知识库管理
# ========================================================================
with tab_kb:
    st.markdown('<p class="section-title">📁 知识库管理</p>', unsafe_allow_html=True)
    st.caption("一个知识库 = 一组目录。文件放入目录后自动索引，各知识库数据隔离。")

    # ---- STEP 1: 创建知识库 ----
    st.markdown("### 第一步：创建知识库")
    st.caption("给知识库起个名字（如「遥感课程」「个人笔记」），可选填描述。")

    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            new_kb_name = st.text_input("知识库名称 *", placeholder="例如：遥感课程、个人笔记、工作文档", key="new_kb_name")
        with c2:
            new_kb_desc = st.text_input("描述（可选）", placeholder="简要说明这个知识库的内容", key="new_kb_desc")
        if st.button("✨ 创建知识库", type="primary", key="create_kb"):
            name = new_kb_name.strip()
            if not name:
                st.warning("请输入知识库名称。")
            elif name in kb_map:
                st.error(f"知识库「{name}」已存在。")
            else:
                kb_map[name] = []
                st.session_state.selected_kb = name
                st.success(f"知识库「{name}」已创建！现在在下方为它添加目录。")
                st.rerun()

    st.divider()

    # ---- STEP 2: 选择知识库 + 管理目录 ----
    st.markdown("### 第二步：为知识库添加目录")

    # 知识库选择
    kb_names = sorted(kb_map.keys())
    if "selected_kb" not in st.session_state or st.session_state.selected_kb not in kb_map:
        st.session_state.selected_kb = kb_names[0]

    cols = st.columns(min(len(kb_names), 6))
    for i, name in enumerate(kb_names):
        with cols[i]:
            dcount = len(kb_map[name])
            selected = st.session_state.selected_kb == name
            if st.button(
                f"{'✅ ' if selected else ''}📂 {name}\n({dcount}个目录)",
                key=f"sel_{name}",
                type="primary" if selected else "secondary",
                use_container_width=True,
            ):
                st.session_state.selected_kb = name
                st.rerun()

    current = st.session_state.selected_kb
    kb_dirs = kb_map[current]

    st.divider()

    with st.container(border=True):
        st.markdown(f"#### 📂 {current} — 监控目录")

        if kb_dirs:
            for d in kb_dirs:
                with st.container(border=True):
                    c1, c2 = st.columns([6, 1])
                    with c1:
                        st.markdown(f"📁 `{d['path']}`")
                        st.caption(f"递归: {'是' if d.get('recursive', True) else '否'}")
                    with c2:
                        if st.button("🗑", key=f"del_{d['path']}", help="删除此目录"):
                            requests.post(f"{API_BASE}/settings", json={
                                "section": "directories", "data": {"action": "remove", "path": d["path"]},
                            })
                            st.rerun()
        else:
            st.info("尚无目录。在下方添加。")

        # 添加目录 — 使用 session_state 直接管理路径值
        st.caption("添加目录：")

        # 初始化路径状态
        dir_key = f"dir_path_{current}"
        if dir_key not in st.session_state:
            st.session_state[dir_key] = ""

        path_col, btn_col = st.columns([5, 1])
        with path_col:
            new_path = st.text_input(
                "目录路径",
                key=dir_key,
                placeholder="~/Documents/",
                label_visibility="collapsed",
            )
        with btn_col:
            if st.button("📁…", key=f"browse_{current}", help="系统文件夹选择", use_container_width=True):
                result = subprocess.run(
                    ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择要监控的目录：")'],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0 and result.stdout.strip():
                    st.session_state[dir_key] = result.stdout.strip()
                    st.rerun()

        rec_col, add_col = st.columns([1, 3])
        with rec_col:
            new_recursive = st.checkbox("递归子目录", value=True, key=f"rec_{current}")
        with add_col:
            if st.button("➕ 添加此目录", key=f"add_{current}", type="primary", use_container_width=True):
                p = st.session_state.get(dir_key, "").strip()
                if p:
                    requests.post(f"{API_BASE}/settings", json={
                        "section": "directories",
                        "data": {"action": "add", "path": p, "group": current,
                                 "recursive": new_recursive,
                                 "exclude_patterns": [".git", "__pycache__", ".DS_Store"]},
                    })
                    st.session_state[dir_key] = ""
                    st.rerun()
                else:
                    st.warning("请输入路径或点击 📁… 选择。")
