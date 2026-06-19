"""文件管理页面 — 文件列表、统计、索引控制。

KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import os
import time

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


def _status_icon(status: str) -> str:
    """绿色对勾 / 红色叉"""
    return "✅" if status == "done" else "❌"


def _status_tip(status: str) -> str:
    tips = {
        "done": "✅ 已索引：该文件已成功完成实体和关系的提取。",
        "failed": "❌ 未索引：处理过程中出错。点击文件名旁的 🔄 按钮可单独重试。",
        "skipped": "❌ 未索引：文件被跳过（类型不支持或内容为空）。点击文件名旁的 🔄 按钮可单独重试。",
        "pending": "❌ 未索引：该文件尚未被处理。",
        "processing": "❌ 未索引：该文件正在处理中。",
    }
    return tips.get(status, "")


def _format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _format_time(ts: str | None) -> str:
    if not ts:
        return "—"
    return ts.replace("T", " ")[:19]


kb: str = st.session_state.get("global_kb", "默认")

st.title("📁 文件管理")
st.caption(f"「{kb}」的文件索引状态与操作。")

try:
    resp = requests.get(f"{API_BASE}/status?kb={kb}", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        health = data.get("health", {})

        # ── Stats cards ──
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            st.metric("文件总数", data.get("files_total", 0))
        with sc2:
            st.metric("事实总数", data.get("facts_total", 0))
        with sc3:
            st.metric("实体数", health.get("graph", {}).get("nodes", 0))
        with sc4:
            st.metric("向量数", health.get("faiss", {}).get("vectors", 0))

        # ── Last run (simplified, no ❓) ──
        last = data.get("last_run")
        if last and last.get("status") == "completed":
            st.caption(
                f"上次索引完成：处理 {last.get('files_changed', 0)} 个文件，"
                f"新增 {last.get('facts_added', 0)} 条事实"
            )
        elif last and last.get("status") == "running":
            st.caption("🔄 索引正在运行中…")
        elif last and last.get("status") == "crashed":
            st.caption(f"上次索引异常退出，处理了 {last.get('files_changed', 0)} 个文件")

        # ── Actions ──
        if st.button(
            f"🔄 索引「{kb}」知识库",
            use_container_width=True,
            type="primary",
            help=f"扫描「{kb}」的所有监控目录，检测文件变更（新增/修改/删除），"
            "解析文件内容并通过 LLM 提取实体和关系，更新向量索引和知识图谱。"
            "已索引且未变化的文件会被跳过。索引大量文件可能需要较长时间。",
        ):
            with st.spinner(f"正在索引「{kb}」..."):
                try:
                    resp2 = requests.post(
                        f"{API_BASE}/index", json={"kb": kb}, timeout=600
                    )
                    if resp2.status_code == 200:
                        st.success(f"「{kb}」索引完成")
                        st.rerun()
                    else:
                        st.error(f"启动失败: {resp2.status_code}")
                except requests.exceptions.Timeout:
                    st.warning("索引任务已提交（等待超时），索引在后台继续运行。")
                except Exception as e:
                    st.error(f"错误: {e}")

        # ═══════════════════════════════════════════════════════════
        # File list
        # ═══════════════════════════════════════════════════════════
        st.subheader("文件列表")

        # ── Filters ──
        fc1, fc2 = st.columns([2, 1])
        with fc1:
            search_query = st.text_input(
                "搜索文件",
                placeholder="输入路径关键词过滤...",
                label_visibility="collapsed",
                key="file_search",
            )
        with fc2:
            status_filter = st.selectbox(
                "状态筛选",
                options=["全部", "✅ 已索引", "❌ 失败", "⏭️ 跳过", "⏳ 待处理", "🔄 处理中"],
                index=0,
                label_visibility="collapsed",
                key="file_status",
            )

        status_map = {
            "全部": None,
            "✅ 已索引": "done",
            "❌ 失败": "failed",
            "⏭️ 跳过": "skipped",
            "⏳ 待处理": "pending",
            "🔄 处理中": "processing",
        }
        api_status = status_map[status_filter]
        api_search = search_query.strip() if search_query.strip() else None

        # Pagination state
        if "file_page" not in st.session_state:
            st.session_state.file_page = 0
        if "file_kb" not in st.session_state:
            st.session_state.file_kb = kb
        if st.session_state.file_kb != kb:
            st.session_state.file_page = 0
            st.session_state.file_kb = kb

        page_size = 30
        offset_val = st.session_state.file_page * page_size

        try:
            params: dict = {"kb": kb, "limit": page_size, "offset": offset_val}
            if api_status:
                params["status"] = api_status
            if api_search:
                params["search"] = api_search

            files_resp = requests.get(f"{API_BASE}/files", params=params, timeout=10)
            if files_resp.status_code == 200:
                files_data = files_resp.json()
                files = files_data.get("files", [])
                total = files_data.get("total", 0)

                if not files:
                    st.info(
                        "没有匹配的文件。"
                        if data.get("files_total", 0) > 0
                        else "尚未索引任何文件。添加监控目录后点击「🔄 索引当前知识库」。"
                    )
                else:
                    st.caption(
                        f"共 {total} 个文件"
                        f"（第 {offset_val + 1}–{offset_val + len(files)} 个）"
                    )

                    for f in files:
                        file_name = os.path.basename(f["path"])
                        file_dir = os.path.dirname(f["path"])
                        is_indexed = f["status"] == "done"
                        detail_key = f"detail_{f['id']}"

                        with st.container(border=True):
                            # Row 1: ✅/❌  ☐  文件名  [🔄]
                            c1, c2, c3, c4 = st.columns([0.4, 0.5, 9, 2])

                            with c1:
                                icon = _status_icon(f["status"])
                                tip = _status_tip(f["status"])
                                st.markdown(
                                    f"<span title='{tip}' style='font-size:1.2em;"
                                    f"cursor:default'>{icon}</span>",
                                    unsafe_allow_html=True,
                                )

                            with c2:
                                if detail_key not in st.session_state:
                                    st.session_state[detail_key] = False
                                st.checkbox(
                                    "",
                                    key=detail_key,
                                    label_visibility="collapsed",
                                    help="勾选以展开该文件提取的实体-关系事实详情。取消勾选则收起。",
                                )

                            with c3:
                                st.markdown(f"**{file_name}**")
                                st.caption(file_dir)

                            with c4:
                                if st.button(
                                    "🔄 重索引",
                                    key=f"reidx_{f['id']}",
                                    help=f"单独重新索引「{file_name}」：重新解析并通过 "
                                    "LLM 提取实体和关系。无论文件当前状态如何都会重新处理。",
                                ):
                                        with st.spinner(f"正在重索引 {file_name}..."):
                                            try:
                                                re_resp = requests.post(
                                                    f"{API_BASE}/files/{f['id']}/reindex",
                                                    params={"kb": kb},
                                                    timeout=300,
                                                )
                                                if re_resp.status_code == 200:
                                                    rd = re_resp.json()
                                                    st.success(
                                                        f"✅ {file_name} 重索引完成 — "
                                                        f"{rd.get('facts_added', 0)} 条事实"
                                                    )
                                                    time.sleep(1)
                                                    st.rerun()
                                                else:
                                                    st.error(f"重索引失败 ({re_resp.status_code})")
                                            except requests.exceptions.Timeout:
                                                st.warning("重索引请求已提交，正在后台处理…")
                                            except Exception as e:
                                                st.error(f"错误: {e}")

                            # Row 2: metadata
                            m1, m2, m3, m4, m5 = st.columns([1.2, 1.0, 1.0, 1.5, 5.3])
                            with m1:
                                st.caption(f"📊 {f.get('fact_count', 0)} 事实")
                            with m2:
                                st.caption(f"📦 {f.get('chunk_count', 0)} 块")
                            with m3:
                                st.caption(_format_size(f.get("file_size")))
                            with m4:
                                if f.get("indexed_at"):
                                    st.caption(f"🕐 {_format_time(f.get('indexed_at'))}")
                            with m5:
                                if f.get("error_msg"):
                                    st.caption(f"⚠️ {f['error_msg'][:100]}")

                            # Expanded: show facts when checkbox is checked
                            if st.session_state.get(detail_key, False):
                                with st.spinner("加载事实..."):
                                    try:
                                        facts_resp = requests.get(
                                            f"{API_BASE}/files/{f['id']}/facts",
                                            params={"kb": kb},
                                            timeout=10,
                                        )
                                        if facts_resp.status_code == 200:
                                            facts_list = facts_resp.json().get("facts", [])
                                            if facts_list:
                                                for fact in facts_list:
                                                    st.markdown(
                                                        f"**{fact['subject']}** "
                                                        f"*{fact['predicate']}* "
                                                        f"**{fact['object']}** "
                                                        f"（置信度: {fact['confidence']}）"
                                                    )
                                            else:
                                                st.caption("该文件未提取到事实。")
                                        else:
                                            st.error(f"加载失败 ({facts_resp.status_code})")
                                    except Exception as e:
                                        st.error(f"加载失败: {e}")

                    # ── Pagination ──
                    total_pages = max(1, (total + page_size - 1) // page_size)
                    if total_pages > 1:
                        pc1, pc2, pc3, pc4, pc5 = st.columns([1, 1, 2, 1, 1])
                        with pc1:
                            if st.button("⏮ 首页", disabled=st.session_state.file_page == 0):
                                st.session_state.file_page = 0
                                st.rerun()
                        with pc2:
                            if st.button("◀ 上一页", disabled=st.session_state.file_page == 0):
                                st.session_state.file_page -= 1
                                st.rerun()
                        with pc3:
                            st.caption(
                                f"第 {st.session_state.file_page + 1} / {total_pages} 页"
                            )
                        with pc4:
                            if st.button(
                                "下一页 ▶",
                                disabled=st.session_state.file_page >= total_pages - 1,
                            ):
                                st.session_state.file_page += 1
                                st.rerun()
                        with pc5:
                            if st.button(
                                "末页 ⏭",
                                disabled=st.session_state.file_page >= total_pages - 1,
                            ):
                                st.session_state.file_page = total_pages - 1
                                st.rerun()
            else:
                st.error(f"加载文件列表失败 ({files_resp.status_code})")
        except requests.exceptions.ConnectionError:
            st.error("无法连接到 FileKB 后端。")
        except Exception as e:
            st.error(f"出错了: {e}")

    else:
        st.warning(f"API 状态检查失败: {resp.status_code}")
except requests.exceptions.ConnectionError:
    st.error("无法连接到 FileKB 后端服务。请先启动服务。")
except Exception as e:
    st.error(f"出错了: {e}")
