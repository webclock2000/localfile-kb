"""设置页面 — LLM、Embedding、OCR、知识库与目录管理。"""

import subprocess
import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 设置", page_icon="⚙️", layout="wide")
st.title("⚙️ 系统设置")

try:
    resp = requests.get(f"{API_BASE}/settings", timeout=5)
    settings = resp.json() if resp.status_code == 200 else {}
except Exception:
    settings = {}

tab_llm, tab_emb, tab_ocr, tab_kb = st.tabs([
    "🤖 LLM", "🧮 Embedding", "🔍 OCR", "📁 知识库与目录",
])

# ========================================================================
# TAB 1: LLM
# ========================================================================
with tab_llm:
    st.caption("配置本地大语言模型服务。默认使用 oMLX。")
    llm = settings.get("llm", {})
    c1, c2 = st.columns(2)
    with c1:
        llm_url = st.text_input("API 地址", value=llm.get("base_url", "http://127.0.0.1:8081/v1"), key="llm_url")
        llm_timeout = st.number_input("超时(秒)", value=llm.get("timeout", 120), min_value=10, max_value=600, key="llm_timeout")
    with c2:
        llm_model = st.text_input("模型名称", value=llm.get("model", "daily-agent-best-mtp"), key="llm_model")
        llm_ctx = st.number_input("上下文窗口(tokens)", value=llm.get("context_window", 32768), min_value=4096, max_value=262144, key="llm_ctx")
    if st.button("💾 保存 LLM 设置", key="save_llm"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "llm",
            "data": {"base_url": llm_url, "model": llm_model, "timeout": llm_timeout, "context_window": llm_ctx},
        })
        st.success("已保存，重启服务后生效。")

# ========================================================================
# TAB 2: Embedding
# ========================================================================
with tab_emb:
    st.caption("嵌入模型将文本转为向量，用于语义搜索。")
    emb = settings.get("embedding", {})
    emb_backend = st.radio(
        "后端", options=["omlx", "local"],
        index=0 if emb.get("backend") == "omlx" else 1,
        format_func=lambda x: "🚀 oMLX (推荐, 14ms, 省2GB内存)" if x == "omlx" else "💻 本地 sentence-transformers (~2GB)",
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
# TAB 3: OCR
# ========================================================================
with tab_ocr:
    st.caption("Apple Vision 高精度中文 OCR，仅限 macOS。可随时开关。")
    ocr_cfg = settings.get("ocr", {})
    ocr_enabled = st.toggle("启用 OCR", value=ocr_cfg.get("enabled", True), key="ocr_toggle")
    ocr_confidence = st.slider("最低识别置信度", 0.1, 1.0, value=ocr_cfg.get("min_confidence", 0.3), step=0.1, key="ocr_conf")
    if st.button("💾 保存 OCR 设置", key="save_ocr"):
        requests.post(f"{API_BASE}/settings", json={
            "section": "ocr",
            "data": {"enabled": ocr_enabled, "min_confidence": ocr_confidence,
                     "languages": ["zh-Hans", "zh-Hant", "en"]},
        })
        st.success("已保存。")

# ========================================================================
# TAB 4: 知识库与目录
# ========================================================================
with tab_kb:
    st.caption("每个知识库独立存储，可包含多个监控目录。")

    dirs = settings.get("directories", [])
    kb_map: dict[str, list[dict]] = {}
    for d in dirs:
        g = d.get("group", "默认")
        kb_map.setdefault(g, []).append(d)
    if not kb_map:
        kb_map["默认"] = []
    kb_names = sorted(kb_map.keys())

    if "selected_kb" not in st.session_state:
        st.session_state.selected_kb = kb_names[0]

    # 上面：知识库选择行
    kb_tabs = st.tabs([f"📂 {n} ({len(kb_map[n])}个目录)" for n in kb_names])
    for i, (kb_name, kb_tab) in enumerate(zip(kb_names, kb_tabs)):
        with kb_tab:
            st.session_state.selected_kb = kb_name
            current = kb_name
            kb_dirs = kb_map[current]

            # 已有目录
            if kb_dirs:
                for d in kb_dirs:
                    with st.container(border=True):
                        c1, c2 = st.columns([5, 1])
                        with c1:
                            st.markdown(f"📁 `{d['path']}`")
                            st.caption(f"递归扫描: {'是' if d.get('recursive', True) else '否'}")
                        with c2:
                            if st.button("🗑", key=f"del_{d['path']}", help="删除此目录"):
                                requests.post(f"{API_BASE}/settings", json={
                                    "section": "directories", "data": {"action": "remove", "path": d["path"]},
                                })
                                st.rerun()
            else:
                st.info("此知识库下还没有监控目录。在下方添加第一个目录。")

            # 添加目录
            st.divider()
            st.caption("添加目录到此知识库：")

            path_col, btn_col = st.columns([5, 1])
            with path_col:
                picked = st.session_state.get("picked_dir", "")
                new_path = st.text_input("目录路径", value=picked, placeholder="~/Documents/", key=f"path_{current}", label_visibility="collapsed")
            with btn_col:
                if st.button("📁…", key=f"browse_{current}", help="打开系统文件夹选择对话框", use_container_width=True):
                    result = subprocess.run(
                        ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择要监控的目录：")'],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        st.session_state.picked_dir = result.stdout.strip()
                        st.rerun()

            rec_col, add_col = st.columns([1, 3])
            with rec_col:
                new_recursive = st.checkbox("递归扫描子目录", value=True, key=f"rec_{current}")
            with add_col:
                if st.button("➕ 添加", key=f"add_{current}", type="primary", use_container_width=True):
                    if new_path and new_path.strip():
                        requests.post(f"{API_BASE}/settings", json={
                            "section": "directories",
                            "data": {"action": "add", "path": new_path.strip(), "group": current,
                                     "recursive": new_recursive,
                                     "exclude_patterns": [".git", "__pycache__", ".DS_Store"]},
                        })
                        st.session_state.picked_dir = ""
                        st.rerun()
                    else:
                        st.warning("请输入路径或点击 📁… 选择。")

    # 新建知识库
    with st.expander("➕ 新建知识库"):
        new_name = st.text_input("知识库名称", placeholder="例如：遥感课程、个人笔记", key="new_kb")
        if st.button("创建", type="primary", key="create_kb"):
            name = new_name.strip()
            if name and name not in kb_map:
                st.session_state.selected_kb = name
                st.success(f"知识库「{name}」已创建。切换到对应标签页添加目录。")
                st.rerun()
            elif name in kb_map:
                st.error("已存在同名知识库。")
