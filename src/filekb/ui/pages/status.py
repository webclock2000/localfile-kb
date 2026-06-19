"""系统状态页面 — 健康检查、失败队列 (DLQ)、运行记录、实时日志。

KB 感知：统计数据按当前知识库过滤。
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://localhost:9494"

ERROR_CLASS_LABELS: dict[str, str] = {
    "RATE_LIMIT": "🚦 限流",
    "TIMEOUT": "⏱ 超时",
    "RATE_LIMIT/TIMEOUT": "🚦⏱ 限流/超时",
    "MODEL_UNAVAILABLE": "🔌 模型不可用",
    "INVALID_JSON": "📝 JSON 解析错误",
    "CONTEXT_LENGTH_EXCEEDED": "📏 上下文超长",
    "UNKNOWN": "❓ 未知错误",
}

RETRYABLE_CLASSES = {
    "RATE_LIMIT", "TIMEOUT", "RATE_LIMIT/TIMEOUT",
    "MODEL_UNAVAILABLE", "CONTEXT_LENGTH_EXCEEDED",
}


def _is_retryable(error_class: str) -> bool:
    return error_class in RETRYABLE_CLASSES


# ── KB context ──
kb: str = st.session_state.get("global_kb", "默认")

# ── Header ──
st.title("📊 系统状态")
st.caption(f"当前知识库: 「{kb}」")

try:
    resp = requests.get(f"{API_BASE}/status?kb={kb}", timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        health = data.get("health", {})

        # ═══════════════════════════════════════════════════════════
        # Service Health Cards
        # ═══════════════════════════════════════════════════════════
        st.subheader("🫀 服务健康")
        hc1, hc2, hc3, hc4 = st.columns(4)
        with hc1:
            llm_status = health.get("llm_server", "unknown")
            st.metric(
                "LLM 服务",
                "✅ 在线" if llm_status == "ok" else "❌ 离线",
            )
        with hc2:
            sqlite = health.get("sqlite", {})
            st.metric("数据库", f"{sqlite.get('size_mb', 0):.1f} MB")
        with hc3:
            faiss = health.get("faiss", {})
            st.metric("FAISS 索引", f"{faiss.get('vectors', 0)} 向量")
        with hc4:
            graph_h = health.get("graph", {})
            st.metric(
                "知识图谱",
                f"{graph_h.get('nodes', 0)} 节点 / {graph_h.get('edges', 0)} 边",
            )

        # ═══════════════════════════════════════════════════════════
        # Last Run
        # ═══════════════════════════════════════════════════════════
        last = data.get("last_run")
        if last:
            st.subheader("📜 最近索引运行")
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

        # ═══════════════════════════════════════════════════════════
        # Index action
        # ═══════════════════════════════════════════════════════════
        st.subheader("🔄 索引操作")
        if st.button(
            f"🔄 索引「{kb}」知识库",
            use_container_width=True,
            type="primary",
            help=f"扫描「{kb}」的所有监控目录，检测文件变更（新增/修改/删除），"
            "解析文件内容并通过 LLM 提取实体和关系，更新向量索引和知识图谱。",
        ):
            requests.post(f"{API_BASE}/index", json={"kb": kb}, timeout=10)
            st.success(f"「{kb}」索引已启动")

        # ═══════════════════════════════════════════════════════════
        # Dead Letter Queue
        # ═══════════════════════════════════════════════════════════
        st.divider()
        st.subheader("📋 失败队列 (Dead Letter Queue)")
        st.caption(
            "索引过程中因 LLM 超时、JSON 解析错误、模型不可用等原因"
            "失败的文本块会进入此队列。可重试的错误类型可以自动或手动重新处理。"
        )

        try:
            dlq_resp = requests.get(
                f"{API_BASE}/dlq", params={"kb": kb, "limit": 50}, timeout=10
            )
            if dlq_resp.status_code == 200:
                dlq_data = dlq_resp.json()
                total = dlq_data.get("total_pending", 0)
                by_class: dict = dlq_data.get("by_error_class", {})
                retryable = dlq_data.get("retryable", 0)
                entries: list = dlq_data.get("entries", [])

                if total == 0:
                    st.success("🎉 失败队列为空，所有文本块均已成功处理。")
                else:
                    # ── Stats cards ──
                    num_classes = len(by_class)
                    dcols = st.columns(max(num_classes + 1, 2))
                    with dcols[0]:
                        st.metric("总失败块", total)
                    col_idx = 1
                    for error_class, cnt in sorted(by_class.items(), key=lambda x: -x[1]):
                        label = ERROR_CLASS_LABELS.get(error_class, error_class)
                        with dcols[col_idx]:
                            st.metric(label, cnt)
                        col_idx += 1
                        if col_idx >= len(dcols):
                            break

                    # ── Retry action ──
                    st.caption(
                        f"其中 **{retryable}** 个可自动重试"
                        f"（超时、限流、模型不可用、上下文超长）"
                    )
                    rc1, rc2 = st.columns([1, 3])
                    with rc1:
                        if retryable > 0 and st.button(
                            "📋 重试全部可重试项",
                            use_container_width=True,
                            type="primary",
                            help=f"将 {retryable} 个可重试的失败块重新提交处理。"
                            "不可重试的错误（如 JSON 解析错误）将被跳过。",
                        ):
                            with st.spinner("正在重试..."):
                                retry_resp = requests.post(
                                    f"{API_BASE}/dlq/retry",
                                    json={"kb": kb},
                                    timeout=60,
                                )
                                if retry_resp.status_code == 200:
                                    rd = retry_resp.json()
                                    st.success(f"已重试 {rd.get('retried', 0)} 个条目")
                                    st.rerun()
                                else:
                                    st.error(f"重试失败 ({retry_resp.status_code})")

                    # ── Entry list ──
                    st.markdown("---")
                    st.caption(f"失败详情（最近 {len(entries)} 条）")

                    for entry in entries:
                        is_retryable = _is_retryable(entry["error_class"])
                        label = ERROR_CLASS_LABELS.get(
                            entry["error_class"], entry["error_class"]
                        )
                        status_tag = "🔄 可重试" if is_retryable else "🚫 已放弃"

                        with st.container(border=True):
                            ec1, ec2 = st.columns([5, 1])
                            with ec1:
                                file = entry.get("file_path", "(未知文件)")
                                st.markdown(f"**{file}**")
                                st.caption(
                                    f"{label} · {status_tag} · "
                                    f"重试 {entry['retry_count']}/3 · "
                                    f"{entry.get('created_at', '')[:19]}"
                                )
                                if entry.get("error_msg"):
                                    with st.expander("错误详情"):
                                        st.code(entry["error_msg"], language=None)
                            with ec2:
                                if is_retryable and st.button(
                                    "🔄 重试",
                                    key=f"dlq_retry_{entry['id']}",
                                    use_container_width=True,
                                ):
                                    with st.spinner("重试中..."):
                                        sr = requests.post(
                                            f"{API_BASE}/dlq/retry",
                                            json={"entry_id": entry["id"], "kb": kb},
                                            timeout=30,
                                        )
                                        if sr.status_code == 200:
                                            st.success("已重试")
                                            st.rerun()
                                        else:
                                            st.error(f"失败 ({sr.status_code})")
            else:
                st.warning(f"加载 DLQ 数据失败 ({dlq_resp.status_code})")
        except requests.exceptions.ConnectionError:
            st.warning("无法连接后端，跳过 DLQ 数据加载。")
        except Exception as e:
            st.warning(f"加载 DLQ 数据时出错: {e}")

        st.divider()

        # ═══════════════════════════════════════════════════════════
        # Logs
        # ═══════════════════════════════════════════════════════════
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
                log_entries = log_resp.json().get("lines", [])
                if log_entries:
                    log_text = "\n".join(e["text"] for e in log_entries)
                    st.code(log_text, language=None)
                else:
                    st.caption("没有匹配的日志条目。")
        except Exception:
            st.caption("无法加载日志。")

        # ═══════════════════════════════════════════════════════════
        # KB overview
        # ═══════════════════════════════════════════════════════════
        st.subheader("📚 知识库概览")
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
