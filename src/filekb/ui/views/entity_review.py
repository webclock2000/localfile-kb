"""实体审核页面 — 合并审核 + 可疑实体标记 + 实体管理.

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
st.caption(f"审查「{kb}」中的实体合并提案、可疑实体和实体库。")

# ============================================================================
# Tabs
# ============================================================================

tab_merge, tab_suspect, tab_manage = st.tabs(["🔗 合并审核", "⚠️ 可疑实体", "📋 实体管理"])

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
# Tab 2: Suspect entities
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
                        "image_source": "图片来源",
                        "numeric_dominant": "数值/金额",
                        "looks_like_code": "疑似代码",
                        "looks_like_date": "疑似日期",
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
                            # no facts (deleted), show as "deleted".
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

# ============================================================================
# Tab 3: Entity Management (NEW)
# ============================================================================

with tab_manage:
    st.subheader("📋 实体管理")
    st.caption(
        "浏览、搜索和编辑所有实体。支持单个或批量重命名、删除，操作即时生效。"
    )

    # ── Controls bar ──
    if "entity_page" not in st.session_state:
        st.session_state["entity_page"] = 1
    ctl1, ctl2, ctl3, ctl4 = st.columns([3, 1, 1, 1])
    with ctl1:
        search = st.text_input(
            "🔍 搜索实体",
            key="entity_search",
            placeholder="输入实体名称过滤...",
            label_visibility="collapsed",
        )
    with ctl2:
        sort_by = st.selectbox(
            "排序",
            options=["count", "name"],
            format_func=lambda x: {"count": "按出现次数", "name": "按名称"}[x],
            label_visibility="collapsed",
            help="实体排序方式：按出现次数或按名称",
            key="entity_sort",
        )
    with ctl3:
        filter_type = st.selectbox(
            "过滤",
            options=["all", "suspect", "merge"],
            format_func=lambda x: {"all": "全部", "suspect": "仅可疑", "merge": "有合并提案"}[x],
            label_visibility="collapsed",
            help="筛选实体：全部、仅可疑实体、有合并提案的实体",
            key="entity_filter",
        )
    with ctl4:
        page_size = st.selectbox(
            "每页",
            options=[50, 100, 200],
            index=0,
            format_func=lambda x: f"每页 {x} 条",
            label_visibility="collapsed",
            help="每页显示的实体数量",
            key="entity_page_size",
        )

    # Reset page to 1 when filters change
    _filter_key = (search, sort_by, filter_type, page_size)
    _prev_filter = st.session_state.get("_entity_filter_key", None)
    if _prev_filter is not None and _filter_key != _prev_filter:
        st.session_state["entity_page"] = 1
    st.session_state["_entity_filter_key"] = _filter_key

    # ── Load entities ──
    try:
        suspect_only = filter_type == "suspect"
        merge_only = filter_type == "merge"

        resp = requests.get(
            f"{API_BASE}/entities/list",
            params={
                "kb": kb,
                "search": search or None,
                "sort_by": sort_by,
                "order": "desc",
                "page": st.session_state.get("entity_page", 1),
                "page_size": page_size,
                "suspect_only": str(suspect_only).lower(),
                "merge_proposal_only": str(merge_only).lower(),
            },
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            entities = data.get("entities", [])
            total = data.get("total", 0)

            st.markdown(f"共 **{total}** 个实体，显示前 {len(entities)} 个")

            if not entities:
                st.info("没有匹配的实体。")
            else:
                # Track selected entities for batch operations
                selected: set[str] = set()
                if "entity_selected" in st.session_state:
                    selected = set(st.session_state["entity_selected"])

                # ── Entity table header ──
                h_sel, h_name, h_count, h_status, h_actions = st.columns(
                    [0.5, 3, 1, 1.5, 1.5]
                )
                with h_sel:
                    st.markdown("**选择**")
                with h_name:
                    st.markdown("**实体名称**")
                with h_count:
                    st.markdown("**出现次数**")
                with h_status:
                    st.markdown("**标签**")
                with h_actions:
                    st.markdown("**操作**")

                st.divider()

                # ── Entity rows ──
                for entity in entities:
                    name = entity["name"]
                    count = entity["fact_count"]
                    has_suspect = entity.get("has_suspect_proposal", False)
                    has_merge = entity.get("has_merge_proposal", False)

                    # Status badges
                    badges: list[str] = []
                    if has_suspect:
                        badges.append("⚠️可疑")
                    if has_merge:
                        badges.append("🔗待合并")

                    row_key = f"entity_row_{name}"

                    r_sel, r_name, r_count, r_status, r_actions = st.columns(
                        [0.5, 3, 1, 1.5, 1.5]
                    )
                    with r_sel:
                        is_selected = st.checkbox(
                            "选择",
                            key=f"sel_{row_key}",
                            label_visibility="collapsed",
                            value=name in selected,
                        )
                        if is_selected and name not in selected:
                            selected.add(name)
                            st.session_state["entity_selected"] = list(selected)
                        elif not is_selected and name in selected:
                            selected.discard(name)
                            st.session_state["entity_selected"] = list(selected)
                    with r_name:
                        st.markdown(f"**{name}**")
                    with r_count:
                        st.markdown(str(count))
                    with r_status:
                        st.markdown(" ".join(badges) if badges else "—")
                    with r_actions:
                        ac1, ac2 = st.columns(2)
                        with ac1:
                            if st.button(
                                "✏️",
                                key=f"em_ren_{row_key}",
                                help=f"重命名 {name}",
                                use_container_width=True,
                            ):
                                st.session_state[f"em_renaming_{row_key}"] = True
                        with ac2:
                            if st.button(
                                "🗑️",
                                key=f"em_del_{row_key}",
                                help=f"删除 {name}",
                                use_container_width=True,
                            ):
                                st.session_state[f"em_deleting_{row_key}"] = True

                    # ── Inline rename for entity management ──
                    if st.session_state.get(f"em_renaming_{row_key}"):
                        with st.container(border=True):
                            new_name = st.text_input(
                                "新名称",
                                value=name,
                                key=f"em_new_name_{row_key}",
                            )
                            rc1, rc2 = st.columns(2)
                            with rc1:
                                if st.button(
                                    "✓ 确认重命名",
                                    key=f"em_confirm_ren_{row_key}",
                                    use_container_width=True,
                                ) and new_name.strip() and new_name.strip() != name:
                                        try:
                                            resp = requests.post(
                                                f"{API_BASE}/entities/rename",
                                                json={
                                                    "entity_name": name,
                                                    "new_name": new_name.strip(),
                                                    "kb": kb,
                                                },
                                                timeout=10,
                                            )
                                            if resp.status_code == 200:
                                                st.success(f"已重命名：{name} → {new_name.strip()}")
                                                del st.session_state[f"em_renaming_{row_key}"]
                                                st.rerun()
                                            else:
                                                st.error(f"重命名失败: {resp.status_code}")
                                        except Exception as e:
                                            st.error(str(e))
                            with rc2:
                                if st.button(
                                    "取消",
                                    key=f"em_cancel_ren_{row_key}",
                                    use_container_width=True,
                                ):
                                    del st.session_state[f"em_renaming_{row_key}"]
                                    st.rerun()

                    # ── Inline delete confirmation for entity management ──
                    if st.session_state.get(f"em_deleting_{row_key}"):
                        with st.container(border=True):
                            st.warning(
                                f"确认删除实体 `{name}` 及其所有 {count} 条关联事实？"
                                f"此操作不可撤销。"
                            )
                            dc1, dc2 = st.columns(2)
                            with dc1:
                                if st.button(
                                    "✓ 确认删除",
                                    key=f"em_confirm_del_{row_key}",
                                    type="primary",
                                    use_container_width=True,
                                ):
                                    try:
                                        resp = requests.post(
                                            f"{API_BASE}/entities/delete",
                                            json={
                                                "entity_name": name,
                                                "kb": kb,
                                            },
                                            timeout=10,
                                        )
                                        if resp.status_code == 200:
                                            st.success(f"已删除：{name}")
                                            del st.session_state[f"em_deleting_{row_key}"]
                                            # Also remove from selection if present
                                            selected.discard(name)
                                            st.session_state["entity_selected"] = list(selected)
                                            st.rerun()
                                        else:
                                            st.error(f"删除失败: {resp.status_code}")
                                    except Exception as e:
                                        st.error(str(e))
                            with dc2:
                                if st.button(
                                    "取消",
                                    key=f"em_cancel_del_{row_key}",
                                    use_container_width=True,
                                ):
                                    del st.session_state[f"em_deleting_{row_key}"]
                                    st.rerun()

                    st.divider()

                # ── Bottom batch actions ──
                if selected:
                    st.info(f"已选 {len(selected)} 个实体")
                    bc1, bc2 = st.columns(2)
                    with bc1:
                        if st.button(
                            f"🗑️ 批量删除已选的 {len(selected)} 个实体",
                            key="batch_delete_selected",
                            type="secondary",
                        ):
                            ops = [
                                {"action": "delete", "entity": e_name}
                                for e_name in selected
                            ]
                            with st.spinner(f"正在删除 {len(selected)} 个实体..."):
                                try:
                                    batch_resp = requests.post(
                                        f"{API_BASE}/entities/batch",
                                        json={
                                            "kb": kb,
                                            "operations": ops,
                                        },
                                        timeout=120,
                                    )
                                    if batch_resp.status_code == 200:
                                        batch_data = batch_resp.json()
                                        ok = sum(
                                            1 for r in batch_data.get("results", [])
                                            if r.get("success")
                                        )
                                        st.success(
                                            f"完成！{ok}/{len(selected)} 个实体已删除。"
                                            f"图已重建（{batch_data.get('graph_nodes', '?')} 节点，"
                                            f"{batch_data.get('graph_edges', '?')} 边）。"
                                        )
                                        st.session_state["entity_selected"] = []
                                        st.rerun()
                                    else:
                                        st.error(f"批量删除失败: {batch_resp.status_code}")
                                except Exception as e:
                                    st.error(f"提交出错: {e}")
                    with bc2:
                        if st.button(
                            "✗ 取消选择",
                            key="clear_selection",
                            use_container_width=True,
                        ):
                            st.session_state["entity_selected"] = []
                            st.rerun()

                # ── Pagination ──
                total_pages = max(1, (total + page_size - 1) // page_size)
                cur_page = st.session_state.get("entity_page", 1)
                if total_pages > 1:
                    pc1, pc2, pc3, pc4 = st.columns([1, 1, 2, 1])
                    with pc1:
                        if st.button("◀ 上一页", key="entity_prev", disabled=cur_page <= 1,
                                     use_container_width=True):
                            st.session_state["entity_page"] = max(1, cur_page - 1)
                            st.rerun()
                    with pc2:
                        if st.button("下一页 ▶", key="entity_next", disabled=cur_page >= total_pages,
                                     use_container_width=True):
                            st.session_state["entity_page"] = min(total_pages, cur_page + 1)
                            st.rerun()
                    with pc3:
                        st.markdown(f"第 **{cur_page}** / **{total_pages}** 页")

        else:
            st.error(f"API 错误: {resp.status_code}")

    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端服务。请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")
