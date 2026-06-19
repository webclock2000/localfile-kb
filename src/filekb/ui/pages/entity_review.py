"""实体审核页面 — 审查系统自动发现的疑似重复实体。

KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("🔍 实体合并审核")
st.caption(f"审查「{kb}」中系统发现的疑似重复实体，批准或拒绝合并。")

# ── Load proposals ──
try:
    resp = requests.get(
        f"{API_BASE}/entities/proposals",
        params={"status": "proposed", "kb": kb},
        timeout=10,
    )
    if resp.status_code == 200:
        proposals = resp.json().get("proposals", [])

        if not proposals:
            st.success("没有待审核的合并提案，所有实体已消歧。")
        else:
            st.info(f"共 {len(proposals)} 条提案待审核")

            # Pending
            pending = [p for p in proposals if p.get("status") == "proposed"]
            processed = [p for p in proposals if p.get("status") != "proposed"]

            if pending:
                st.subheader(f"待处理 ({len(pending)})")
                for prop in pending:
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
                                if st.button("✓ 批准", key=f"app_{prop['id']}", use_container_width=True):
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
                                if st.button("✗ 拒绝", key=f"rej_{prop['id']}", use_container_width=True):
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

            # Processed
            if processed:
                st.subheader(f"已处理 ({len(processed)})")
                for prop in processed:
                    status_tag = "✅ 已合并" if prop.get("status") == "approved" else "❌ 已拒绝"
                    with st.expander(
                        f"{status_tag} | {prop['entity_a']} ↔ {prop['entity_b']}"
                    ):
                        st.markdown(
                            f"提议名称: `{prop.get('proposed_name', '?')}`\n"
                            f"置信度: {prop.get('confidence', 0):.0%}"
                        )
    else:
        st.error(f"API 错误: {resp.status_code}")
except requests.exceptions.ConnectionError:
    st.error("无法连接到 FileKB 后端服务。请先启动服务。")
except Exception as e:
    st.error(f"出错了: {e}")
