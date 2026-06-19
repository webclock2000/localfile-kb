"""文件管理页面。"""

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

st.set_page_config(page_title="FileKB — 文件管理", page_icon="📁", layout="wide")
st.title("📁 文件管理")
st.caption("查看已索引的文件和知识库统计信息。")

try:
    resp = requests.get(f"{API_BASE}/status", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("文件总数", data.get("files_total", 0))
        with c2:
            st.metric("事实总数", data.get("facts_total", 0))
        health = data.get("health", {})
        with c3:
            st.metric("实体数", health.get("graph", {}).get("nodes", 0))
        with c4:
            st.metric("向量数", health.get("faiss", {}).get("vectors", 0))

        last = data.get("last_run")
        if last:
            st.info(f"最近一次索引: **{last.get('status', '?')}** — 处理了 {last.get('files_changed', 0)} 个文件，新增 {last.get('facts_added', 0)} 条事实")
        else:
            st.caption("尚未运行过索引。在终端执行 `filekb index --watch` 来开始。")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 立即索引"):
                try:
                    requests.post(f"{API_BASE}/index", json={}, timeout=5)
                    st.success("索引任务已启动！")
                except Exception as e:
                    st.error(f"错误: {e}")
        with c2:
            if st.button("📋 重试失败队列"):
                try:
                    requests.post(f"{API_BASE}/index", json={"dlq": True}, timeout=5)
                    st.success("DLQ 重试已启动")
                except Exception as e:
                    st.error(f"错误: {e}")
    else:
        st.warning(f"API 状态检查失败: {resp.status_code}")
except requests.exceptions.ConnectionError:
    st.warning("无法连接到 FileKB 后端服务。请先启动服务: `filekb ui`")
except Exception as e:
    st.error(f"出错了: {e}")
