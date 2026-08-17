#!/usr/bin/env bash
# 本地运行入口：后端仅监听回环地址，前端通过 VITE_BACKEND_URL 调用它。
# 后端端口默认 8090；若与本地服务冲突，可用 BACKEND_PORT 覆盖，例如：
#   BACKEND_PORT=8091 ./run-local.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
BACKEND_PORT="${BACKEND_PORT:-8090}"

if ! command -v adb >/dev/null 2>&1; then
  echo "未找到 adb。请安装 Android SDK Platform-Tools，并确保 adb 已加入 PATH。" >&2
  exit 1
fi

if [ ! -d "$BACKEND_DIR/.venv" ]; then
  python3 -m venv "$BACKEND_DIR/.venv"
fi
# "$BACKEND_DIR/.venv/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"

"$BACKEND_DIR/.venv/bin/python" -m uvicorn app.main:app --app-dir "$BACKEND_DIR" --host 127.0.0.1 --port "${BACKEND_PORT}" &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM
echo "完成后端开启（http://127.0.0.1:${BACKEND_PORT}）"
cd "$ROOT_DIR"
VITE_BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}" pnpm dev --host 127.0.0.1
echo "完成前端开启"
