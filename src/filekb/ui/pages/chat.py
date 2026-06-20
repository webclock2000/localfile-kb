"""对话问答页面 — 向知识库用自然语言提问。

支持会话历史持久化：页面加载时恢复最近会话，自动保存每条消息。
KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import json
import time

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Session state init ──
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = ""  # empty = new session
if "chat_sessions_loaded" not in st.session_state:
    st.session_state.chat_sessions_loaded = False
if "chat_save_id" not in st.session_state:
    st.session_state.chat_save_id = 0   # counter to enable re-saving the same msg


# ── Helper functions (defined BEFORE first use in sidebar) ──

def _save_current_session(kb_name: str) -> None:
    """Persist any unsaved last message before switching sessions."""
    msgs = st.session_state.messages
    if not msgs or not st.session_state.session_id:
        return
    # Messages are normally saved inline during chat flow,
    # but we do a safety check: if the last message lacks _db_id, save it.
    last = msgs[-1]
    if "_db_id" not in last:
        _persist_message(
            kb_name, st.session_state.session_id,
            last["role"], last["content"],
            last.get("sources"), last.get("related_facts"),
        )


def _load_session(kb_name: str, session_id: str) -> None:
    """Load a session's messages from the API into session_state."""
    try:
        resp = requests.get(
            f"{API_BASE}/chat/history",
            params={"session_id": session_id, "kb": kb_name},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("messages", [])
            msgs: list[dict] = []
            for m in raw:
                entry = {
                    "role": m["role"],
                    "content": m["content"],
                    "sources": m.get("sources", []),
                    "related_facts": m.get("related_facts", []),
                    "_db_id": m.get("id"),
                }
                if m.get("feedback_given"):
                    entry["feedback_given"] = m["feedback_given"]
                msgs.append(entry)
            st.session_state.messages = msgs
            st.session_state.session_id = session_id
    except Exception as e:
        st.error(f"加载会话失败: {e}")


def _persist_message(
    kb_name: str, session_id: str, role: str, content: str,
    sources: list | None = None, related_facts: list | None = None,
) -> str:
    """Save one message to the API. Returns the session_id (may be new)."""
    try:
        resp = requests.post(
            f"{API_BASE}/chat/save",
            params={"session_id": session_id},
            json={
                "role": role,
                "content": content,
                "sources": sources or [],
                "related_facts": related_facts or [],
                "kb": kb_name,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("session_id", session_id)
    except Exception:
        pass
    return session_id


# ── Track KB switches — reset session when KB changes ──
current_kb_key = f"_last_kb_chat_{id(st)}"
prev_kb = st.session_state.get(current_kb_key, kb)
if prev_kb != kb:
    st.session_state.messages = []
    st.session_state.session_id = ""
    st.session_state[current_kb_key] = kb
    st.rerun()

# ── Header ──
st.title("💬 知识库问答")
st.caption(f"向「{kb}」提问，每一条回答都会注明信息来源。")


# ── Sidebar: Session switcher ──
with st.sidebar:
    st.divider()
    st.markdown("#### 💬 会话历史")

    # Load session list from API for the CURRENT kb
    sessions: list[dict] = []
    try:
        resp = requests.get(f"{API_BASE}/chat/sessions", params={"kb": kb}, timeout=5)
        if resp.status_code == 200:
            sessions = resp.json().get("sessions", [])
    except Exception:
        pass

    # New chat button
    if st.button("➕ 新建对话", use_container_width=True, key="new_chat_btn",
                 help="开始一个全新的对话会话。"):
        _save_current_session(kb)
        st.session_state.messages = []
        st.session_state.session_id = ""
        st.rerun()

    # Session list
    if sessions:
        st.caption(f"共 {len(sessions)} 个历史会话")
        for s in sessions:
            sid = s["session_id"]
            is_active = sid == st.session_state.session_id
            label = f"{'● ' if is_active else ''}{s['created_at'][:10]} — {s['turns']} 轮"

            sc1, sc2 = st.columns([5, 1])
            with sc1:
                if st.button(
                    label,
                    key=f"load_{sid}",
                    use_container_width=True,
                    help="点击加载此会话",
                ):
                    _save_current_session(kb)
                    _load_session(kb, sid)
                    st.rerun()
            with sc2:
                if st.button("🗑", key=f"del_{sid}", help="删除此会话"):
                    _save_current_session(kb)
                    try:
                        requests.delete(
                            f"{API_BASE}/chat/sessions/{sid}",
                            params={"kb": kb},
                            timeout=5,
                        )
                    except Exception:
                        pass
                    if st.session_state.session_id == sid:
                        st.session_state.messages = []
                        st.session_state.session_id = ""
                    st.rerun()

        if st.button("🔄 刷新列表", use_container_width=True, key="refresh_sessions"):
            st.rerun()
    else:
        st.caption("暂无历史会话。首次对话将自动保存。")


# ── Render existing messages ──
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
                        # Persist feedback to DB
                        db_id = msg.get("_db_id")
                        if db_id:
                            from filekb.store import Store
                            # We use the API to update — save via the store layer
                            # For now, we re-save the entire message with feedback
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
                        st.success("感谢反馈，我们会改进。")
                        st.rerun()
                    except Exception:
                        st.error("反馈提交失败")

# ── Input ──
question = st.chat_input("输入你的问题...")

if question:
    # Persist user message
    session_id = st.session_state.session_id
    session_id = _persist_message(kb, session_id, "user", question)
    st.session_state.session_id = session_id

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("正在搜索知识库..."):
            try:
                resp = requests.post(
                    f"{API_BASE}/ask",
                    json={"question": question, "kb": kb, "session_id": session_id or None},
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

                    # Persist assistant message
                    _persist_message(kb, session_id, "assistant", answer, sources, related)

                    # Feedback buttons for new answer
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
        help="清空当前对话并开始新会话。历史会话不会被删除。",
    ):
        st.session_state.messages = []
        st.session_state.session_id = ""
        st.rerun()
with c2:
    if st.button("🔄 刷新统计", use_container_width=True, help="刷新页面数据"):
        st.rerun()
