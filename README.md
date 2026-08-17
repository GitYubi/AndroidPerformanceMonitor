# Android 车机性能监测工具

这是一个**在测试主机本地运行**的前后端分离性能采集工具。它通过已授权的 ADB 连接 Android 车机，最长连续采样 60 分钟，可分别开启 CPU、Memory 和 SurfaceFlinger 帧率模块，并把原始会话数据存为本机 SQLite 文件。前端提供实时曲线、平均值与峰值汇总、进程排行及 CSV 导出。

> 本工具不是安装到车机中的 APK。它适合部署在连接车机 USB 或 TCP/IP ADB 的测试电脑、台架工控机或 CI 测试主机上。

## 已实现能力

| 模块 | 已实现内容 | 默认行为 |
| --- | --- | --- |
| CPU | 调用 `top -b -n 1` 与 `dumpsys cpuinfo`，解析整体 CPU 与进程 CPU；汇总平均值、峰值与进程排行 | 每 1 秒一次；多核 `top` 输出优先按“总容量 − idle”换算整体利用率。 |
| Memory | 调用 `dumpsys meminfo`，记录按进程汇总的 PSS/RSS 及其聚合值 | 以 KiB 落盘，界面以 MiB 显示；单独缺失 RSS 时 PSS 继续工作。 |
| FPS | **多数据源自动降级**：FrameTimeline → framestats → SurfaceFlinger `--latency`，估算 layer 呈现帧率并输出逐帧 Jank / P95 / P99 帧耗时 | 启动时探测 SDK 版本与数据源可用性，采样中失败自动降级；未选定 layer 时自动选择候选 layer。 |
| 实时界面 | 单独开关三种模块、动态显示对应轨迹带、最近 180 个数据点窗口、会话进度与错误事件 | 新曲线出现时旧曲线自动压缩；未启用模块不渲染图表区。 |
| 本地存储 | 会话级 SQLite、WAL、事件表、CSV 导出、进程细目 | 一次只运行一个会话；最长 3,600 秒；异常退出时下次启动标记为 `interrupted`。 |

Android 官方文档说明，ADB 是运行在开发主机与设备端 `adbd` 之间的客户端—服务端工具；当存在多个设备时，需要使用序列号指定目标。本工具将此序列号附加到每条采样命令，避免误采其他设备。[1] `dumpsys` 输出会随 Android 版本和厂商定制变化，因此后端采用容错解析；某个模块无法取数时会记入事件表，但不会主动停止其他模块。[2]

## 工程结构

```text
android-car-performance-monitor/
├── client/                       # React + TypeScript 实时控制台
├── backend/
│   ├── app/                       # FastAPI、ADB 执行、解析、采样与 SQLite 数据层
│   ├── data/<session-id>/         # 运行时生成：每会话 monitor.db / metadata.json
│   └── tests/                     # 解析器单元测试
├── docs/ARCHITECTURE.md           # 架构、数据模型、API 契约和内存策略
├── ideas.md                       # 已选界面设计语言与品牌规则
└── run-local.sh                   # Unix/Linux 一键本地启动入口
```

## 本地运行

当前工程已在 Ubuntu 环境中通过 Python 3.12、Node 22、ADB Platform-Tools 的构建和解析测试。运行前，需要在**实际连接车机的主机**上安装以下运行时。

| 依赖 | 建议版本 | 用途 |
| --- | --- | --- |
| Android SDK Platform-Tools | 最新稳定版 | 提供 `adb` 命令。 |
| Python | 3.11 或更高 | 运行本地 FastAPI 采样后端。 |
| Node.js | 20 或更高 | 编译和运行 React 控制台。 |
| pnpm | 10 或更高 | 安装前端依赖。 |

在 Linux 或 macOS 的项目根目录执行：

```bash
chmod +x run-local.sh
./run-local.sh
```

脚本会验证 `adb`、创建 Python 虚拟环境、安装后端依赖、在 `127.0.0.1:8090` 启动后端，并在 `127.0.0.1:3000` 启动前端。然后访问终端显示的本地地址。

> 后端默认端口为 8090；若与本机其他服务冲突，可通过环境变量覆盖：`BACKEND_PORT=8091 ./run-local.sh`。

在 Windows 上，请分别启动两个终端；PowerShell 示例：

```powershell
# 终端 A：后端
cd backend
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
$env:BACKEND_PORT = "8090"   # 端口冲突时可改为其他值
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port $env:BACKEND_PORT

# 终端 B：前端（项目根目录）
pnpm install
$env:VITE_BACKEND_URL = "http://127.0.0.1:8090"   # 与上面端口保持一致
pnpm dev --host 127.0.0.1
```

## 连接车机并开始测试

首先在车机的开发者选项中开启调试，并在车机弹窗上授权测试主机的 RSA 指纹。Android 官方文档给出了 USB 调试、无线调试与 `adb devices -l` 查询设备状态的标准流程。[1]

```bash
adb devices -l
# 期望状态包含：<serial> device ...
```

随后在控制台中选择状态为 `device` 的设备，配置 5/15/30/60 分钟窗口、0.5–5 秒采样间隔和需要的指标，点击“开始连续采样”。手动点击“停止并汇总”或达到 60 分钟后，后端会关闭任务并写入各项平均值与峰值。

## 帧率数据源兼容矩阵

车机 Android 版本在 10–12 之间不确定时，帧率模块会**启动时探测 SDK 版本与数据源可用性，采样中失败自动降级**，保证任意版本都有可用读数。控制台在配置面板会显示探测结果与将使用的数据源。

| 优先级 | 数据源 | Android 版本 | 提供维度 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | FrameTimeline（`dumpsys SurfaceFlinger --frametimeline -all`） | 12+ (API 31+) | 显示帧呈现节奏、卡顿分类（App/SurfaceFlinger/DisplayHAL 原因） | 逐帧 jank 判定最贴近感知；SurfaceView 路径不受支持时会自动降级。 |
| 2 | framestats（`dumpsys gfxinfo <pkg> framestats`） | 7+ (API 24+) | 应用逐帧渲染/呈现时间戳、输入延迟 | 覆盖 View/Canvas 渲染路径；需前台应用正在产生帧。 |
| 3 | SurfaceFlinger `--latency <layer>` | 4.2+ (API 17+) | 单 layer 呈现节奏 | 全版本兜底；静态画面读数会保持不变。 |
| 4 | gfxinfo 累计计数器 | 4.2+ (API 17+) | 渲染 FPS / Jank 增量 | 最后兜底；随 `app_render_fps` 输出。 |

每个采样点都会记录实际使用的数据源（`frame_source` 列），并随 CSV 导出；数据源切换会写入会话事件。逐帧统计（`frame_count` / `jank_count` / `jank_pct` / `avg_frame_time_ms` / `p95_frame_time_ms` / `p99_frame_time_ms` / `input_latency_ms`）只在逐帧源可用时落盘。

> 帧耗时口径：framestats 采用 `FrameCompleted − IntendedVsync`，卡顿判定为耗时超过两倍帧间隔（与 gfxinfo 的 Janky frames 口径一致）；FrameTimeline 直接采用其显示帧级 Jank Type。

## 数据口径与限制

| 项目 | 口径 | 注意事项 |
| --- | --- | --- |
| CPU 整体 | `top` 总体 CPU 行；多核总容量样式取总量减 idle | 各 OEM `top` 列和总览格式不同，界面会提示不可用，而非伪造数值。 |
| CPU 进程 | `top` 进程列为主，`dumpsys cpuinfo` 补充 | 高并发短进程可能出现在相邻周期中的不同采样。 |
| PSS/RSS | `dumpsys meminfo` 的按进程 PSS/RSS 段落加总 | PSS、RSS 的含义和可用列依车机 Android 版本与服务权限而异。 |
| 呈现 FPS | FrameTimeline 显示帧呈现时间 / framestats `DisplayPresentTime` / SF `--latency` present 时间戳 | 是显示 layer 的呈现速率估计，不等同于应用渲染线程耗时或全链路掉帧率。SurfaceFlinger 的职责是组合并发送显示 buffer，围绕显示刷新节奏工作。[3] |
| 逐帧 Jank | 逐帧源窗口内的卡顿帧占比（超过两倍帧间隔） | 是窗口内“卡顿密度”的估计；单次采样窗口内的帧数取决于采样间隔，1 秒间隔下每点约 30–120 帧。 |

## 内存与可靠性设计

工具不会把 60 分钟历史采样不断放在内存中。后端每个周期直接写入会话 SQLite 数据库，运行内存只保留当前任务与短窗口；前端只渲染最近 180 点。每条 ADB 子进程均带 3–8 秒超时，单模块故障写入事件表后继续下一个周期。启动时会恢复扫描遗留的 `running` 会话并改标为 `interrupted`，从而保留崩溃前已写入的数据。

## 需要您确认的事项

在接入真实车机前，请确认下列会影响采集覆盖率的条件。

| 待确认项 | 为什么需要确认 |
| --- | --- |
| 车机 Android 版本、OEM、SoC 与是否 root | 影响 `top` 文本格式、`meminfo` 的 RSS 段落以及 SurfaceFlinger 权限。 |
| 调试连接方式 | USB ADB 最适合台架；若使用网络 ADB，请确认车机与测试主机网络隔离和调试授权策略。 |
| 帧率测试对象 | 首版按一个 SurfaceFlinger layer 采样，已实现 FrameTimeline → framestats → SF latency 自动降级；请确认需要全屏合成、指定应用，或多个 layer 分别记录。 |
| 数据保留策略 | 当前已设计为按会话目录删除，默认建议“30 天或最近 30 会话”；如需接入 CI/测试平台，需要另行定义上传和鉴权方式。 |

## 验证结果

| 验证项 | 结果 |
| --- | --- |
| 后端 CPU、Memory、FPS 文本解析测试（含 framestats / FrameTimeline / SF latency 统计） | 9 项通过。 |
| 前端 TypeScript 静态检查 | 通过。 |
| 前端生产构建 | 通过。 |
| 本地 API 健康检查 | 通过；无连接设备时控制台保持空状态。 |
| 桌面视觉检查 | 已完成，覆盖侧栏控制、弹性图表空态、汇总卡、进程表与事件区。 |

> FrameTimeline 文本解析基于 AOSP `FrameTimeline.cpp` 的 dump 格式实现；请在真实 Android 12 车机上通过 `/api/frame-capabilities` 与会话事件验证实际输出。framestats 解析按列头索引取值，兼容 Android 7–12 的列数差异。

## References

[1] [Android Debug Bridge (adb) — Android Developers](https://developer.android.com/tools/adb)  
[2] [dumpsys — Android Developers](https://developer.android.com/tools/dumpsys)  
[3] [SurfaceFlinger and WindowManager — Android Open Source Project](https://source.android.com/docs/core/graphics/surfaceflinger-windowmanager)

