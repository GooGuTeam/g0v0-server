#!/bin/bash

# Development Environment Startup Script
# Startup order: Performance Server → FastAPI

set -e

if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    set -a
    source .env
    set +a
else
    echo ".env file not found, skipping loading environment variables."
fi

echo "🚀 Starting development environment..."

cleanup() {
    echo "🛑 Stopping services..."
    [ ! -z "$SPECTATOR_PID" ] && kill $SPECTATOR_PID 2>/dev/null || true
    [ ! -z "$FASTAPI_PID" ] && kill $FASTAPI_PID 2>/dev/null || true
    [ ! -z "$PERFORMANCE_PID" ] && kill $PERFORMANCE_PID 2>/dev/null || true
    exit ${1:-0}
}

trap 'cleanup 1' INT TERM ERR

wait_for_service() {
    local url=$1
    local service_name=$2
    local pre_sleep=$3
    local max_attempts=30
    local attempt=0

    echo "Waiting for $service_name to start..."
    if [ ! -z "$pre_sleep" ]; then
        sleep $pre_sleep
    fi

    while [ $attempt -lt $max_attempts ]; do
        http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 5 "$url" 2>/dev/null || echo "000")

        if [ "$http_code" = "200" ] || [ "$http_code" = "404" ]; then
            echo "✅ $service_name is ready (HTTP $http_code)"
            return 0
        elif [ "$http_code" = "000" ]; then
            echo "  ⏳ $service_name is starting... (Attempt $((attempt + 1))/$max_attempts)"
        else
            echo "  ⚠️  $service_name returned HTTP $http_code (Attempt $((attempt + 1))/$max_attempts)"
        fi

        attempt=$((attempt + 1))
        sleep 2
    done

    echo "❌ $service_name startup timed out"
    return 1
}

echo "Starting Performance Server..."
cd /workspaces/osu_lazer_api/performance-server
dotnet run --project PerformanceServer --urls "http://0.0.0.0:8090" &
PERFORMANCE_PID=$!

if ! wait_for_service "http://localhost:8090" "Performance Server"; then
    echo "Performance Server failed to start, stopping startup process"
    cleanup 1
fi

echo "Starting FastAPI server..."
cd /workspaces/osu_lazer_api
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
FASTAPI_PID=$!

if ! wait_for_service "http://localhost:8000/health" "FastAPI"; then
    echo "FastAPI failed to start, stopping startup process"
    cleanup 1
fi

echo ""
echo "✅ All services are up and running:"
echo "  - FastAPI: http://localhost:8000"
echo "  - Performance Server: http://localhost:8090"
echo ""
echo "Press Ctrl+C to stop all services"

wait
