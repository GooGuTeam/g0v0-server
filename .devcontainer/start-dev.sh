#!/bin/bash

# 开发环境启动脚本
# 按依赖顺序启动：Performance Server → FastAPI → Spectator Server

set -e

if [ -f .env ]; then
    echo "加载 .env 文件中的环境变量..."
    set -a
    source .env
    set +a
else
    echo ".env 文件未找到，跳过加载环境变量。"
fi

echo "🚀 启动开发环境..."

# 清理函数
cleanup() {
    echo "🛑 正在停止服务..."
    [ ! -z "$SPECTATOR_PID" ] && kill $SPECTATOR_PID 2>/dev/null || true
    [ ! -z "$FASTAPI_PID" ] && kill $FASTAPI_PID 2>/dev/null || true
    [ ! -z "$PERFORMANCE_PID" ] && kill $PERFORMANCE_PID 2>/dev/null || true
    exit ${1:-0}
}

# 捕获中断信号和错误
trap 'cleanup 1' INT TERM ERR

# 健康检查函数
wait_for_service() {
    local url=$1
    local service_name=$2
    local pre_sleep=$3
    local max_attempts=30
    local attempt=0

    echo "等待 $service_name 启动..."
    if [ ! -z "$pre_sleep" ]; then
        sleep $pre_sleep
    fi

    while [ $attempt -lt $max_attempts ]; do
        # 使用 curl 检查，添加 10 秒超时，区分连接失败和 HTTP 错误
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 5 "$url" 2>/dev/null || echo "000")

        if [ "$http_code" = "200" ] || [ "$http_code" = "404" ]; then
            echo "✅ $service_name 已就绪 (HTTP $http_code)"
            return 0
        elif [ "$http_code" = "000" ]; then
            # 连接被拒绝或超时，服务还在启动中
            echo "  ⏳ $service_name 正在启动... (尝试 $((attempt + 1))/$max_attempts)"
        else
            # 其他 HTTP 状态码
            echo "  ⚠️  $service_name 返回 HTTP $http_code (尝试 $((attempt + 1))/$max_attempts)"
        fi

        attempt=$((attempt + 1))
        sleep 2
    done

    echo "❌ $service_name 启动超时"
    return 1
}

# 1. 启动 Performance Server (最底层依赖)
echo "启动 Performance Server..."
cd /workspaces/osu_lazer_api/performance-server
dotnet run --project PerformanceServer --urls "http://0.0.0.0:8090" &
PERFORMANCE_PID=$!

# 等待 Performance Server 就绪
if ! wait_for_service "http://localhost:8090" "Performance Server"; then
    echo "Performance Server 启动失败，停止启动流程"
    cleanup 1
fi

# 2. 启动 FastAPI 服务器 (依赖 Performance Server)
echo "启动 FastAPI 服务器..."
cd /workspaces/osu_lazer_api
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
FASTAPI_PID=$!

# 等待 FastAPI 就绪
if ! wait_for_service "http://localhost:8000/health" "FastAPI"; then
    echo "FastAPI 启动失败，停止启动流程"
    cleanup 1
fi

# 3. 启动 Spectator Server (依赖 FastAPI)
echo "启动 Spectator Server..."
cd /workspaces/osu_lazer_api/spectator-server
dotnet run --project osu.Server.Spectator --urls "http://0.0.0.0:8086" &
SPECTATOR_PID=$!

echo ""
echo "✅ 所有服务已启动:"
echo "  - FastAPI: http://localhost:8000"
echo "  - Spectator Server: http://localhost:8086"
echo "  - Performance Server: http://localhost:8090"
echo "  - Nginx (统一入口): http://localhost:8080"
echo ""
echo "按 Ctrl+C 停止所有服务"

# 等待用户中断
wait
