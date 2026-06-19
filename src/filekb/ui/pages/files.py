"""文件管理页面 — 文件列表、统计、索引控制。

KB 上下文从 st.session_state.global_kb 获取。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("📁 文件管理")
st.caption(f"「{kb}」的文件索引状态与操作。")

# ── Load data ──
try:
    resp = requests.get(f"{API_BASE}/status?kb={kb}", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        health = data.get("health", {})

        # Stats cards
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            st.metric("文件总数", data.get("files_total", 0))
        with sc2:
            st.metric("事实总数", data.get("facts_total", 0))
        with sc3:
            st.metric("实体数", health.get("graph", {}).get("nodes", 0))
        with sc4:
            st.metric("向量数", health.get("faiss", {}).get("vectors", 0))

        # Last run
        last = data.get("last_run")
        if last:
            status_icon = {"completed": "✅", "crashed": "❌", "running": "🔄"}.get(
                last.get("status", ""), "❓"
            )
            st.info(
                f"{status_icon} 最近一次索引: **{last.get('status', '?')}** — "
                f"处理了 {last.get('files_changed', 0)} 个文件，"
                f"新增 {last.get('facts_added', 0)} 条事实"
            )
        else:
            st.caption("尚未运行过索引。")

        # Actions
        st.subheader("操作")
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            if st.button("🔄 索引当前知识库", use_container_width=True):
                try:
                    resp2 = requests.post(
                        f"{API_BASE}/index", json={"kb": kb}, timeout=10
                    )
                    if resp2.status_code == 200:
                        st.success(f"「{kb}」索引任务已启动！")
                    else:
                        st.error(f"启动失败: {resp2.status_code}")
                except Exception as e:
                    st.error(f"错误: {e}")
        with ac2:
            if st.button("📋 重试失败队列", use_container_width=True):
                try:
                    resp2 = requests.post(
                        f"{API_BASE}/index", json={"kb": kb, "dlq": True}, timeout=10
                    )
                    if resp2.status_code == 200:
                        st.success("DLQ 重试已启动")
                    else:
                        st.error(f"启动失败: {resp2.status_code}")
                except Exception as e:
                    st.error(f"错误: {e}")
        with ac3:
            if st.button("🔄 刷新", use_container_width=True):
                st.rerun()

        # File list
        st.subheader("文件列表")
        st.caption("注：详细文件列表 API 尚未实现（见 FRONTEND_DESIGN.md §8.2）。")

    else:
        st.warning(f"API 状态检查失败: {resp.status_code}")
except requests.exceptions.ConnectionError:
    st.error("无法连接到 FileKB 后端服务。请先启动服务。")
except Exception as e:
    st.error(f"出错了: {e}")
