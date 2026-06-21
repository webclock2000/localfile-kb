"""实体审核页面 — 合并审核 + 可疑实体标记。

KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import json

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("🔍 实体审核")
st.caption(f"审查「{kb}」中的实体合并提案和可疑实体。")

# ============================================================================
# Tabs
# ============================================================================

tab_merge, tab_suspect = st.tabs(["🔗 合并审核", "⚠️ 可疑实体"])

# ============================================================================
# Tab 1: Merge proposals (existing functionality)
# ============================================================================

with tab_merge:
    try:
        resp = requests.get(
            f"{API_BASE}/entities/proposals",
            params={"status": "proposed", "proposal_type": "merge", "kb": kb},
            timeout=10,
        )
        if resp.status_code == 200:
            proposals = resp.json().get("proposals", [])

            if not proposals:
                st.success("没有待审核的合并提案，所有实体已消歧。")
            else:
                st.info(f"共 {len(proposals)} 条合并提案待审核")

                for prop in proposals:
                    with st.container(border=True):
                        pc1, pc2 = st.columns([3, 1])
                        with pc1:
                            st.markdown(
                                f"**{prop['entity_a']}** ↔ **{prop['entity_b']}** → "
                                f"`{prop.get('proposed_name', '?')}` "
                                f"({prop.get('confidence', 0):.0%} 置信度)"
                            )
                        with pc2:
                            bc1, bc2 = st.columns(2)
                            with bc1:
                                if st.button(
                                    "✓ 批准",
                                    key=f"app_{prop['id']}",
                                    use_container_width=True,
                                    help="确认这两个实体是同一事物，执行合并。",
                                ):
                                    try:
                                        requests.post(
                                            f"{API_BASE}/entities/merge",
                                            json={
                                                "proposal_id": prop["id"],
                                                "canonical_name": prop.get("proposed_name"),
                                                "kb": kb,
                                            },
                                            timeout=10,
                                        )
                                        st.success("合并成功！")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(str(e))
                            with bc2:
                                if st.button(
                                    "✗ 拒绝",
                                    key=f"rej_{prop['id']}",
                                    use_container_width=True,
                                ):
                                    try:
                                        requests.post(
                                            f"{API_BASE}/entities/reject",
                                            json={"proposal_id": prop["id"], "kb": kb},
                                            timeout=10,
                                        )
                                        st.info("已拒绝。")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(str(e))
        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端服务。请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")

# ============================================================================
# Tab 2: Suspect entities (new)
# ============================================================================

with tab_suspect:
    st.subheader("⚠️ 可疑实体检测")
    st.caption(
        "自动检测疑似 OCR 错误、乱码、无意义实体。"
        "可对每个可疑实体进行重命名、删除或确认有效。"
        "已处理的实体可在「修改历史」中查看和撤销。"
    )

    # ── Scan button ──
    col_scan, col_info = st.columns([1, 3])
    with col_scan:
        if st.button("🔎 重新扫描", key="suspect_scan", help="运行实体质量检测规则"):
            try:
                scan_resp = requests.post(
                    f"{API_BASE}/entities/suspects/scan",
                    json={"kb": kb},
                    timeout=30,
                )
                if scan_resp.status_code == 200:
                    data = scan_resp.json()
                    st.success(
                        f"扫描完成：{data['entities_scanned']} 个实体，"
                        f"发现 {data['suspects_found']} 个可疑实体"
                    )
                    st.rerun()
                else:
                    st.error(f"扫描失败: {scan_resp.status_code}")
            except Exception as e:
                st.error(f"扫描出错: {e}")

    # ── Load suspect proposals ──
    try:
        resp = requests.get(
            f"{API_BASE}/entities/proposals",
            params={"status": "proposed", "proposal_type": "suspect", "kb": kb},
            timeout=10,
        )
        if resp.status_code == 200:
            suspects = resp.json().get("proposals", [])

            if not suspects:
                st.success("没有可疑实体，实体质量良好。")
            else:
                st.info(f"共 {len(suspects)} 个可疑实体待审核")

                for prop in suspects:
                    # Parse llm_response for detailed info
                    detail: dict = {}
                    if prop.get("llm_response"):
                        try:
                            detail = json.loads(prop["llm_response"])
                        except (json.JSONDecodeError, TypeError):
                            pass

                    reason = detail.get("reason", prop.get("entity_a", ""))
                    flags = detail.get("flags", [])
                    gib_score = detail.get("gibberish_score", 0)

                    # Flag badges
                    flag_labels = {
                        "too_short": "过短",
                        "too_long": "过长",
                        "gibberish": "疑似乱码",
                        "not_a_word": "不成词",
                        "isolated": "孤立节点",
                        "ocr_only": "仅OCR来源",
                    }

                    with st.container(border=True):
                        # Entity name + flag badges
                        flag_badges = " ".join(
                            f"`{flag_labels.get(f, f)}`" for f in flags
                        )
                        st.markdown(
                            f"### `{prop['entity_a']}`  "
                            f"<small>{flag_badges}</small>",
                            unsafe_allow_html=True,
                        )

                        # Reason
                        st.caption(f"**原因**：{reason}  |  乱码得分：{gib_score:.2f}")

                        # Action buttons
                        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
                        with c1:
                            if st.button("✏️ 重命名", key=f"ren_{prop['id']}"):
                                st.session_state[f"renaming_{prop['id']}"] = True
                        with c2:
                            if st.button(
                                "🗑️ 删除",
                                key=f"del_{prop['id']}",
                                type="secondary",
                            ):
                                st.session_state[f"deleting_{prop['id']}"] = True
                        with c3:
                            if st.button("✓ 确认为实体", key=f"ign_{prop['id']}"):
                                try:
                                    requests.post(
                                        f"{API_BASE}/entities/suspects/{prop['id']}/ignore",
                                        params={"kb": kb},
                                        timeout=10,
                                    )
                                    st.success("已确认为有效实体，不再审核。")
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))

                        # ── Inline rename form ──
                        if st.session_state.get(f"renaming_{prop['id']}"):
                            with st.form(key=f"rename_form_{prop['id']}"):
                                new_name = st.text_input(
                                    "新名称",
                                    value=prop["entity_a"],
                                    key=f"new_name_{prop['id']}",
                                )
                                rc1, rc2 = st.columns(2)
                                with rc1:
                                    if st.form_submit_button("✓ 确认重命名"):
                                        if new_name.strip() and new_name != prop["entity_a"]:
                                            try:
                                                requests.post(
                                                    f"{API_BASE}/entities/rename",
                                                    json={
                                                        "entity_name": prop["entity_a"],
                                                        "new_name": new_name.strip(),
                                                        "proposal_id": prop["id"],
                                                        "kb": kb,
                                                    },
                                                    timeout=10,
                                                )
                                                st.success("已重命名。")
                                                del st.session_state[f"renaming_{prop['id']}"]
                                                st.rerun()
                                            except Exception as e:
                                                st.error(str(e))
                                with rc2:
                                    if st.form_submit_button("取消"):
                                        del st.session_state[f"renaming_{prop['id']}"]
                                        st.rerun()

                        # ── Inline delete confirmation ──
                        if st.session_state.get(f"deleting_{prop['id']}"):
                            with st.container(border=True):
                                st.warning(
                                    f"确认删除实体 `{prop['entity_a']}` "
                                    f"及其所有关联事实？此操作不可撤销。"
                                )
                                dc1, dc2 = st.columns(2)
                                with dc1:
                                    if st.button(
                                        "✓ 确认删除",
                                        key=f"confirm_del_{prop['id']}",
                                        type="primary",
                                    ):
                                        try:
                                            requests.post(
                                                f"{API_BASE}/entities/delete",
                                                json={
                                                    "entity_name": prop["entity_a"],
                                                    "proposal_id": prop["id"],
                                                    "kb": kb,
                                                },
                                                timeout=10,
                                            )
                                            st.success("已删除。")
                                            del st.session_state[f"deleting_{prop['id']}"]
                                            st.rerun()
                                        except Exception as e:
                                            st.error(str(e))
                                with dc2:
                                    if st.button(
                                        "取消",
                                        key=f"cancel_del_{prop['id']}",
                                    ):
                                        del st.session_state[f"deleting_{prop['id']}"]
                                        st.rerun()

                    st.divider()

            # ── Modification history ──
            st.divider()
            show_history = st.toggle(
                "📋 显示已处理",
                key="show_suspect_history",
                help="显示已重命名、已确认或已删除的可疑实体记录，可以撤销操作。",
            )

            if show_history:
                try:
                    hist_resp = requests.get(
                        f"{API_BASE}/entities/proposals",
                        params={"status": "approved", "proposal_type": "suspect", "kb": kb},
                        timeout=10,
                    )
                    rej_resp = requests.get(
                        f"{API_BASE}/entities/proposals",
                        params={"status": "rejected", "proposal_type": "suspect", "kb": kb},
                        timeout=10,
                    )
                    del_resp = requests.get(
                        f"{API_BASE}/entities/proposals",
                        params={"status": "deleted", "proposal_type": "suspect", "kb": kb},
                        timeout=10,
                    )
                    all_history: list[dict] = []
                    if hist_resp.status_code == 200:
                        all_history.extend(hist_resp.json().get("proposals", []))
                    if rej_resp.status_code == 200:
                        all_history.extend(rej_resp.json().get("proposals", []))
                    if del_resp.status_code == 200:
                        all_history.extend(del_resp.json().get("proposals", []))

                    if not all_history:
                        st.caption("暂无已处理的记录。")
                    else:
                        all_history.sort(
                            key=lambda h: h.get("created_at", ""), reverse=True
                        )
                        st.caption(f"共 {len(all_history)} 条已处理记录")

                        status_labels = {
                            "approved": ("✅ 已重命名", "green"),
                            "deleted": ("🗑️ 已删除", "red"),
                            "rejected": ("✓ 已确认", "gray"),
                        }

                        for h in all_history:
                            detail: dict = {}
                            if h.get("llm_response"):
                                try:
                                    detail = json.loads(h["llm_response"])
                                except (json.JSONDecodeError, TypeError):
                                    pass

                            h_reason = detail.get("reason", "")

                            # Determine actual status: if approved but entity has
                            # no facts (deleted), show as "deleted".  Older records
                            # all used "approved" for both rename and delete.
                            h_status = h["status"]
                            if h_status == "approved":
                                try:
                                    f_resp = requests.get(
                                        f"{API_BASE}/facts",
                                        params={"entity": h["entity_a"], "limit": 1, "kb": kb},
                                        timeout=5,
                                    )
                                    if f_resp.status_code == 200:
                                        f_count = len(f_resp.json().get("facts", []))
                                        if f_count == 0:
                                            h_status = "deleted"
                                except Exception:
                                    pass

                            h_status_label, h_color = status_labels.get(
                                h_status, (h_status, "gray")
                            )

                            with st.container(border=True):
                                hc1, hc2 = st.columns([4, 1])
                                with hc1:
                                    st.markdown(
                                        f"**`{h['entity_a']}`** → "
                                        f"<span style='color:{h_color}'>{h_status_label}</span>",
                                        unsafe_allow_html=True,
                                    )
                                    if h_reason:
                                        st.caption(h_reason)
                                with hc2:
                                    if st.button(
                                        "↩ 撤销",
                                        key=f"revert_{h['id']}",
                                        use_container_width=True,
                                        help="将此实体恢复到待审核列表",
                                    ):
                                        try:
                                            requests.post(
                                                f"{API_BASE}/entities/suspects/{h['id']}/revert",
                                                params={"kb": kb},
                                                timeout=10,
                                            )
                                            st.success("已撤销，实体回到待审核列表。")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(str(e))
                except requests.exceptions.ConnectionError:
                    st.error("无法连接到 FileKB 后端服务。")
                except Exception as e:
                    st.error(f"出错了: {e}")

        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端服务。")
    except Exception as e:
        st.error(f"出错了: {e}")
