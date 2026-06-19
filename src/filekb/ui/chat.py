"""聊天页面 — 对话式知识库问答。

参考设计: AnythingLLM 极简聊天 + Dify 流式反馈。
用自然语言提问，回答附源文件引用，可点赞/踩反馈。
"""

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 对话问答", page_icon="💬", layout="wide")

st.title("💬 知识库问答")
st.caption("向你的知识库提问，每一条回答都会注明信息来源。")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("📎 参考来源"):
                for src in msg["sources"]:
                    st.markdown(f"- `{src['file']}`")

question = st.chat_input("输入你的问题...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("正在搜索知识库..."):
            try:
                resp = requests.post(
                    f"{API_BASE}/ask", json={"question": question}, timeout=120,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    answer = data.get("answer", "未能获取回答。")
                    sources = data.get("sources", [])
                    related = data.get("related_facts", [])

                    st.markdown(answer)

                    if sources:
                        with st.expander("📎 参考来源"):
                            for src in sources:
                                st.markdown(f"- `{src['file']}`")

                    if related:
                        with st.expander("🔗 相关事实"):
                            for fact in related:
                                st.markdown(
                                    f"**{fact.get('subject', '?')}** "
                                    f"*{fact.get('predicate', '?')}* "
                                    f"**{fact.get('object', '?')}** "
                                    f"— `{fact.get('source', '?')}`"
                                )

                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("👍 有帮助"):
                            try:
                                requests.post(
                                    f"{API_BASE}/feedback",
                                    json={"type": "positive", "cited_facts": related},
                                )
                                st.success("感谢你的反馈！")
                            except Exception:
                                pass
                    with c2:
                        if st.button("👎 没帮助"):
                            try:
                                requests.post(
                                    f"{API_BASE}/feedback",
                                    json={"type": "negative", "cited_facts": related},
                                )
                                st.info("感谢反馈，我们会改进。")
                            except Exception:
                                pass

                    st.session_state.messages.append({
                        "role": "assistant", "content": answer, "sources": sources,
                    })
                else:
                    st.error(f"API 错误: {resp.status_code}")
            except requests.exceptions.ConnectionError:
                st.error("无法连接到 FileKB 后端服务，请先启动服务。")
            except Exception as e:
                st.error(f"出错了: {e}")

with st.sidebar:
    st.header("📊 知识库概览")
    try:
        resp = requests.get(f"{API_BASE}/status", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            st.metric("文件数", data.get("files_total", 0))
            st.metric("事实数", data.get("facts_total", 0))
            health = data.get("health", {})
            st.metric("向量数", health.get("faiss", {}).get("vectors", 0))
            st.metric("实体数", health.get("graph", {}).get("nodes", 0))
        else:
            st.warning("后端 API 不可用")
    except Exception:
        st.warning("后端服务未启动")

    if st.button("🗑 清空对话"):
        st.session_state.messages = []
        st.rerun()
