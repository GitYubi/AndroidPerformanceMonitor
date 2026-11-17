# 本机离线回归和工程证据

2026-11-16，macOS arm64，仓库已有 Python、Node 和 pnpm。未访问 ADB 设备或 CAN 通道。

- Python：在临时 PATH 前置返回 84 的 adb 替身，设置 PYTHONPATH=backend 及临时 ZCAN_DATA_DIR。使用 `backend/.venv/bin/python -B -m pytest -p no:cacheprovider backend/tests -q --ignore=backend/tests/zcan --deselect=backend/tests/test_ui_testing.py::test_can_steps_use_selected_dbc_and_share_the_bus --deselect=backend/tests/test_ui_testing.py::test_cancel_can_loop_keeps_unrelated_sends`，退出 0，138 passed、2 deselected，12.19 秒。
- Node：`node --test tools/ui-test/*.test.mjs`，40 项通过。
- 任务校验回归：`python3 -B -m unittest discover -s scripts -p 'test_*.py'`，16 项通过。
- 类型：`pnpm check` 退出 0，现有 pnpm 版本提示 package.json 的两项 pnpm 字段未读取。覆盖主 tsconfig 的 client/shared/server。
- 构建：`pnpm exec vite build --outDir <临时目录>/public`、`pnpm exec esbuild server/index.ts --platform=node --packages=external --bundle --format=esm --outdir=<临时目录>`，均退出 0，验证 public/index.html 与 index.js 存在。Vite 提示两个 analytics 变量未设置、旧 loader API 弃用、JS 1,096.50 kB（gzip 309.27 kB）超过 500 kB 阈值。未覆盖现有 dist。
- scrcpy：`bash -n tools/setup-scrcpy.sh` 退出 0，本机 version 3.3.4 vendor 文件 SHA-256 为 8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e，与脚本期望一致。
- A50：用 Node vm 执行 client/public/__manus__/debug-collector.js，注入空监听器、伪 XMLHttpRequest、无操作计时器和 fetch 替身。发起 /api/example?token=fixture-query，头 Authorization: Bearer fixture-auth，JSON 正文 token=fixture-field，等待 clone.text() 后 forceReport。检查提交到 /__manus__/logs 的第一条记录中两个合成值未被脱敏，正文为 [REDACTED]。
- 原工作区 tracked diff SHA-256 仍为 dcd7d2d10e8d85f09de047ed16372326dab5d40d3f169d44780d296144ebc83a。`git diff --check` 退出 0。
