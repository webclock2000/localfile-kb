"""知识库管理页面 — 创建、重命名、删除知识库，管理监控目录。

独立页面，不依赖 global_kb（管理的是 KB 本身，而非在某个 KB 内操作）。
从设置页拆分出来，采用卡片网格布局。
"""

from __future__ import annotations

import subprocess
import time
import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── Helpers ──

def _load_kb_data() -> tuple[dict[str, list[dict]], dict, list[str]]:
    """Load KB map + metadata + names from API.

    Returns (kb_map, kb_meta, kb_names).  ``kb_names`` is the authoritative
    list of KB names (server guarantees "默认" is always present).
    """
    try:
        resp = requests.get(f"{API_BASE}/settings", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            dirs = data.get("directories", [])
            kb_meta = data.get("kb_meta", {})
            # Use server-computed kb_names as authoritative list — server
            # always includes "默认" even when it has no kb_meta entry.
            kb_names: list[str] = data.get("kb_names", ["默认"])

            kb_map: dict[str, list[dict]] = {}
            for name in kb_names:
                kb_map.setdefault(name, [])
            for d in dirs:
                g = d.get("group", "默认")
                kb_map.setdefault(g, []).append(d)

            return kb_map, kb_meta, kb_names
    except Exception:
        pass
    return {"默认": []}, {}, ["默认"]

@st.cache_data(ttl=10)
def _load_kb_data_cached():
    """Cached wrapper to avoid repeated API calls within a single rerun."""
    return _load_kb_data()

# ── Session state init ──
for k, v in [
    ("kb_mgmt_mode", "view"),       # view | create | rename | delete_confirm | clear_confirm
    ("kb_mgmt_form", ""),           # form text field
    ("kb_mgmt_target", ""),         # target KB for rename/delete/clear
    ("kb_mgmt_delete_confirm", False),
    ("kb_mgmt_dir_added", None),    # {kb_name, path} — triggers CTA after dir add
    ("kb_mgmt_index_result", None), # {type: "success"/"error", message: str} — index result
    ("kb_mgmt_index_hint", False),  # show "where to index" hint after dismissing CTA
]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── Header ──
st.title("📚 知识库管理")
st.caption("创建、重命名、删除知识库。每个知识库拥有独立的索引和存储。")

# ── Feedback messages (top of page, immediately visible) ──
if "_mgmt_success" in st.session_state:
    st.success(st.session_state.pop("_mgmt_success"))
if "_mgmt_error" in st.session_state:
    st.error(st.session_state.pop("_mgmt_error"))

# ── Load data ──
kb_map, kb_meta, kb_names = _load_kb_data_cached()
kb_names_sorted = sorted(kb_names)

# ═══════════════════════════════════════════════════════════════════
# ACTION AREA — button + operation panels (appear ABOVE the KB list)
# ═══════════════════════════════════════════════════════════════════

st.markdown("### 🏗️ 新建知识库")

if st.button(
    "➕ 新建知识库",
    type="primary",
    help="创建一个新的知识库。知识库是一组监控目录的集合，拥有独立的数据库和索引。",
):
    st.session_state.kb_mgmt_mode = "create"
    st.session_state.kb_mgmt_form = ""
    st.rerun()

# ── Create panel ──
if st.session_state.kb_mgmt_mode == "create":
    with st.container(border=True):
        st.markdown("#### ➕ 新建知识库")
        st.caption("知识库是一组目录的集合，拥有独立的 SQLite 数据库和 FAISS 索引。")

        cc1, cc2, cc3 = st.columns([3, 1, 1])
        with cc1:
            st.text_input("名称", key="kb_mgmt_form", placeholder="例如：遥感课程",
                          label_visibility="collapsed")
        with cc2:
            confirm = st.button("✅ 确认创建", key="btn_create_confirm", use_container_width=True)
        with cc3:
            cancel = st.button("❌ 取消", key="btn_create_cancel", use_container_width=True)

        if confirm:
            name = st.session_state.kb_mgmt_form.strip()
            if not name:
                st.session_state._mgmt_error = "请输入知识库名称。"
                st.rerun()
            else:
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
                        st.session_state.global_kb = name
                        st.session_state._sidebar_stale = True
                        st.session_state.kb_mgmt_mode = "view"
                        st.cache_data.clear()
                        st.rerun()
                    elif resp.status_code == 409:
                        st.session_state._mgmt_error = f"知识库「{name}」已存在。"
                        st.rerun()
                    else:
                        st.session_state._mgmt_error = f"创建失败 ({resp.status_code})"
                        st.rerun()
                except Exception as e:
                    st.session_state._mgmt_error = f"连接失败: {e}"
                    st.rerun()

        if cancel:
            st.session_state.kb_mgmt_mode = "view"
            st.rerun()

# ── Rename panel ──
if st.session_state.kb_mgmt_mode == "rename":
    target = st.session_state.kb_mgmt_target
    with st.container(border=True):
        st.markdown(f"#### ✏️ 重命名「{target}」")

        rc1, rc2, rc3 = st.columns([3, 1, 1])
        with rc1:
            st.text_input("新名称", key="kb_mgmt_form", label_visibility="collapsed")
        with rc2:
            confirm = st.button("✅ 确认", key="btn_rename_confirm", use_container_width=True)
        with rc3:
            cancel = st.button("❌ 取消", key="btn_rename_cancel", use_container_width=True)

        if confirm:
            new_name = st.session_state.kb_mgmt_form.strip()
            if not new_name:
                st.session_state._mgmt_error = "请输入新名称。"
                st.rerun()
            elif new_name == target:
                st.session_state.kb_mgmt_mode = "view"
                st.rerun()
            else:
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
                        st.session_state._sidebar_stale = True
                        # Update global KB selector if current
                        if st.session_state.get("global_kb") == target:
                            st.session_state.global_kb = new_name
                        st.session_state.kb_mgmt_mode = "view"
                        st.session_state.kb_mgmt_target = ""
                        st.cache_data.clear()
                        st.rerun()
                    elif resp.status_code == 409:
                        st.session_state._mgmt_error = f"「{new_name}」已存在。"
                        st.rerun()
                    else:
                        st.session_state._mgmt_error = f"重命名失败 ({resp.status_code})"
                        st.rerun()
                except Exception as e:
                    st.session_state._mgmt_error = f"连接失败: {e}"
                    st.rerun()

        if cancel:
            st.session_state.kb_mgmt_mode = "view"
            st.rerun()

# ── Delete confirm panel ──
if st.session_state.kb_mgmt_mode == "delete_confirm":
    target = st.session_state.kb_mgmt_target
    with st.container(border=True):
        st.warning(f"⚠️ 确认删除知识库「{target}」及其所有目录配置？此操作**不可逆**。")

        dc1, dc2 = st.columns([3, 1])
        with dc1:
            st.checkbox("我确认要删除此知识库及其所有目录", key="kb_mgmt_delete_confirm")
        with dc2:
            confirm = st.button(
                "🗑 确认删除",
                key="btn_delete_do",
                help="⚠️ 确认永久删除此知识库。此操作不可恢复！",
                disabled=not st.session_state.kb_mgmt_delete_confirm,
                use_container_width=True, type="primary",
            )
        cancel = st.button("❌ 取消", key="btn_delete_cancel")

        if confirm:
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
                    st.session_state._sidebar_stale = True
                    if st.session_state.get("global_kb") == target:
                        st.session_state.global_kb = "默认"
                    st.session_state.kb_mgmt_mode = "view"
                    st.session_state.kb_mgmt_target = ""
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.session_state._mgmt_error = f"删除失败 ({resp.status_code}): {resp.text}"
                    st.rerun()
            except Exception as e:
                st.session_state._mgmt_error = f"连接失败: {e}"
                st.rerun()

        if cancel:
            st.session_state.kb_mgmt_mode = "view"
            st.rerun()

# ── Clear confirm panel ──
if st.session_state.kb_mgmt_mode == "clear_confirm":
    target = st.session_state.kb_mgmt_target
    with st.container(border=True):
        st.warning(f"⚠️ 确认清空知识库「{target}」的全部数据？\n\n"
                   "此操作将删除该知识库中的**所有文件记录、事实、实体、向量和索引**。"
                   "知识库本身及其目录配置将保留。清空后需重新索引才能恢复数据。")

        cc1, cc2 = st.columns([3, 1])
        with cc1:
            st.checkbox("我确认要清空此知识库的全部数据", key="kb_mgmt_clear_confirm")
        with cc2:
            confirm = st.button(
                "🧹 确认清空",
                key="btn_clear_do",
                help="⚠️ 确认清空此知识库的全部索引数据。目录配置将保留。",
                disabled=not st.session_state.get("kb_mgmt_clear_confirm", False),
                use_container_width=True, type="primary",
            )
        cancel = st.button("❌ 取消", key="btn_clear_cancel")

        if confirm:
            try:
                resp = requests.post(
                    f"{API_BASE}/settings",
                    json={
                        "section": "knowledge_bases",
                        "data": {"action": "clear", "name": target},
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    st.session_state._mgmt_success = (
                        f"知识库「{target}」已清空。"
                        f"（{data.get('rows_deleted', 0)} 行数据，"
                        f"{data.get('tables_cleared', 0)} 张表）"
                    )
                    st.session_state._sidebar_stale = True
                    st.session_state.kb_mgmt_mode = "view"
                    st.session_state.kb_mgmt_target = ""
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.session_state._mgmt_error = f"清空失败 ({resp.status_code}): {resp.text}"
                    st.rerun()
            except Exception as e:
                st.session_state._mgmt_error = f"连接失败: {e}"
                st.rerun()

        if cancel:
            st.session_state.kb_mgmt_mode = "view"
            st.rerun()

# ═══════════════════════════════════════════════════════════════════
# KB LIST — filter + card grid (separate from the action area above)
# ═══════════════════════════════════════════════════════════════════

st.divider()
st.markdown("### 📋 知识库列表")

filter_col, _ = st.columns([1, 3])
with filter_col:
    search = st.text_input(
        "🔍 筛选",
        placeholder="输入名称筛选...",
        label_visibility="visible",
    )

# ── KB Card Grid ──
filtered_names = [n for n in kb_names_sorted if search.lower() in n.lower()] if search else kb_names_sorted

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
                    if st.button(
                        "✏️ 重命名",
                        key=f"rn_{name}",
                        use_container_width=True,
                        help="修改该知识库的显示名称",
                    ):
                        st.session_state.kb_mgmt_mode = "rename"
                        st.session_state.kb_mgmt_target = name
                        st.session_state.kb_mgmt_form = name
                        st.rerun()
                with bc2:
                    if name != "默认":
                        if st.button(
                            "🗑 删除",
                            key=f"del_{name}",
                            use_container_width=True,
                            help=f"永久删除知识库「{name}」及其所有目录配置。"
                            "数据文件不会被删除，但索引将丢失。此操作不可恢复！",
                        ):
                            st.session_state.kb_mgmt_mode = "delete_confirm"
                            st.session_state.kb_mgmt_target = name
                            st.session_state.kb_mgmt_delete_confirm = False
                            st.rerun()
                    else:
                        if st.button(
                            "🧹 清空数据",
                            key=f"clear_{name}",
                            use_container_width=True,
                            help="清空默认知识库的全部索引数据（文件记录、事实、实体、"
                            "向量）。知识库本身和目录配置将保留。清空后可重新索引。",
                        ):
                            st.session_state.kb_mgmt_mode = "clear_confirm"
                            st.session_state.kb_mgmt_target = name
                            st.session_state.kb_mgmt_clear_confirm = False
                            st.rerun()

# ── Directory management for selected KB ──
st.divider()
st.markdown("### 📂 目录管理")

# KB selector for directory management
dir_kb_names = kb_names_sorted
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

# Directory list (deduplicated by path — API may return duplicates)
raw_dirs = [d for d in kb_map.get(selected_kb, []) if d.get("path")]
seen: set[str] = set()
real_dirs: list[dict] = []
for d in raw_dirs:
    if d["path"] not in seen:
        seen.add(d["path"])
        real_dirs.append(d)
if real_dirs:
    for i, d in enumerate(real_dirs):
        with st.container(border=True):
            dc1, dc2 = st.columns([10, 1])
            with dc1:
                recursive_label = "📁" if d.get("recursive", True) else "📄"
                st.markdown(f"{recursive_label} `{d['path']}`")
            with dc2:
                if st.button(
                    "🗑",
                    key=f"rmdir_{i}_{d['path']}",
                    help="从该知识库的监控列表中移除此目录。目录中的文件不会被删除。",
                ):
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
            st.session_state.kb_mgmt_dir_added = {"kb_name": selected_kb, "path": path}
            st.session_state.kb_mgmt_index_hint = False  # new CTA supersedes old hint
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
    st.button(
        "📁…",
        key="dir_browse_btn",
        help="打开 macOS 系统文件夹选择器，选择要监控的目录",
        on_click=_browse_dir,
    )

rec_col, add_col = st.columns([3, 2])
with rec_col:
    st.checkbox("递归子目录", key="dir_recursive")
with add_col:
    st.button(
        "➕ 添加此目录",
        key="dir_add_btn",
        type="primary",
        on_click=_add_dir,
        use_container_width=True,
        help="将该目录加入知识库的监控列表。添加后需在文件管理页手动触发索引。",
    )

# ── Indexing CTA — shown after a directory is added ──
cta = st.session_state.get("kb_mgmt_dir_added")
if cta:
    cta_kb = cta["kb_name"]
    cta_path = cta["path"]
    with st.container(border=True):
        st.info(f"✅ 已为「**{cta_kb}**」添加目录 `{cta_path}`\n\n该目录尚未索引。要立即索引吗？")
        cta_col1, cta_col2, cta_col3 = st.columns([2, 1, 3])
        with cta_col1:
            if st.button(
                "🚀 立即索引",
                key="cta_index_now",
                type="primary",
                use_container_width=True,
                help=f"立即启动知识库「{cta_kb}」的文件扫描和索引。"
                "系统将扫描所有监控目录，解析新文件和变更文件，"
                "通过 LLM 提取实体和关系。索引可能需要较长时间。",
            ):
                with st.spinner(f"正在索引「{cta_kb}」..."):
                    try:
                        resp = requests.post(
                            f"{API_BASE}/index",
                            json={"kb": cta_kb},
                            timeout=600,
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            st.session_state.kb_mgmt_index_result = {
                                "type": "success",
                                "message": (
                                    f"索引「{cta_kb}」完成 — "
                                    f"处理 {data.get('files_processed', 0)} 个文件，"
                                    f"新增 {data.get('facts_added', 0)} 条事实"
                                    f"（跳过 {data.get('files_skipped', 0)} 个不支持的文件）"
                                    if data.get("files_processed", 0) > 0 or data.get("files_skipped", 0) > 0
                                    else f"索引「{cta_kb}」完成 — {data.get('message', '没有检测到文件变更')}"
                                ),
                            }
                        else:
                            st.session_state.kb_mgmt_index_result = {
                                "type": "error",
                                "message": f"索引启动失败 ({resp.status_code})",
                            }
                    except requests.exceptions.Timeout:
                        st.session_state.kb_mgmt_index_result = {
                            "type": "warning",
                            "message": "索引任务已提交（等待响应超时）。索引在后台继续运行。前往「📁 文件管理」或「📊 系统状态」查看进度。",
                        }
                    except Exception as e:
                        st.session_state.kb_mgmt_index_result = {
                            "type": "error",
                            "message": f"索引失败: {e}",
                        }
                st.session_state.kb_mgmt_dir_added = None
                st.cache_data.clear()
                st.rerun()
        with cta_col2:
            if st.button(
                "稍后再说",
                key="cta_later",
                use_container_width=True,
                help="关闭此提示。你可以随时在「📁 文件管理」页面手动触发索引。",
            ):
                st.session_state.kb_mgmt_dir_added = None
                st.session_state.kb_mgmt_index_hint = True
                st.rerun()

# ── Index hint — shown after user dismisses the CTA ──
if st.session_state.get("kb_mgmt_index_hint"):
    st.info(
        "💡 **目录已添加但尚未索引。** "
        "完成所有目录配置后，前往「📁 **文件管理**」页面，"
        "在上方下拉框中选择该知识库，点击「🔄 **重建索引**」按钮即可统一索引。"
    )
    if st.button("👍 知道了", key="dismiss_index_hint"):
        st.session_state.kb_mgmt_index_hint = False
        st.rerun()

# ── Index result display (persists across reruns) ──
index_result = st.session_state.get("kb_mgmt_index_result")
if index_result:
    msg_type = index_result["type"]
    if msg_type == "success":
        st.success(index_result["message"])
    elif msg_type == "warning":
        st.warning(index_result["message"])
    else:
        st.error(index_result["message"])
    st.caption("💡 前往「📁 文件管理」查看文件列表和索引进度，或「📊 系统状态」查看运行记录。")
    if st.button("👍 知道了", key="dismiss_index_result"):
        st.session_state.kb_mgmt_index_result = None
        st.rerun()
