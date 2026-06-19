"""图谱页面 — 交互式知识图谱浏览。"""

import requests
import streamlit as st
import networkx as nx

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 知识图谱", page_icon="🔗", layout="wide")
st.title("🔗 知识图谱浏览")
st.caption("探索知识库中实体之间的关联关系。")

entity_query = st.text_input("搜索实体...", placeholder="例如：张三、遥感原理与方法、华为技术有限公司")
hop = st.slider("扩展跳数", min_value=1, max_value=3, value=1)

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
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.subheader(f"图谱: {len(nodes)} 个节点, {len(edges)} 条边")
                    st.graphviz_chart(_to_dot(G, entity_query), use_container_width=True)
                with col2:
                    st.subheader("实体节点")
                    for n in sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True):
                        st.markdown(f"**{n['name']}** (连接数: {n.get('degree', 0)})")
                    st.subheader("关系列表")
                    for e in edges[:30]:
                        st.markdown(f"**{e['source']}** → *{e.get('predicate', '?')}* → **{e['target']}**")
            else:
                st.info(f"未找到与 '{entity_query}' 相关的实体。")
        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端服务，请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")


def _to_dot(G: nx.DiGraph, center: str) -> str:
    lines = ["digraph G {", "  rankdir=LR;", '  node [shape=ellipse, style=filled, fillcolor="#e8f0fe"];']
    for node in G.nodes():
        color = "#fff9c4" if node == center else "#e8f0fe"
        lines.append(f'  "{node}" [fillcolor="{color}" label="{node}"];')
    for u, v, data in G.edges(data=True):
        pred = data.get("predicate", "")
        lines.append(f'  "{u}" -> "{v}" [label="{pred}"];')
    lines.append("}")
    return "\n".join(lines)
