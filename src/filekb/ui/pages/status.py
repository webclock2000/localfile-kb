"""系统状态页面 — 健康检查、失败队列、运行记录、实时日志。

KB 感知：统计数据按当前知识库过滤。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("📊 系统状态")
st.caption(f"当前知识库: 「{kb}」")

# ── Load data ──
try:
    resp = requests.get(f"{API_BASE}/status?kb={kb}", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        health = data.get("health", {})

        # ── Health cards ──
        st.subheader("服务健康")
        hc1, hc2, hc3, hc4 = st.columns(4)
        with hc1:
            llm_status = health.get("llm_server", "unknown")
            st.metric(
                "LLM 服务",
                "✅ 在线" if llm_status == "ok" else "❌ 离线",
            )
        with hc2:
            sqlite = health.get("sqlite", {})
            st.metric(
                "数据库",
                f"{sqlite.get('size_mb', 0):.1f} MB",
            )
        with hc3:
            faiss = health.get("faiss", {})
            st.metric("FAISS 索引", f"{faiss.get('vectors', 0)} 向量")
        with hc4:
            graph_h = health.get("graph", {})
            st.metric(
                "知识图谱",
                f"{graph_h.get('nodes', 0)} 节点 / {graph_h.get('edges', 0)} 边",
            )

        # ── Last run ──
        last = data.get("last_run")
        if last:
            st.subheader("最近索引运行")
            st.json({
                "id": last.get("id"),
                "status": last.get("status"),
                "files_total": last.get("files_total"),
                "files_changed": last.get("files_changed"),
                "facts_added": last.get("facts_added"),
                "started_at": last.get("started_at"),
                "finished_at": last.get("finished_at"),
            })
        else:
            st.caption("尚未运行过索引。")

        # ── Actions ──
        st.subheader("操作")
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            if st.button("🔄 索引当前知识库", use_container_width=True):
                requests.post(f"{API_BASE}/index", json={"kb": kb}, timeout=10)
                st.success(f"「{kb}」索引已启动")
        with ac2:
            if st.button("📋 DLQ 重试", use_container_width=True):
                requests.post(f"{API_BASE}/index", json={"kb": kb, "dlq": True}, timeout=10)
                st.success("DLQ 重试已启动")
        with ac3:
            if st.button("🔄 刷新状态", use_container_width=True):
                st.rerun()

        # ── Logs ──
        st.subheader("📋 运行日志")
        lc1, lc2 = st.columns([1, 3])
        with lc1:
            log_level = st.selectbox(
                "日志级别", ["DEBUG", "INFO", "WARNING", "ERROR"], index=1
            )
        with lc2:
            log_lines = st.slider("行数", 10, 200, 50)

        try:
            log_resp = requests.get(
                f"{API_BASE}/logs",
                params={"lines": log_lines, "level": log_level},
                timeout=5,
            )
            if log_resp.status_code == 200:
                entries = log_resp.json().get("lines", [])
                if entries:
                    log_text = "\n".join(e["text"] for e in entries)
                    st.code(log_text, language=None)
                else:
                    st.caption("没有匹配的日志条目。")
        except Exception:
            st.caption("无法加载日志。")

        # ── KB list info ──
        st.subheader("知识库概览")
        kb_list = data.get("kb_list", [])
        if kb_list:
            st.markdown(" | ".join(f"`{k}`" for k in kb_list))
        st.metric("当前 KB 总数", len(kb_list))

    else:
        st.error(f"API 错误: {resp.status_code}")

except requests.exceptions.ConnectionError:
    st.error("无法连接到 FileKB 后端服务。请先启动服务。")
except Exception as e:
    st.error(f"出错了: {e}")
