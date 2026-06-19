"""设置页面 — LLM、Embedding、OCR、知识库与目录管理。"""

import os
import subprocess
import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 设置", page_icon="⚙️", layout="wide")
st.title("⚙️ 系统设置")

# 加载当前设置
try:
    resp = requests.get(f"{API_BASE}/settings", timeout=5)
    settings = resp.json() if resp.status_code == 200 else {}
except Exception:
    settings = {}

# ============================================================
# LLM
# ============================================================
st.header("🤖 大语言模型 (LLM)")
llm = settings.get("llm", {})
c1, c2 = st.columns(2)
with c1:
    llm_url = st.text_input("API 地址", value=llm.get("base_url", "http://127.0.0.1:8081/v1"))
    llm_model = st.text_input("模型名称", value=llm.get("model", "daily-agent-best-mtp"))
with c2:
    llm_timeout = st.number_input("超时(秒)", value=llm.get("timeout", 120), min_value=10, max_value=600)
    llm_ctx = st.number_input("上下文窗口(tokens)", value=llm.get("context_window", 32768), min_value=4096, max_value=262144)

if st.button("💾 保存 LLM 设置"):
    requests.post(f"{API_BASE}/settings", json={
        "section": "llm",
        "data": {"base_url": llm_url, "model": llm_model, "timeout": llm_timeout, "context_window": llm_ctx},
    })
    st.success("LLM 设置已保存！重启服务生效。")

st.divider()

# ============================================================
# Embedding
# ============================================================
st.header("🧮 嵌入模型 (Embedding)")
emb = settings.get("embedding", {})
emb_backend = st.radio(
    "嵌入后端",
    options=["omlx", "local"],
    index=0 if emb.get("backend") == "omlx" else 1,
    format_func=lambda x: "🚀 oMLX (推荐, 14ms)" if x == "omlx" else "💻 本地 sentence-transformers (~2GB)",
)
c1, c2 = st.columns(2)
with c1:
    emb_model = st.text_input("模型名", value=emb.get("model", "bge-m3-mlx-fp16"))
with c2:
    emb_omxl_url = st.text_input("oMLX 地址", value=emb.get("omxl_url", "http://127.0.0.1:8081/v1"))

if st.button("💾 保存 Embedding 设置"):
    requests.post(f"{API_BASE}/settings", json={
        "section": "embedding",
        "data": {"backend": emb_backend, "model": emb_model, "omxl_url": emb_omxl_url},
    })
    st.success("Embedding 设置已保存！重启服务生效。")

st.divider()

# ============================================================
# OCR
# ============================================================
st.header("🔍 OCR 文字识别")
ocr_cfg = settings.get("ocr", {})
ocr_enabled = st.toggle("启用 OCR (Apple Vision, 仅 macOS)", value=ocr_cfg.get("enabled", True))
ocr_confidence = st.slider("最低识别置信度", 0.1, 1.0, value=ocr_cfg.get("min_confidence", 0.3), step=0.1)

if st.button("💾 保存 OCR 设置"):
    requests.post(f"{API_BASE}/settings", json={
        "section": "ocr",
        "data": {"enabled": ocr_enabled, "min_confidence": ocr_confidence,
                 "languages": ["zh-Hans", "zh-Hant", "en"]},
    })
    st.success("OCR 设置已保存！")

st.divider()

# ============================================================
# 知识库与目录管理
# ============================================================
st.header("📁 知识库与目录管理")

dirs = settings.get("directories", [])

# 将目录按 group 分组 → 每个 group 就是一个知识库
kb_map: dict[str, list[dict]] = {}
for d in dirs:
    g = d.get("group", "默认")
    kb_map.setdefault(g, []).append(d)

# 确保至少有一个"默认"知识库
if not kb_map:
    kb_map["默认"] = []

kb_names = sorted(kb_map.keys())

# ---- 左栏：知识库管理 ----
col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("知识库列表")
    st.caption("每个知识库独立存储，可包含多个目录。")

    # 选择当前操作的知识库
    if "selected_kb" not in st.session_state:
        st.session_state.selected_kb = kb_names[0] if kb_names else "默认"

    for kb_name in kb_names:
        selected = st.session_state.selected_kb == kb_name
        label = f"📂 {kb_name} ({len(kb_map[kb_name])} 个目录)"
        if st.button(label, key=f"sel_kb_{kb_name}",
                     type="primary" if selected else "secondary",
                     use_container_width=True):
            st.session_state.selected_kb = kb_name
            st.rerun()

    st.divider()

    # 新建知识库
    st.subheader("➕ 新建知识库")
    new_kb_name = st.text_input("知识库名称", placeholder="例如：遥感课程、个人笔记", key="new_kb_name")
    if st.button("创建知识库", type="primary", use_container_width=True):
        name = new_kb_name.strip()
        if name:
            if name in kb_map:
                st.error(f"知识库「{name}」已存在。")
            else:
                kb_map[name] = []
                st.session_state.selected_kb = name
                st.success(f"知识库「{name}」已创建。添加目录后自动生效。")
                st.rerun()
        else:
            st.warning("请输入知识库名称。")

with col_right:
    current_kb = st.session_state.selected_kb

    st.subheader(f"📂 {current_kb} — 监控目录")
    st.caption("此知识库下的所有目录中的文件将被索引到一起。")

    kb_dirs = kb_map.get(current_kb, [])

    if kb_dirs:
        for d in kb_dirs:
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"📁 `{d['path']}`")
                st.caption(f"递归: {'是' if d.get('recursive', True) else '否'}")
            with c2:
                pass
            with c3:
                if st.button("🗑 删除", key=f"del_{d['path']}"):
                    try:
                        r = requests.post(f"{API_BASE}/settings", json={
                            "section": "directories",
                            "data": {"action": "remove", "path": d["path"]},
                        })
                        if r.status_code == 200:
                            st.success("已删除！")
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))
    else:
        st.info("此知识库下还没有目录。在下方添加。")

    # 添加目录到当前 KB
    st.subheader("➕ 添加目录")

    # 两行：第一行是路径（文本框 + 浏览按钮），第二行是递归 + 添加按钮
    path_col, btn_col = st.columns([5, 1])
    with path_col:
        default_path = st.session_state.get("picked_dir", "")
        new_path = st.text_input("目录路径", value=default_path,
                                 placeholder="~/Documents/", key="new_dir_path",
                                 label_visibility="collapsed")
    with btn_col:
        if st.button("📁…", help="打开系统文件夹选择对话框", use_container_width=True):
            try:
                result = subprocess.run(
                    ["osascript", "-e",
                     'POSIX path of (choose folder with prompt "选择要监控的目录：")'],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0 and result.stdout.strip():
                    st.session_state.picked_dir = result.stdout.strip()
                    st.rerun()
            except Exception:
                pass

    rec_col, add_col = st.columns([1, 3])
    with rec_col:
        new_recursive = st.checkbox("递归扫描子目录", value=True)
    with add_col:
        if st.button("➕ 添加到此知识库", type="primary", use_container_width=True):
            if new_path and new_path.strip():
                try:
                    r = requests.post(f"{API_BASE}/settings", json={
                        "section": "directories",
                        "data": {
                            "action": "add", "path": new_path.strip(), "group": current_kb,
                            "recursive": new_recursive,
                            "exclude_patterns": [".git", "__pycache__", ".DS_Store"],
                        },
                    })
                    if r.status_code == 200:
                        st.success(f"已添加 `{new_path}` 到「{current_kb}」！")
                        # 清除已选路径
                        st.session_state.picked_dir = ""
                        st.rerun()
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("请输入目录路径或点击 📁… 选择。")

st.divider()
st.caption("💡 LLM / Embedding 设置保存后需重启服务。知识库和目录变更立即生效。")
