"""图谱页面 — 交互式知识图谱浏览。"""

import requests
import streamlit as st
import networkx as nx

API_BASE = "http://localhost:9494"


def _to_dot(G, center):
    """NetworkX → DOT for graphviz rendering."""
    lines = ["digraph G {", "  rankdir=LR;",
             '  node [shape=ellipse, style=filled, fillcolor="#e8f0fe"];']
    for node in G.nodes():
        color = "#fff9c4" if center in node else "#e8f0fe"
        lines.append(f'  "{node}" [fillcolor="{color}" label="{node}"];')
    for u, v, data in G.edges(data=True):
        pred = data.get("predicate", "")
        lines.append(f'  "{u}" -> "{v}" [label="{pred}"];')
    lines.append("}")
    return "\n".join(lines)


st.set_page_config(page_title="FileKB — 知识图谱", page_icon="🔗", layout="wide")
st.title("🔗 知识图谱浏览")
st.caption("探索知识库中实体之间的关联关系。支持模糊搜索。")

settings_resp = requests.get(f"{API_BASE}/settings", timeout=5)
kb_names = settings_resp.json().get("kb_names", ["默认"]) if settings_resp.status_code == 200 else ["默认"]

col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    entity_query = st.text_input("搜索实体", placeholder="例如：遥感、邓磊、张三")
with col2:
    hop = st.selectbox("扩展跳数", [1, 2, 3], index=0)
with col3:
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
                G = nx.DiGraph()
                for n in nodes:
                    G.add_node(n["name"], degree=n.get("degree", 0))
                for e in edges:
                    G.add_edge(e["source"], e["target"], predicate=e.get("predicate", ""))

                c1, c2 = st.columns([2, 1])
                with c1:
                    st.subheader(f"{len(nodes)} 个实体, {len(edges)} 条关系")
                    st.graphviz_chart(_to_dot(G, entity_query), use_container_width=True)
                with c2:
                    with st.expander("📋 实体列表", expanded=True):
                        for n in sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True):
                            st.markdown(f"**{n['name']}** (连接数: {n.get('degree', 0)})")
                    with st.expander("📋 关系列表"):
                        for e in edges:
                            st.markdown(f"**{e['source']}** → *{e.get('predicate', '?')}* → **{e['target']}**")
            else:
                st.info(f"未找到与 '{entity_query}' 相关的实体。试试更短的词？")
        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端，请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")
else:
    st.info("输入一个实体名称来探索知识图谱。支持模糊搜索——输入「遥感」即可匹配「遥感原理与方法」。")
