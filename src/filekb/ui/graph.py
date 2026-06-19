"""图谱页面 — 交互式知识图谱浏览。

使用 yfiles-graphs-for-streamlit (ADR-4 设计文档指定)。
支持力导向布局、节点拖拽、缩放、邻域探索。
"""

import requests
import streamlit as st
import networkx as nx
from yfiles_graphs_for_streamlit import GraphLayout, LayoutAlgorithm

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 知识图谱", page_icon="🔗", layout="wide")
st.title("🔗 知识图谱浏览")
st.caption("交互式探索知识库中实体之间的关联关系。支持拖拽、缩放、点击查看详情。")

col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    entity_query = st.text_input("搜索实体", placeholder="例如：张三、遥感原理与方法")
with col2:
    hop = st.selectbox("扩展跳数", [1, 2, 3], index=0)
with col3:
    layout_choice = st.selectbox("布局", ["层级", "有机", "圆形"], index=0)

LAYOUT_MAP = {
    "层级": LayoutAlgorithm.HIERARCHIC,
    "有机": LayoutAlgorithm.ORGANIC,
    "圆形": LayoutAlgorithm.CIRCULAR,
}

if entity_query:
    try:
        resp = requests.get(f"{API_BASE}/graph", params={"entity": entity_query, "hop": hop}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])
            if nodes:
                G = nx.DiGraph()
                for n in nodes:
                    G.add_node(n["name"], degree=n.get("degree", 0))
                for e in edges:
                    G.add_edge(e["source"], e["target"], predicate=e.get("predicate", ""))
                st.subheader(f"找到 {len(nodes)} 个实体, {len(edges)} 条关系")
                graph_layout = GraphLayout(
                    graph=G,
                    layout=LAYOUT_MAP.get(layout_choice, LayoutAlgorithm.HIERARCHIC),
                    edge_label="predicate",
                    directed=True,
                )
                graph_layout.show()
                with st.expander("📋 实体列表"):
                    for n in sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True):
                        st.markdown(f"**{n['name']}** (连接数: {n.get('degree', 0)})")
                with st.expander("📋 关系列表"):
                    for e in edges:
                        st.markdown(f"**{e['source']}** → *{e.get('predicate', '?')}* → **{e['target']}**")
            else:
                st.info(f"未找到与 '{entity_query}' 相关的实体。")
        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端服务，请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")
else:
    st.info(
        "输入一个实体名称来探索知识图谱。\n\n"
        "图谱支持：拖拽节点、滚轮缩放、点击查看详情、切换布局算法。"
    )
