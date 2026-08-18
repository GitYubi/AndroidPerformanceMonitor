# Android 车机性能监测工具

这是一个**在测试主机本地运行**的前后端分离性能采集工具。它通过已授权的 ADB 连接 Android 车机，最长连续采样 60 分钟，可分别开启 CPU、Memory 和帧率模块，把原始会话数据存为本机 SQLite 文件；并在测试结束时自动导出车机 ANR / Crash / Tombstone 日志。前端提供实时曲线、平均值与峰值汇总、进程排行、采样倒计时、历史会话查看与 CSV 导出。

> 本工具不是安装到车机中的 APK。它适合部署在连接车机 USB 或 TCP/IP ADB 的测试电脑、台架工控机或 CI 测试主机上。

## 已实现能力

| 模块 | 已实现内容 | 默认行为 |
| --- | --- | --- |
| CPU | 调用 `top -b -n 1` 与 `dumpsys cpuinfo`，解析整体 CPU 与进程 CPU；汇总平均值、峰值与进程排行 | 采样间隔默认 0.5 秒；多核 `top` 输出优先按“总容量 − idle”换算整体利用率。 |
| Memory | 调用 `dumpsys meminfo`，记录按进程汇总的 PSS/RSS 及其聚合值 | 以 KiB 落盘，界面以 MiB 显示；单独缺失 RSS 时 PSS 继续工作。 |
| FPS | **多数据源自动降级**：FrameTimeline → framestats → SurfaceFlinger `--latency`，估算呈现帧率并输出逐帧 Jank / P95 / P99 帧耗时、应用渲染空闲态（虚线保持） | 启动时探测 SDK 版本与数据源可用性，采样中失败自动降级；未选定 layer 时自动选择候选 layer。 |
| 设备日志导出 | 测试结束时自动拉取车机 ANR / Crash / Tombstone 日志到本地，并清理车机日志（保留目录） | 导出到 `DevicesLogs/<结束时间>/ANR|Crash|Tombstone/`；路径留空则不导出该类型。 |
| 历史会话 | 本地 `data/` 目录全部历史会话的查看（曲线/事件/进程/汇总）、CSV 导出与外部 `monitor.db` 导入 | 会话目录按 `<设备号>_<日期>` 命名；导入文件自动校验并去冲突。 |
| 实时界面 | 单独开关三种模块、动态轨迹带、最近 180 点窗口、会话进度、**采样倒计时**、错误事件与导出结果 toast | 侧边栏各分区可折叠且状态持久化；新曲线出现时旧曲线自动压缩。 |
| 本地存储 | 会话级 SQLite、WAL、事件表、CSV 导出、进程细目 | 一次只运行一个会话；最长 3,600 秒；前端刷新后自动恢复接管运行中会话。 |

Android 官方文档说明，ADB 是运行在开发主机与设备端 `adbd` 之间的客户端—服务端工具；当存在多个设备时，需要使用序列号指定目标。本工具将此序列号附加到每条采样命令，避免误采其他设备。[1] `dumpsys` 输出会随 Android 版本和厂商定制变化，因此后端采用容错解析；某个模块无法取数时会记入事件表，但不会主动停止其他模块。[2]

## 工程结构

```text
android-car-performance-monitor/
├── client/                       # React + TypeScript 实时控制台
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口与 REST 接口
│   │   ├── monitor.py             # 采样会话编排（多源降级、日志导出触发）
│   │   ├── adb.py                 # ADB 执行与文本解析器
│   │   ├── frame_sources.py       # 帧率数据源能力探测与自动降级
│   │   ├── device_logs.py         # ANR/Crash/Tombstone 导出与清理
│   │   ├── storage.py             # SQLite 会话数据层（含导入）
│   │   └── interaction.py         # Perfetto 交互诊断
│   ├── data/<设备号>_<日期>[_序号]/  # 运行时生成：每会话 monitor.db / metadata.json
│   ├── DevicesLogs/<结束时间>/     # 运行时生成：导出的车机日志（ANR/Crash/Tombstone）
│   └── tests/                     # 解析器与存储层单元测试
├── docs/ARCHITECTURE.md           # 架构、数据模型、API 契约和内存策略
├── ideas.md                       # 界面设计语言与品牌规则
└── run-local.sh                   # Unix/Linux 一键本地启动入口
```

## 环境要求

当前工程已在 Ubuntu 环境通过 Python 3.12、Node 22、ADB Platform-Tools 的构建和解析测试。运行前，需要在**实际连接车机的主机**上安装以下运行时。

| 依赖 | 建议版本 | 用途 |
| --- | --- | --- |
| Android SDK Platform-Tools | 最新稳定版 | 提供 `adb` 命令。 |
| Python | 3.11 或更高 | 运行本地 FastAPI 采样后端。 |
| Node.js | 20 或更高 | 编译和运行 React 控制台。 |
| pnpm | 10 或更高 | 安装前端依赖。 |

## 本地运行

在 Linux 或 macOS 的项目根目录执行：

```bash
chmod +x run-local.sh
./run-local.sh
```

脚本会验证 `adb`、创建 Python 虚拟环境、安装后端依赖、在 `127.0.0.1:8090` 启动后端，并在 `127.0.0.1:3000` 启动前端。然后访问终端显示的本地地址。

在 Windows 上，请分别启动两个终端；PowerShell 示例：

```powershell
# 终端 A：后端
cd backend
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8090

# 终端 B：前端（项目根目录）
pnpm install
$env:VITE_BACKEND_URL = "http://127.0.0.1:8090"
pnpm dev --host 127.0.0.1
```

## 配置方式

### 1. 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BACKEND_PORT` | `8090` | 后端监听端口。与本地其他服务冲突时改为其他值（`BACKEND_PORT=8091 ./run-local.sh`）。 |
| `VITE_BACKEND_URL` | `http://127.0.0.1:8090` | 前端调用后端的地址；`run-local.sh` 会自动随 `BACKEND_PORT` 设置，手动起前端时需要与后端端口保持一致。 |

CORS 已放行 `localhost` / `127.0.0.1` 任意端口（后端本身仅绑定回环地址，不暴露给外部）。

### 2. 前端控制台配置（会话骨架侧边栏）

启动后先在**会话骨架**侧边栏完成配置（各分区可点击标题折叠，折叠状态会保存）：

| 分区 | 配置项 | 说明 |
| --- | --- | --- |
| 目标车机 | 设备下拉框 | 选择状态为 `device` 的设备；刷新按钮重新扫描 ADB 设备。 |
| 采样窗口 | 时长 5/15/30/60 分钟、采样间隔 0.5/1/2/5 秒 | 间隔越小曲线越细、操作期帧率越接近真实；默认 0.5s。 |
| 采样模块 | CPU / Memory / 帧率 开关 | 至少启用一项；未启用模块不渲染图表区。 |
| 帧率数据源 | SurfaceFlinger layer 选择（可选） | 默认自动选择活跃 layer；下方自动显示设备支持的帧率数据源（FrameTimeline / framestats / SF latency）与推荐源。 |
| 设备日志导出 | ANR / Crash / Tombstone 路径、导出根目录 | 见下文“设备日志导出”。路径配置会**持久化到浏览器本地**，下次打开自动回填。 |
| 交互诊断 | Perfetto 诊断时长 | 录制真实操作 trace，可下载 `.pftrace` 文件离线分析。 |

### 3. 配置持久化

浏览器 `localStorage` 会保存以下内容，刷新页面不丢失：

- 设备日志路径配置（ANR / Crash / Tombstone / 导出根目录）
- 侧边栏各分区折叠状态

## 连接车机并开始测试

首先在车机的开发者选项中开启调试，并在车机弹窗上授权测试主机的 RSA 指纹。Android 官方文档给出了 USB 调试、无线调试与 `adb devices -l` 查询设备状态的标准流程。[1]

```bash
adb devices -l
# 期望状态包含：<serial> device ...
```

随后在控制台中选择设备、配置采样窗口与指标，点击**开始连续采样**。采样过程中：

- 曲线区实时展示最近 180 个采样点；右上角显示**倒计时**与剩余采样时间
- 应用渲染（R 线）在应用空闲（静态界面不渲染）时以**虚线保持**最后一次活跃值，操作时恢复实线
- 每个采样点记录实际使用的帧率数据源（`frame_source`）与逐帧 Jank / P95 统计

手动点击**停止并汇总**或达到时长上限后，后端关闭任务并写入平均值与峰值；若配置了设备日志导出，会同步执行导出与清理，结果以 toast 与会话事件提示。

## 设备日志导出

测试结束时（手动停止或自然到期）自动执行：

1. 读取配置的车机路径（如 `/data/anr`、`/data/crash`、`/data/tombstones`），`ls` 检测是否有文件；
2. 有文件 → `adb pull` 到本地 `<导出根目录>/<结束时间 yyyy_MM_DD-hh_mm_ss>/ANR|Crash|Tombstone/`；
3. 导出成功后删除车机内对应日志文件（`rm -f <路径>/*`），**保留目录**；
4. 无文件、路径不存在或无权限时写入事件并跳过该类型，互不影响；导出结果弹 toast 提示。

> 配置留空的类型不导出（仅提示）。`/data/anr`、`/data/tombstones` 在部分车机上仅 root 可写，但**读取**通常可用；若被拒绝，会以 warning 事件记录并跳过。默认导出根目录为项目根目录下的 `DevicesLogs`，已在 `.gitignore` 中排除。

## FPS 数据链路巡检

排查"车机哪个界面拿不到 FPS 信息"的独立界面工具。后端启动后访问：

```
http://127.0.0.1:8090/fps-probe
```

**不自动操作**：你自己在车机上打开目标界面并操作（滑动/点击触发渲染），页面每秒检测并显示当前前台包名，以及三个参数是否可测：

| 参数 | 判定 | 显示 |
| --- | --- | --- |
| R · 应用渲染 | gfxinfo 计数器在操作时是否增长（增量判定） | ✓ / ✗ |
| P · 呈现帧率 | 逐帧数据源（FrameTimeline / framestats）是否产出呈现 FPS | ✓ / ✗ |
| J · 逐帧 Jank | 逐帧数据源是否有帧数据（frame_count > 0） | ✓ / ✗ |

每个参数旁附实时详情（计数增量、当前 FPS、帧数与 Jank 占比）。典型结果解读：

- **R ✗ 且操作后持续 ✗**：该界面渲染不经过 gfxinfo（纯合成动画如桌面滑动、SurfaceView 视频），R 线拿不到属正常；
- **R ✓**：走 View/HWUI 渲染，实时 R 曲线可用；
- **P / J ✗**：无可用逐帧数据源（权限受限或设备不支持）。

> 页面由后端直接返回（自包含 HTML + 1s 轮询），不依赖前端构建；巡检状态按前台包名自动重置。

## 历史会话

- 点击状态栏的**历史会话**按钮，可查看本地 `data/` 目录中全部会话（时间、设备、状态、时长、样本数），选择"查看"后曲线、进程排行、事件与汇总随之加载，并可导出 CSV。
- 会话目录按 `<设备号>_<日期>[_序号]` 命名（如 `42b86e9c_2026_08_17_2`），方便在文件系统中定位；旧版 UUID 目录同样兼容。
- **导入**：点击弹窗中的文件选择器，可导入外部 `monitor.db`（例如从其他测试机器拷贝的会话文件）；导入会校验文件合法性，session_id 冲突时自动生成新 ID。

## 帧率数据源兼容矩阵

车机 Android 版本在 10–12 之间不确定时，帧率模块会**启动时探测 SDK 版本与数据源可用性，采样中失败自动降级**，保证任意版本都有可用读数。控制台在配置面板会显示探测结果与将使用的数据源。

| 优先级 | 数据源 | Android 版本 | 提供维度 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | FrameTimeline（`dumpsys SurfaceFlinger --frametimeline -all`） | 12+ (API 31+) | 显示帧呈现节奏、卡顿分类（App/SurfaceFlinger/DisplayHAL 原因） | 逐帧 jank 判定最贴近感知；部分 OEM（如三星）present 时间与 Jank Type 不可用时会自动回退。 |
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
| 应用渲染 FPS | gfxinfo `Total frames rendered` 计数器增量 / 窗口时长 | 静态界面不重绘时计数器不增长，读数回落为 0（界面以虚线保持），属正常现象而非卡顿。 |
| 逐帧 Jank | 逐帧源窗口内的卡顿帧占比（超过两倍帧间隔） | 是窗口内“卡顿密度”的估计；单次采样窗口内的帧数取决于采样间隔，0.5 秒间隔下每点约 15–60 帧。 |

## 内存与可靠性设计

工具不会把 60 分钟历史采样不断放在内存中。后端每个周期直接写入会话 SQLite 数据库，运行内存只保留当前任务与短窗口；前端只渲染最近 180 点。每条 ADB 子进程均带 3–8 秒超时，单模块故障写入事件表后继续下一个周期。启动时会恢复扫描遗留的 `running` 会话并改标为 `interrupted`，从而保留崩溃前已写入的数据；前端刷新页面后会自动恢复接管仍在运行的会话，不会出现"无法停止"或"无法重新开始"的状态。

## 数据保留策略

- 会话数据：`backend/data/<设备号>_<日期>[_序号]/`（`monitor.db` + `metadata.json`），建议按项目周期清理。
- 导出的车机日志：项目根目录 `DevicesLogs/<结束时间>/`（已被 `.gitignore` 排除）。
- 会话历史列表会扫描 `data/` 目录下的所有会话，手动删除目录即可移除对应会话。

## 验证结果

| 验证项 | 结果 |
| --- | --- |
| 后端文本解析与存储层测试（framestats / FrameTimeline / SF latency / 目录命名 / 导入） | 22 项通过。 |
| 前端 TypeScript 静态检查 | 通过。 |
| 前端生产构建 | 通过。 |
| 本地 API 健康检查 | 通过；无连接设备时控制台保持空状态。 |
| 真机验证 | 三星 Android 13 手机、高通 qcm6125 Android 10 车机：帧率多源降级、R 线空闲虚线、设备日志导出均验证通过。 |

> FrameTimeline 文本解析基于 AOSP `FrameTimeline.cpp` 的 dump 格式实现，并在三星固件上做过 OEM 兼容适配；不同车机 ROM 的输出差异可通过 `/api/frame-capabilities` 与会话事件排查。framestats 解析按列头索引取值，兼容 Android 7–12 的列数差异。

## References

[1] [Android Debug Bridge (adb) — Android Developers](https://developer.android.com/tools/adb)  
[2] [dumpsys — Android Developers](https://developer.android.com/tools/dumpsys)  
[3] [SurfaceFlinger and WindowManager — Android Open Source Project](https://source.android.com/docs/core/graphics/surfaceflinger-windowmanager)
