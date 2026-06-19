"""实体审核页面。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 实体审核", page_icon="🔍", layout="wide")
st.title("🔍 实体合并审核")
st.caption("审查系统自动发现的疑似重复实体，批准或拒绝合并。")

try:
    resp = requests.get(f"{API_BASE}/entities/proposals", params={"status": "proposed"}, timeout=10)
    if resp.status_code == 200:
        proposals = resp.json().get("proposals", [])
        if not proposals:
            st.success("没有待审核的合并提案，所有实体已消歧。")
        else:
            st.info(f"共 {len(proposals)} 条提案待审核")
            for prop in proposals:
                with st.expander(
                    f"**{prop['entity_a']}** ↔ **{prop['entity_b']}** → "
                    f"`{prop.get('proposed_name', '?')}` (置信度: {prop.get('confidence', 0):.0%})"
                ):
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(f"✓ 批准", key=f"app_{prop['id']}"):
                            try:
                                requests.post(f"{API_BASE}/entities/merge", json={
                                    "proposal_id": prop["id"],
                                    "canonical_name": prop.get("proposed_name"),
                                })
                                st.success("合并成功！")
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))
                    with c2:
                        if st.button(f"✗ 拒绝", key=f"rej_{prop['id']}"):
                            try:
                                requests.post(f"{API_BASE}/entities/reject", json={"proposal_id": prop["id"]})
                                st.info("已拒绝。")
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))
    else:
        st.error(f"API 错误: {resp.status_code}")
except requests.exceptions.ConnectionError:
    st.warning("无法连接到 FileKB 后端服务。请先启动服务。")
except Exception as e:
    st.error(f"出错了: {e}")
