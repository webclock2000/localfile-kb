"""设置页面 — LLM、Embedding、OCR、知识库与目录管理。"""

import subprocess
import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 设置", page_icon="⚙️", layout="wide")

st.markdown("""
<style>
.section-title { font-size: 1.4rem; font-weight: 700; margin-top: 1.5rem; margin-bottom: 0.5rem; }
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

# Merge in session_state KBs that don't have dirs yet
if "extra_kb_names" not in st.session_state:
    st.session_state.extra_kb_names = set()
for n in st.session_state.extra_kb_names:
    if n not in kb_map:
        kb_map[n] = []

tab_llm, tab_emb, tab_ocr, tab_kb = st.tabs([
    "🤖 大语言模型", "🧮 嵌入模型", "🔍 OCR 识别", "📁 知识库管理",
])

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

with tab_emb:
    st.markdown('<p class="section-title">🧮 嵌入模型设置</p>', unsafe_allow_html=True)
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
        emb_model = st.text_input("模型名称", value=emb.get("model", "bge-m3-mlx-fp16"), key="emb_model")
    with c2:
        emb_omxl_url = st.text_input("oMLX 地址", value=emb.get("omxl_url", "http://127.0.0.1:8081/v1"), key="emb_omxl")
    if st.button("💾 保存 Embedding 设置", key="save_emb"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "embedding",
            "data": {"backend": emb_backend, "model": emb_model, "omxl_url": emb_omxl_url},
        })
        st.success("已保存，重启服务后生效。")

with tab_ocr:
    st.markdown('<p class="section-title">🔍 OCR 文字识别</p>', unsafe_allow_html=True)
    st.caption("Apple Vision 高精度中文 OCR。仅限 macOS。")
    ocr_cfg = settings.get("ocr", {})
    ocr_enabled = st.toggle("启用 OCR", value=ocr_cfg.get("enabled", True), key="ocr_toggle")
    ocr_confidence = st.slider("最低识别置信度", 0.1, 1.0, value=ocr_cfg.get("min_confidence", 0.3), step=0.1, key="ocr_conf")
    if st.button("💾 保存 OCR 设置", key="save_ocr"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "ocr",
            "data": {"enabled": ocr_enabled, "min_confidence": ocr_confidence, "languages": ["zh-Hans", "zh-Hant", "en"]},
        })
        st.success("已保存。")

with tab_kb:
    st.markdown('<p class="section-title">📁 知识库管理</p>', unsafe_allow_html=True)
    st.caption("一个知识库 = 一组目录。各知识库数据完全隔离。")

    # === 第一步：创建知识库 ===
    st.markdown("### 第一步：创建知识库")

    with st.container(border=True):
        c1, c2 = st.columns(2)
        with c1:
            new_kb_name = st.text_input("知识库名称 *", placeholder="例如：遥感课程、个人笔记", key="new_kb_name")
        with c2:
            st.text_input("描述（可选）", placeholder="简要说明这个知识库的内容", key="new_kb_desc")

        create_clicked = st.button("✨ 创建知识库", type="primary", key="create_kb")
        if create_clicked:
            name = new_kb_name.strip()
            if not name:
                st.warning("请输入知识库名称。")
            elif name in kb_map:
                st.error(f"知识库「{name}」已存在。")
            else:
                # 注册一个空目录来持久化 KB 到 config
                requests.post(f"{API_BASE}/settings", json={
                    "section": "directories",
                    "data": {"action": "add", "path": "", "group": name, "recursive": True, "placeholder": True},
                })
                st.session_state.extra_kb_names.add(name)
                st.session_state.selected_kb = name
                st.success(f"知识库「{name}」已创建！在下方添加目录。")
                st.rerun()

    st.divider()

    # === 第二步：加目录 ===
    st.markdown("### 第二步：为知识库添加目录")

    kb_names = sorted(kb_map.keys())
    if "selected_kb" not in st.session_state or st.session_state.selected_kb not in kb_map:
        st.session_state.selected_kb = kb_names[0]

    # KB 选择按钮行
    cols = st.columns(min(len(kb_names), 6))
    for i, name in enumerate(kb_names):
        with cols[i]:
            dcount = len(kb_map[name])
            selected = st.session_state.selected_kb == name
            if st.button(
                f"{'✅ ' if selected else ''}{name} ({dcount})",
                key=f"sel_{name}",
                type="primary" if selected else "secondary",
                width="stretch",
            ):
                st.session_state.selected_kb = name
                st.rerun()

    current = st.session_state.selected_kb
    kb_dirs = kb_map[current]

    st.divider()

    with st.container(border=True):
        st.markdown(f"#### 📂 {current} — 监控目录")

        real_dirs = [d for d in kb_dirs if d.get("path")]
        if real_dirs:
            for d in real_dirs:
                with st.container(border=True):
                    c1, c2 = st.columns([6, 1])
                    with c1:
                        st.markdown(f"📁 `{d['path']}`")
                        st.caption(f"递归: {'是' if d.get('recursive', True) else '否'}")
                    with c2:
                        if st.button("🗑", key=f"del_{d['path']}", help="删除"):
                            requests.post(f"{API_BASE}/settings", json={
                                "section": "directories", "data": {"action": "remove", "path": d["path"]},
                            })
                            st.rerun()
        else:
            st.info("尚无目录。在下方添加。")

        # 添加目录 — 用独立的 picked_ 前缀 key 避免 widget 冲突
        st.caption("添加目录：")

        path_col, btn_col = st.columns([5, 1])
        with path_col:
            dir_input = st.text_input(
                "目录路径",
                key=f"dir_input_{current}",
                placeholder="~/Documents/",
                label_visibility="collapsed",
            )
        with btn_col:
            browse_clicked = st.button("📁…", key=f"browse_{current}", help="系统文件夹选择", width="stretch")
            if browse_clicked:
                result = subprocess.run(
                    ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择要监控的目录：")'],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0 and result.stdout.strip():
                    st.session_state[f"dir_input_{current}"] = result.stdout.strip()
                    st.rerun()

        rec_col, add_col = st.columns([1, 3])
        with rec_col:
            new_recursive = st.checkbox("递归子目录", value=True, key=f"rec_{current}")
        with add_col:
            add_clicked = st.button("➕ 添加此目录", key=f"add_{current}", type="primary", width="stretch")
            if add_clicked:
                p = st.session_state.get(f"dir_input_{current}", "").strip()
                if p:
                    requests.post(f"{API_BASE}/settings", json={
                        "section": "directories",
                        "data": {"action": "add", "path": p, "group": current,
                                 "recursive": new_recursive,
                                 "exclude_patterns": [".git", "__pycache__", ".DS_Store"]},
                    })
                    st.session_state[f"dir_input_{current}"] = ""
                    st.success(f"已添加「{p}」")
                    st.rerun()
                else:
                    st.warning("请输入路径或点击 📁… 选择。")
