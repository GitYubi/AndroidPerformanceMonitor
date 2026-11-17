#!/usr/bin/env bash
# 本地运行入口：后端仅监听回环地址，前端通过 VITE_BACKEND_URL 调用它。
# 后端端口默认 8090；若与本地服务冲突，可用 BACKEND_PORT 覆盖，例如：
#   BACKEND_PORT=8091 ./run-local.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
BACKEND_PORT="${BACKEND_PORT:-8090}"
PYTHON="$BACKEND_DIR/.venv/bin/python"

if ! command -v adb >/dev/null 2>&1; then
  echo "未找到 adb。请安装 Android SDK Platform-Tools，并确保 adb 已加入 PATH。" >&2
  exit 1
fi

if [ ! -x "$PYTHON" ] || ! "$PYTHON" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  echo "后端环境未就绪。请在项目根目录运行 python3 -m venv backend/.venv，再用 backend/.venv/bin/pip install -r backend/requirements.txt。" >&2
  exit 1
fi
if ! command -v pnpm >/dev/null 2>&1 || [ ! -d "$ROOT_DIR/node_modules" ]; then
  echo "前端环境未就绪。请安装 pnpm 并在项目根目录运行 pnpm install。" >&2
  exit 1
fi

"$PYTHON" - "$BACKEND_PORT" <<'PY'
import socket
import sys

try:
    port = int(sys.argv[1])
    if not 1 <= port <= 65535:
        raise ValueError()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
except ValueError:
    sys.exit("BACKEND_PORT 必须是 1 到 65535 之间的端口号。")
except OSError as error:
    sys.exit(f"后端端口 {port} 不可用：{error}。请用 BACKEND_PORT 选择其他端口。")
PY

# Give each service its own process group, including the Vite process pnpm starts.
set -m
"$PYTHON" -m uvicorn app.main:app --app-dir "$BACKEND_DIR" --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!
FRONTEND_PID=''
cleanup() {
  kill -- "-$BACKEND_PID" 2>/dev/null || true
  if [ -n "$FRONTEND_PID" ]; then
    kill -- "-$FRONTEND_PID" 2>/dev/null || true
    wait "$FRONTEND_PID" 2>/dev/null || true
  fi
  wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"
if ! "$PYTHON" - "$BACKEND_PID" "$BACKEND_URL" <<'PY'
import os
import sys
import time
import urllib.request

pid, url = int(sys.argv[1]), sys.argv[2]
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        sys.exit("后端进程已退出，请查看上方诊断。")
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            if response.status == 200:
                sys.exit(0)
    except OSError:
        time.sleep(.2)
sys.exit("后端在 30 秒内未通过健康检查。")
PY
then
  exit 1
fi
echo "后端已通过健康检查（${BACKEND_URL}）"
cd "$ROOT_DIR"
VITE_BACKEND_URL="$BACKEND_URL" pnpm dev --host 127.0.0.1 &
FRONTEND_PID=$!
while kill -0 "$FRONTEND_PID" 2>/dev/null; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "后端进程已退出，请查看终端诊断。" >&2
    exit 1
  fi
  sleep 1
done
wait "$FRONTEND_PID"
