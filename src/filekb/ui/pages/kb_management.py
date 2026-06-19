"""知识库管理页面 — 创建、重命名、删除知识库，管理监控目录。

独立页面，不依赖 global_kb（管理的是 KB 本身，而非在某个 KB 内操作）。
从设置页拆分出来，采用卡片网格布局。
"""

from __future__ import annotations

import subprocess
import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── Helpers ──

def _load_kb_data() -> tuple[dict[str, list[dict]], dict]:
    """Load KB map + metadata from API. Returns (kb_map, kb_meta)."""
    try:
        resp = requests.get(f"{API_BASE}/settings", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            dirs = data.get("directories", [])
            kb_meta = data.get("kb_meta", {})

            kb_map: dict[str, list[dict]] = {}
            for name in kb_meta:
                kb_map.setdefault(name, [])
            for d in dirs:
                g = d.get("group", "默认")
                kb_map.setdefault(g, []).append(d)
            if not kb_map:
                kb_map["默认"] = []

            return kb_map, kb_meta
    except Exception:
        pass
    return {"默认": []}, {}

@st.cache_data(ttl=10)
def _load_kb_data_cached():
    """Cached wrapper to avoid repeated API calls within a single rerun."""
    return _load_kb_data()

# ── Session state init ──
for k, v in [
    ("kb_mgmt_mode", "view"),       # view | create | rename | delete_confirm
    ("kb_mgmt_form", ""),           # form text field
    ("kb_mgmt_target", ""),         # target KB for rename/delete
    ("kb_mgmt_delete_confirm", False),
]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── Header ──
st.title("📚 知识库管理")
st.caption("创建、重命名、删除知识库。每个知识库拥有独立的索引和存储。")

# ── Load data ──
kb_map, kb_meta = _load_kb_data_cached()
kb_names = sorted(kb_map.keys())

st.divider()

# ── Toolbar ──
tb1, tb2 = st.columns([3, 1])
with tb1:
    search = st.text_input(
        "搜索知识库",
        placeholder="按名称筛选...",
        label_visibility="collapsed",
    )
with tb2:
    if st.button("➕ 新建知识库", use_container_width=True, type="primary"):
        st.session_state.kb_mgmt_mode = "create"
        st.session_state.kb_mgmt_form = ""
        st.rerun()

# ── Create panel ──
if st.session_state.kb_mgmt_mode == "create":
    with st.container(border=True):
        st.markdown("#### ➕ 新建知识库")
        st.caption("知识库是一组目录的集合，拥有独立的 SQLite 数据库和 FAISS 索引。")

        def _create_kb():
            name = st.session_state.kb_mgmt_form.strip()
            if not name:
                st.session_state._mgmt_error = "请输入知识库名称。"
                return
            try:
                resp = requests.post(
                    f"{API_BASE}/settings",
                    json={
                        "section": "knowledge_bases",
                        "data": {"action": "create", "name": name, "description": ""},
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    st.session_state._mgmt_success = f"知识库「{name}」已创建。"
                    st.session_state.kb_mgmt_mode = "view"
                    st.session_state.kb_mgmt_form = ""
                    st.cache_data.clear()
                    st.rerun()
                elif resp.status_code == 409:
                    st.session_state._mgmt_error = f"知识库「{name}」已存在。"
                else:
                    st.session_state._mgmt_error = f"创建失败 ({resp.status_code})"
            except Exception as e:
                st.session_state._mgmt_error = f"连接失败: {e}"

        cc1, cc2, cc3 = st.columns([3, 1, 1])
        with cc1:
            st.text_input("名称", key="kb_mgmt_form", placeholder="例如：遥感课程",
                          label_visibility="collapsed")
        with cc2:
            st.button("✅ 确认创建", key="btn_create_confirm",
                     on_click=_create_kb, use_container_width=True)
        with cc3:
            if st.button("❌ 取消", key="btn_create_cancel", use_container_width=True):
                st.session_state.kb_mgmt_mode = "view"
                st.rerun()

# ── Rename panel ──
if st.session_state.kb_mgmt_mode == "rename":
    target = st.session_state.kb_mgmt_target
    with st.container(border=True):
        st.markdown(f"#### ✏️ 重命名「{target}」")

        def _rename_kb():
            new_name = st.session_state.kb_mgmt_form.strip()
            if not new_name:
                st.session_state._mgmt_error = "请输入新名称。"
                return
            if new_name == target:
                st.session_state.kb_mgmt_mode = "view"
                return
            try:
                resp = requests.post(
                    f"{API_BASE}/settings",
                    json={
                        "section": "knowledge_bases",
                        "data": {"action": "rename", "old_name": target, "new_name": new_name},
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    st.session_state._mgmt_success = f"已重命名为「{new_name}」。"
                    # Update global KB selector if current
                    if st.session_state.get("global_kb") == target:
                        st.session_state.global_kb = new_name
                    st.session_state.kb_mgmt_mode = "view"
                    st.session_state.kb_mgmt_form = ""
                    st.session_state.kb_mgmt_target = ""
                    st.cache_data.clear()
                    st.rerun()
                elif resp.status_code == 409:
                    st.session_state._mgmt_error = f"「{new_name}」已存在。"
                else:
                    st.session_state._mgmt_error = f"重命名失败 ({resp.status_code})"
            except Exception as e:
                st.session_state._mgmt_error = f"连接失败: {e}"

        rc1, rc2, rc3 = st.columns([3, 1, 1])
        with rc1:
            st.text_input("新名称", key="kb_mgmt_form", label_visibility="collapsed")
        with rc2:
            st.button("✅ 确认", key="btn_rename_confirm",
                     on_click=_rename_kb, use_container_width=True)
        with rc3:
            if st.button("❌ 取消", key="btn_rename_cancel", use_container_width=True):
                st.session_state.kb_mgmt_mode = "view"
                st.rerun()

# ── Delete confirm panel ──
if st.session_state.kb_mgmt_mode == "delete_confirm":
    target = st.session_state.kb_mgmt_target
    with st.container(border=True):
        st.warning(f"⚠️ 确认删除知识库「{target}」及其所有目录配置？此操作**不可逆**。")

        def _delete_kb():
            try:
                resp = requests.post(
                    f"{API_BASE}/settings",
                    json={
                        "section": "knowledge_bases",
                        "data": {"action": "delete", "name": target},
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    st.session_state._mgmt_success = f"知识库「{target}」已删除。"
                    if st.session_state.get("global_kb") == target:
                        st.session_state.global_kb = "默认"
                    st.session_state.kb_mgmt_mode = "view"
                    st.session_state.kb_mgmt_target = ""
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.session_state._mgmt_error = f"删除失败 ({resp.status_code}): {resp.text}"
            except Exception as e:
                st.session_state._mgmt_error = f"连接失败: {e}"

        dc1, dc2 = st.columns([3, 1])
        with dc1:
            st.checkbox("我确认要删除此知识库及其所有目录", key="kb_mgmt_delete_confirm")
        with dc2:
            st.button(
                "🗑 确认删除", key="btn_delete_do",
                disabled=not st.session_state.kb_mgmt_delete_confirm,
                on_click=_delete_kb, use_container_width=True, type="primary",
            )
        if st.button("❌ 取消", key="btn_delete_cancel"):
            st.session_state.kb_mgmt_mode = "view"
            st.rerun()

st.divider()

# ── KB Card Grid ──
filtered_names = [n for n in kb_names if search.lower() in n.lower()] if search else kb_names

if not filtered_names:
    st.info("没有匹配的知识库。点击「➕ 新建知识库」来创建第一个。")
else:
    # Render cards in rows of 3
    cols = st.columns(3)
    for i, name in enumerate(filtered_names):
        col = cols[i % 3]
        with col:
            dirs = kb_map.get(name, [])
            meta = kb_meta.get(name, {})
            desc = meta.get("description", "") if isinstance(meta, dict) else ""

            with st.container(border=True):
                # Card header
                st.markdown(f"#### 📁 {name}")
                if desc:
                    st.caption(desc)

                # Stats
                dir_count = len([d for d in dirs if d.get("path")])
                st.caption(f"{dir_count} 个监控目录")

                # Actions
                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("✏️ 重命名", key=f"rn_{name}", use_container_width=True):
                        st.session_state.kb_mgmt_mode = "rename"
                        st.session_state.kb_mgmt_target = name
                        st.session_state.kb_mgmt_form = name
                        st.rerun()
                with bc2:
                    if name != "默认":
                        if st.button("🗑 删除", key=f"del_{name}", use_container_width=True):
                            st.session_state.kb_mgmt_mode = "delete_confirm"
                            st.session_state.kb_mgmt_target = name
                            st.session_state.kb_mgmt_delete_confirm = False
                            st.rerun()

# ── Directory management for selected KB ──
st.divider()
st.markdown("### 📂 目录管理")

# KB selector for directory management
dir_kb_names = sorted(kb_map.keys())
dkb_col, _ = st.columns([2, 3])
with dkb_col:
    selected_kb = st.selectbox(
        "选择要管理的知识库",
        options=dir_kb_names,
        index=dir_kb_names.index(st.session_state.get("kb_mgmt_selected_dir_kb", dir_kb_names[0]))
        if st.session_state.get("kb_mgmt_selected_dir_kb") in dir_kb_names else 0,
        key="kb_mgmt_dir_selector",
    )
    st.session_state.kb_mgmt_selected_dir_kb = selected_kb

st.caption(f"「{selected_kb}」的监控目录列表。添加目录后，文件变更会自动入库。")

# Directory list
real_dirs = [d for d in kb_map.get(selected_kb, []) if d.get("path")]
if real_dirs:
    for d in real_dirs:
        with st.container(border=True):
            dc1, dc2 = st.columns([10, 1])
            with dc1:
                recursive_label = "📁" if d.get("recursive", True) else "📄"
                st.markdown(f"{recursive_label} `{d['path']}`")
            with dc2:
                if st.button("🗑", key=f"rmdir_{d['path']}", help="移除此目录"):
                    try:
                        requests.post(
                            f"{API_BASE}/settings",
                            json={
                                "section": "directories",
                                "data": {"action": "remove", "path": d["path"]},
                            },
                            timeout=10,
                        )
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"移除失败: {e}")
else:
    st.info("尚无目录。在下方添加第一个目录。")

# ── Add directory form ──
st.markdown("#### ➕ 添加监控目录")

# Session init for directory form
for k, v in [("dir_path", ""), ("dir_recursive", True)]:
    if k not in st.session_state:
        st.session_state[k] = v

def _browse_dir():
    result = subprocess.run(
        ["osascript", "-e",
         'POSIX path of (choose folder with prompt "选择要监控的目录：")'],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0 and result.stdout.strip():
        st.session_state.dir_path = result.stdout.strip()

def _add_dir():
    path = st.session_state.dir_path.strip()
    if not path:
        st.session_state._mgmt_error = "请先选择或输入目录路径。"
        return
    try:
        resp = requests.post(
            f"{API_BASE}/settings",
            json={
                "section": "directories",
                "data": {
                    "action": "add",
                    "path": path,
                    "group": selected_kb,
                    "recursive": st.session_state.dir_recursive,
                    "exclude_patterns": [".git", "__pycache__", ".DS_Store"],
                },
            },
            timeout=10,
        )
        if resp.status_code == 200:
            st.session_state._mgmt_success = f"已添加「{path}」→「{selected_kb}」"
            st.session_state.dir_path = ""
            st.cache_data.clear()
            st.rerun()
        else:
            st.session_state._mgmt_error = f"添加失败 ({resp.status_code})"
    except Exception as e:
        st.session_state._mgmt_error = f"连接失败: {e}"

path_col, btn_col = st.columns([5, 1])
with path_col:
    st.text_input("目录路径", key="dir_path", placeholder="~/Documents/示例目录")
with btn_col:
    st.button("📁…", key="dir_browse_btn", help="系统文件夹选择", on_click=_browse_dir)

rec_col, add_col = st.columns([3, 2])
with rec_col:
    st.checkbox("递归子目录", key="dir_recursive")
with add_col:
    st.button("➕ 添加此目录", key="dir_add_btn", type="primary",
              on_click=_add_dir, use_container_width=True)

# ── Feedback messages ──
if "_mgmt_success" in st.session_state:
    st.success(st.session_state.pop("_mgmt_success"))
    st.rerun()
if "_mgmt_error" in st.session_state:
    st.error(st.session_state.pop("_mgmt_error"))
