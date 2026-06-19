"""知识图谱浏览页面 — 交互式实体关系网络。

yFiles Graphs for Streamlit — WebGL 渲染，支持拖拽/缩放/邻域探索。
KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("🔗 知识图谱浏览")
st.caption(f"探索「{kb}」中实体之间的关联关系。支持模糊搜索。")

# ── Search toolbar ──
tc1, tc2, tc3 = st.columns([3, 1, 1])
with tc1:
    entity_query = st.text_input(
        "搜索实体",
        placeholder="例如：遥感、邓磊、张三",
        label_visibility="collapsed",
    )
with tc2:
    hop = st.selectbox("扩展跳数", [1, 2, 3], index=0)
with tc3:
    if st.button("🔍 搜索", use_container_width=True, help="在知识图谱中搜索该实体及其关联关系"):
        pass  # Trigger rerun with current inputs

# ── Graph area ──
if entity_query:
    try:
        resp = requests.get(
            f"{API_BASE}/graph",
            params={"entity": entity_query, "hop": hop, "kb": kb},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            nodes = data.get("nodes", [])
            edges = data.get("edges", [])

            if nodes:
                # Try yFiles first, fall back to graphviz
                try:
                    from yfiles_graphs_for_streamlit import Layout, StreamlitGraphWidget
                    import networkx as nx

                    G = nx.DiGraph()
                    for n in nodes:
                        G.add_node(n["name"], degree=n.get("degree", 0))
                    for e in edges:
                        G.add_edge(
                            e["source"], e["target"],
                            predicate=e.get("predicate", ""),
                        )

                    st.subheader(f"{len(nodes)} 个实体, {len(edges)} 条关系")

                    widget = StreamlitGraphWidget.from_graph(G)
                    widget.show(
                        directed=True,
                        graph_layout=Layout.ORGANIC,
                        sidebar={"enabled": True, "start_with": "Neighborhood"},
                        overview=True,
                    )
                except ImportError:
                    # Fallback to graphviz
                    st.warning("yFiles 未安装，使用基础图渲染。运行 `pip3 install yfiles_graphs_for_streamlit` 获取交互式图谱。")

                    import networkx as nx

                    G = nx.DiGraph()
                    for n in nodes:
                        G.add_node(n["name"], degree=n.get("degree", 0))
                    for e in edges:
                        G.add_edge(
                            e["source"], e["target"],
                            predicate=e.get("predicate", ""),
                        )

                    # Build DOT
                    lines = [
                        "digraph G {",
                        "  rankdir=LR;",
                        '  node [shape=ellipse, style=filled, fillcolor="#DBEAFE", fontname="Inter"];',
                    ]
                    for node in G.nodes():
                        color = "#FEF3C7" if entity_query in node else "#DBEAFE"
                        lines.append(f'  "{node}" [fillcolor="{color}" label="{node}"];')
                    for u, v, d in G.edges(data=True):
                        pred = d.get("predicate", "")
                        lines.append(f'  "{u}" -> "{v}" [label="{pred}"];')
                    lines.append("}")

                    st.graphviz_chart("\n".join(lines), use_container_width=True)

                # ── Entity + Relation lists ──
                with st.expander(f"📋 实体列表 ({len(nodes)})"):
                    for n in sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True):
                        st.markdown(f"**{n['name']}** (连接数: {n.get('degree', 0)})")

                with st.expander(f"📋 关系列表 ({len(edges)})"):
                    for e in edges:
                        st.markdown(
                            f"**{e['source']}** → "
                            f"*{e.get('predicate', '?')}* → "
                            f"**{e['target']}**"
                        )
            else:
                st.info(f"未找到与「{entity_query}」相关的实体。试试更短的词？")
        else:
            st.error(f"API 错误: {resp.status_code}")
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 FileKB 后端，请先启动服务。")
    except Exception as e:
        st.error(f"出错了: {e}")
else:
    st.info(
        "输入一个实体名称来探索知识图谱。"
        "支持模糊搜索——输入「遥感」即可匹配「遥感原理与方法」。"
    )
