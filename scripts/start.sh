#!/bin/bash
# FileKB 启动脚本 — 启动 API 和前端，并自检
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 激活虚拟环境
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "❌ 未找到虚拟环境，请先运行: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
    exit 1
fi

# 清理旧进程
echo "🧹 清理旧进程..."
lsof -ti:9494 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:8501 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# 启动 FastAPI 后端
echo "🚀 启动 FastAPI 后端 (端口 9494)..."
FILEKB_LOG_LEVEL="${FILEKB_LOG_LEVEL:-INFO}" uvicorn filekb.server:app \
    --host 127.0.0.1 --port 9494 &
API_PID=$!

# 启动 Streamlit 前端
echo "🎨 启动 Streamlit 前端 (端口 8501)..."
streamlit run src/filekb/ui/app.py \
    --server.port 8501 --server.address 127.0.0.1 --server.headless true &
UI_PID=$!

# 自检
echo ""
echo "⏳ 等待服务就绪..."
sleep 4

check_api() {
    curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9494/status 2>/dev/null
}

check_ui() {
    curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8501 2>/dev/null
}

# 重试最多 5 次
for i in $(seq 1 5); do
    API_CODE=$(check_api)
    UI_CODE=$(check_ui)
    if [ "$API_CODE" = "200" ] && [ "$UI_CODE" = "200" ]; then
        echo ""
        echo "✅ FileKB 启动成功！"
        echo ""
        echo "   前端页面: http://127.0.0.1:8501"
        echo "   API 文档: http://127.0.0.1:9494/docs"
        echo "   API 状态: http://127.0.0.1:9494/status"
        echo ""
        echo "   日志级别: ${FILEKB_LOG_LEVEL:-INFO} (设置 FILEKB_LOG_LEVEL=DEBUG 开启调试)"
        echo ""
        echo "   按 Ctrl+C 停止所有服务"
        wait $API_PID $UI_PID
        exit 0
    fi
    echo "   尝试 $i/5: API=$API_CODE UI=$UI_CODE"
    sleep 2
done

echo "❌ 服务启动超时"
kill $API_PID $UI_PID 2>/dev/null
exit 1
