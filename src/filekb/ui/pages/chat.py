"""对话问答页面 — 向知识库用自然语言提问。

参考设计: AnythingLLM 极简 Chat + Dify 流式反馈。
KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("💬 知识库问答")
st.caption(f"向「{kb}」提问，每一条回答都会注明信息来源。")

# ── Messages ──
if "messages" not in st.session_state:
    st.session_state.messages = []

# Render history
for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("📎 参考来源"):
                for src in msg["sources"]:
                    st.markdown(f"- `{src.get('file', '?')}`")
        if msg.get("related_facts"):
            with st.expander("🔗 相关事实"):
                for fact in msg["related_facts"]:
                    st.markdown(
                        f"**{fact.get('subject', '?')}** "
                        f"*{fact.get('predicate', '?')}* "
                        f"**{fact.get('object', '?')}** "
                        f"— `{fact.get('source', '?')}`"
                    )

        # Feedback buttons (only for assistant messages)
        if msg["role"] == "assistant" and "feedback_given" not in msg:
            fc1, fc2 = st.columns([1, 1])
            with fc1:
                if st.button(
                    "👍 有帮助",
                    key=f"up_{i}",
                    help="标记此回答有帮助，系统会提升相关知识的权重",
                ):
                    try:
                        requests.post(
                            f"{API_BASE}/feedback",
                            json={
                                "type": "positive",
                                "cited_facts": msg.get("related_facts", []),
                                "kb": kb,
                            },
                            timeout=10,
                        )
                        st.session_state.messages[i]["feedback_given"] = "positive"
                        st.success("感谢你的反馈！")
                        st.rerun()
                    except Exception:
                        st.error("反馈提交失败")
            with fc2:
                if st.button(
                    "👎 没帮助",
                    key=f"down_{i}",
                    help="标记此回答无帮助，系统会降低相关知识的权重",
                ):
                    try:
                        requests.post(
                            f"{API_BASE}/feedback",
                            json={
                                "type": "negative",
                                "cited_facts": msg.get("related_facts", []),
                                "kb": kb,
                            },
                            timeout=10,
                        )
                        st.session_state.messages[i]["feedback_given"] = "negative"
                        st.info("感谢反馈，我们会改进。")
                        st.rerun()
                    except Exception:
                        st.error("反馈提交失败")

# ── Input ──
question = st.chat_input("输入你的问题...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("正在搜索知识库..."):
            try:
                resp = requests.post(
                    f"{API_BASE}/ask",
                    json={"question": question, "kb": kb},
                    timeout=120,
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
                                st.markdown(f"- `{src.get('file', '?')}`")

                    if related:
                        with st.expander("🔗 相关事实"):
                            for fact in related:
                                st.markdown(
                                    f"**{fact.get('subject', '?')}** "
                                    f"*{fact.get('predicate', '?')}* "
                                    f"**{fact.get('object', '?')}** "
                                    f"— `{fact.get('source', '?')}`"
                                )

                    # Feedback
                    fc1, fc2 = st.columns([1, 1])
                    with fc1:
                        if st.button(
                            "👍 有帮助",
                            key="up_new",
                            help="标记此回答有帮助，系统会提升相关知识的权重",
                        ):
                            try:
                                requests.post(
                                    f"{API_BASE}/feedback",
                                    json={"type": "positive", "cited_facts": related, "kb": kb},
                                    timeout=10,
                                )
                                st.success("感谢你的反馈！")
                            except Exception:
                                st.error("反馈提交失败")
                    with fc2:
                        if st.button(
                            "👎 没帮助",
                            key="down_new",
                            help="标记此回答无帮助，系统会降低相关知识的权重",
                        ):
                            try:
                                requests.post(
                                    f"{API_BASE}/feedback",
                                    json={"type": "negative", "cited_facts": related, "kb": kb},
                                    timeout=10,
                                )
                                st.info("感谢反馈，我们会改进。")
                            except Exception:
                                st.error("反馈提交失败")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "sources": sources,
                        "related_facts": related,
                    })
                else:
                    st.error(f"API 错误: {resp.status_code}")
            except requests.exceptions.ConnectionError:
                st.error("无法连接到 FileKB 后端服务，请先启动服务。")
            except Exception as e:
                st.error(f"出错了: {e}")

# ── Bottom actions ──
st.divider()
c1, c2 = st.columns(2)
with c1:
    if st.button(
        "🗑 清空对话",
        use_container_width=True,
        help="清空当前对话历史。此操作不影响知识库中已索引的数据。",
    ):
        st.session_state.messages = []
        st.rerun()
with c2:
    if st.button("🔄 刷新统计", use_container_width=True, help="刷新页面数据"):
        st.rerun()
