"""图谱页面 — 交互式知识图谱浏览 (yfiles-graphs-for-streamlit)."""

import requests
import streamlit as st
import networkx as nx
from yfiles_graphs_for_streamlit import GraphWidget, Node, Edge

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 知识图谱", page_icon="🔗", layout="wide")
st.title("🔗 知识图谱浏览")
st.caption("交互式探索实体关系。支持拖拽、缩放、点击查看详情。")

# KB selector
settings_resp = requests.get(f"{API_BASE}/settings", timeout=5)
kb_names = settings_resp.json().get("kb_names", ["默认"]) if settings_resp.status_code == 200 else ["默认"]

col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    entity_query = st.text_input("搜索实体", placeholder="例如：遥感原理与方法")
with col2:
    hop = st.selectbox("跳数", [1, 2, 3], index=0)
with col3:
    layout_choice = st.selectbox("布局", ["层级", "有机", "圆形", "正交"], index=0)
with col4:
    selected_kb = st.selectbox("知识库", kb_names, index=0)

if entity_query:
    try:
        resp = requests.get(
            f"{API_BASE}/graph",
            params={"entity": entity_query, "hop": hop, "kb": selected_kb},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])

            if nodes:
                # Build yfiles graph
                widget = GraphWidget()
                widget.directed = True
                widget.graph_layout = layout_choice

                yf_nodes = []
                for n in nodes:
                    yf_nodes.append(Node(
                        id=n["name"],
                        properties={"degree": n.get("degree", 0), "label": n["name"]},
                    ))
                widget.set_nodes(yf_nodes)

                yf_edges = []
                for e in edges:
                    pid = f"{e['source']}-{e['predicate']}-{e['target']}"
                    yf_edges.append(Edge(
                        start=e["source"], end=e["target"], id=pid,
                        properties={"predicate": e.get("predicate", "")},
                    ))
                widget.set_edges(yf_edges)

                # Show interactive graph
                st.subheader(f"找到 {len(nodes)} 个实体, {len(edges)} 条关系")
                widget

                # Detail lists
                c1, c2 = st.columns(2)
                with c1:
                    with st.expander("📋 实体列表"):
                        for n in sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True):
                            st.markdown(f"**{n['name']}** (连接数: {n.get('degree', 0)})")
                with c2:
                    with st.expander("📋 关系列表"):
                        for e in edges:
                            st.markdown(f"**{e['source']}** → *{e.get('predicate', '?')}* → **{e['target']}**")
            else:
                st.info(f"未找到与 '{entity_query}' 相关的实体。")
        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端，请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")
else:
    st.info("输入一个实体名称来探索知识图谱。支持拖拽节点、滚轮缩放、切换布局。")
