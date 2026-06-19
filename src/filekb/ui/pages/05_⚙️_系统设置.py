"""设置页面 — 配置 LLM、Embedding、OCR、目录管理。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 设置", page_icon="⚙️", layout="wide")
st.title("⚙️ 系统设置")
st.caption("配置本地 LLM、嵌入模型、OCR 和知识库目录。设置自动保存到 ~/.filekb/config.yaml")

# 加载当前设置
try:
    resp = requests.get(f"{API_BASE}/settings", timeout=5)
    settings = resp.json() if resp.status_code == 200 else {}
except Exception:
    settings = {}
    st.warning("无法连接到后端服务，显示默认设置。")

# ============================================================
# LLM 设置
# ============================================================
st.header("🤖 大语言模型 (LLM)")
st.caption("默认使用本地 oMLX 服务。")

llm = settings.get("llm", {})
c1, c2 = st.columns(2)
with c1:
    llm_url = st.text_input("API 地址", value=llm.get("base_url", "http://127.0.0.1:8081/v1"))
    llm_model = st.text_input("模型名称", value=llm.get("model", "daily-agent-best-mtp"))
with c2:
    llm_timeout = st.number_input("超时(秒)", value=llm.get("timeout", 120), min_value=10, max_value=600)
    llm_ctx = st.number_input("上下文窗口(tokens)", value=llm.get("context_window", 32768), min_value=4096, max_value=262144)

if st.button("💾 保存 LLM 设置"):
    try:
        r = requests.post(f"{API_BASE}/settings", json={
            "section": "llm",
            "data": {"base_url": llm_url, "model": llm_model, "timeout": llm_timeout, "context_window": llm_ctx},
        })
        if r.status_code == 200:
            st.success("LLM 设置已保存！重启服务后生效。")
    except Exception as e:
        st.error(f"保存失败: {e}")

st.divider()

# ============================================================
# Embedding 设置
# ============================================================
st.header("🧮 嵌入模型 (Embedding)")
st.caption("oMLX 后端更快(14ms)，本地后端不需要 oMLX 服务。")

emb = settings.get("embedding", {})
emb_backend = st.radio(
    "嵌入后端",
    options=["omlx", "local"],
    index=0 if emb.get("backend") == "omlx" else 1,
    format_func=lambda x: "🚀 oMLX (推荐, 14ms, 无需额外内存)" if x == "omlx" else "💻 本地 sentence-transformers (~2GB)",
)

c1, c2 = st.columns(2)
with c1:
    emb_model = st.text_input("模型名", value=emb.get("model", "bge-m3-mlx-fp16"))
with c2:
    emb_omxl_url = st.text_input("oMLX 地址(仅 oMLX 后端)", value=emb.get("omxl_url", "http://127.0.0.1:8081/v1"))

if st.button("💾 保存 Embedding 设置"):
    try:
        r = requests.post(f"{API_BASE}/settings", json={
            "section": "embedding",
            "data": {"backend": emb_backend, "model": emb_model, "omxl_url": emb_omxl_url},
        })
        if r.status_code == 200:
            st.success("Embedding 设置已保存！重启服务后生效。")
    except Exception as e:
        st.error(f"保存失败: {e}")

st.divider()

# ============================================================
# OCR 设置
# ============================================================
st.header("🔍 OCR 文字识别")
st.caption("Apple Vision 高精度中文 OCR (仅 macOS)。可随时开关。")

ocr_cfg = settings.get("ocr", {})
ocr_enabled = st.toggle("启用 OCR", value=ocr_cfg.get("enabled", True), help="关闭后仅提取有文本层的文档")
ocr_confidence = st.slider("最低识别置信度", 0.1, 1.0, value=ocr_cfg.get("min_confidence", 0.3), step=0.1)

if st.button("💾 保存 OCR 设置"):
    try:
        r = requests.post(f"{API_BASE}/settings", json={
            "section": "ocr",
            "data": {"enabled": ocr_enabled, "min_confidence": ocr_confidence, "languages": ["zh-Hans", "zh-Hant", "en"]},
        })
        if r.status_code == 200:
            st.success("OCR 设置已保存！")
    except Exception as e:
        st.error(f"保存失败: {e}")

st.divider()

# ============================================================
# 目录管理 (按知识库分组)
# ============================================================
st.header("📁 知识库目录管理")
st.caption("按分组管理监控的目录。同一分组内的目录共享知识库。")

dirs = settings.get("directories", [])

# 按 group 分组显示
groups: dict[str, list[dict]] = {}
for d in dirs:
    g = d.get("group", "默认")
    groups.setdefault(g, []).append(d)

for group_name, group_dirs in groups.items():
    with st.expander(f"📂 {group_name} ({len(group_dirs)} 个目录)", expanded=True):
        for d in group_dirs:
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"**{d['path']}**")
                st.caption(f"递归: {'是' if d.get('recursive', True) else '否'} | 排除: {', '.join(d.get('exclude_patterns', []))}")
            with c2:
                if st.button("🗑 删除", key=f"del_{d['path']}"):
                    try:
                        r = requests.post(f"{API_BASE}/settings", json={
                            "section": "directories",
                            "data": {"action": "remove", "path": d["path"]},
                        })
                        if r.status_code == 200:
                            st.success("已删除！刷新页面生效。")
                            st.rerun()
                    except Exception as e:
                        st.error(str(e))

# 添加新目录
import subprocess

st.subheader("➕ 添加监控目录")

c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
with c1:
    # 如果 session_state 里有选中的路径就用它
    default_path = st.session_state.get("picked_dir", "")
    new_path = st.text_input("目录路径", value=default_path, placeholder="~/Documents/Work/", key="new_dir_path")
with c2:
    st.caption("")  # 对齐占位
    if st.button("📁…", help="打开系统文件夹选择对话框"):
        try:
            result = subprocess.run(
                ["osascript", "-e",
                 'POSIX path of (choose folder with prompt "选择要监控的目录：")'],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                picked = result.stdout.strip()
                st.session_state.picked_dir = picked
                st.rerun()
            elif result.returncode != 0:
                # 用户取消了对话框
                pass
        except Exception:
            pass
with c3:
    existing = list(groups.keys())
    all_groups = ["工作", "生活", "默认"] + [g for g in existing if g not in ("工作", "生活", "默认")]
    new_group = st.selectbox("分组", all_groups, key="new_dir_group")
with c4:
    new_recursive = st.checkbox("递归扫描", value=True)

if st.button("➕ 添加目录", type="primary"):
    if new_path:
        try:
            r = requests.post(f"{API_BASE}/settings", json={
                "section": "directories",
                "data": {
                    "action": "add", "path": new_path, "group": new_group,
                    "recursive": new_recursive,
                    "exclude_patterns": [".git", "__pycache__", ".DS_Store"],
                },
            })
            if r.status_code == 200:
                st.success(f"已添加 {new_path} 到「{new_group}」！")
                st.rerun()
        except Exception as e:
            st.error(str(e))
    else:
        st.warning("请输入目录路径。")

st.divider()

# 底部提示
st.caption("💡 提示：所有设置保存到 ~/.filekb/config.yaml。修改 LLM/Embedding 设置后需重启服务。目录变更立即生效。")
