# Android 车机性能监测工具：工程架构

## 1. 运行形态与安全边界

本工程采用**测试主机本地服务 + 浏览器控制台 + ADB 目标车机**的分离式架构。浏览器只向回环地址上的后端发起请求；后端才负责执行本机 `adb` 命令、解析车机输出以及写入会话文件。这样既符合 ADB 的“开发主机客户端—设备端 adbd”通信模型，也避免把调试能力暴露给局域网或公网。[1]

> 工具不安装到车机中。它运行在已经获得车机调试授权的测试电脑或工控机上，持续采集受选定 ADB 序列号约束的设备数据。

| 组件 | 实现 | 职责 | 生命周期 |
| --- | --- | --- | --- |
| 前端控制台 | React + TypeScript + Recharts | 设备选择、会话控制、实时曲线、历史会话与导出入口 | 用户浏览器页面 |
| 后端服务 | Python 3.12 + FastAPI | ADB 子进程隔离、协议解析、会话编排、REST/SSE 接口 | 测试主机本地进程 |
| 采样器 | `asyncio` 单会话任务 | 以固定节拍采集 CPU、内存、帧率；最长 3,600 秒 | 开始至停止或自动到期 |
| 数据层 | SQLite（每会话独立） | 采样点、进程明细、聚合结果、命令错误和元数据 | 会话结束后长期保留 |
| 文件层 | `data/<session-id>/` | `monitor.db`、`summary.json`、可导出的 CSV | 由保留策略清理 |

## 2. 指标采集策略

`dumpsys` 是 Android 设备端系统服务的诊断输出工具，输出内容会随 Android 版本和 OEM 定制变化，因此解析器设计为“多模式正则 + 原始输出留档 + 指标降级”，不会因单个字段缺失而终止整个会话。[2]

| 指标 | 主命令 | 辅助命令 | 计算口径 | 兼容性与降级 |
| --- | --- | --- | --- |
| CPU 整体与进程 | `adb -s <serial> shell top -b -n 1` | `adb -s <serial> shell dumpsys cpuinfo` | 记录 `top` 总体 CPU 行（可用时）与进程 `%CPU`；`cpuinfo` 用于交叉校验进程视图。整体平均值与峰值只取有效样本。 | 自动识别 Toybox、procps 风格列头；无法读出总体行时，显示“整体 CPU 不可用”，仍保留进程数据。 |
| Memory（系统与应用） | `adb -s <serial> shell dumpsys meminfo` | — | 系统总 PSS、总 RSS 与进程 PSS/RSS，统一以 KiB 存储、前端显示 MiB。 | 若 OEM 输出缺少 RSS，PSS 数据仍照常入库，RSS 标记为不可用。 |
| 显示帧率 | `adb -s <serial> shell dumpsys SurfaceFlinger --latency <layer>` | `dumpsys SurfaceFlinger --list` | 用连续 present 时间戳的正间隔中位数计算 `fps = 1e9 / median(delta)`；同步保留候选 layer 名。 | 前置检查 `--latency` 输出；受 SELinux/厂商限制时，提示当前设备不支持而不中断 CPU、内存采样。 |

SurfaceFlinger 负责合成并发送显示缓冲区，且围绕显示刷新节奏工作；因此帧率模块将其读数表述为“所选显示 layer 的已呈现帧率估计”，而不是应用渲染线程的完整性能结论。[3]

## 3. 会话与数据模型

一次测试会话由 UUID 标识，最长运行 60 分钟。每一个采样周期有唯一时间戳；CPU、Memory、FPS 模块可独立启用或关闭。采样周期默认 1 秒，可在 500–5,000 ms 内配置；所有 ADB 子进程均设置超时且不经 shell 拼接用户输入，避免阻塞和命令注入。

```text
session (1) ──< sample (N)
sample  (1) ──< process_sample (N)
session (1) ──< event (N)
```

| 表 | 关键字段 | 用途 |
| --- | --- | --- |
| `session` | `id`, `serial`, `started_at`, `ended_at`, `duration_limit_s`, `enabled_metrics`, `state` | 会话基本信息与状态机。 |
| `sample` | `id`, `session_id`, `ts_ms`, `cpu_total_pct`, `pss_kb`, `rss_kb`, `fps`, `raw_status` | 每秒一级指标点；仅保存标量。 |
| `process_sample` | `sample_id`, `process_name`, `pid`, `cpu_pct`, `pss_kb`, `rss_kb` | 按进程保存指标明细，支持后续 Top-N 查询。 |
| `event` | `session_id`, `ts_ms`, `severity`, `code`, `message` | 记录超时、权限不足、解析降级和用户停止。 |

## 4. 内存与文件管理

工具将运行时内存与测试数据彻底分开。SQLite 使用 WAL 模式并按每轮采样写入；内存中只保留最近 180 个一级指标点，用于推送和初始曲线渲染。进程样本不在内存累计，写入后即释放。实时订阅队列有上限；若浏览器暂停或网络拥塞，队列会丢弃旧点并保留最新点，而不是无限增长。

| 风险 | 防护措施 |
| --- | --- |
| 60 分钟进程明细膨胀 | 每秒明细直接批量写入 SQLite，接口默认只查询 Top 10 和最近窗口。 |
| 后端子进程卡住 | 每次 ADB 命令采用 3–8 秒超时，失败事件入库；下次节拍继续。 |
| 前端曲线数据持续累积 | 前端以 180 点滑动窗口渲染；历史数据按需分页读取。 |
| 未停止的测试 | 调度器以单调时钟检查 `3,600 s` 上限，触顶后写入汇总并关闭任务。 |
| 异常退出 | 应用启动时扫描 `running` 会话并标记为 `interrupted`，保留已落盘数据。 |
| 文件长期占用 | 采用按会话目录删除的保留策略；默认只保留最近 30 个会话或 30 天，可配置。 |

## 5. 本地 API 契约

后端监听 `127.0.0.1:8080`，由前端使用 `VITE_BACKEND_URL` 配置。首版使用 REST 轮询以降低本地环境中的长连接复杂度；后端同时预留 SSE 流接口，供后续切换为推送模式。

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/health` | 检查后端、ADB 二进制与可用设备数。 |
| `GET /api/devices` | 列出 ADB `device` 状态的设备与基础属性。 |
| `POST /api/sessions` | 创建并启动一个会话；请求体包含 `serial`、时长、间隔、启用模块和可选 SurfaceFlinger layer。 |
| `POST /api/sessions/{id}/stop` | 手动停止当前会话并生成汇总。 |
| `GET /api/sessions/{id}` | 返回会话状态、实时摘要、均值/峰值。 |
| `GET /api/sessions/{id}/series?limit=180` | 返回滑动窗口曲线点与各模块可用性。 |
| `GET /api/sessions/{id}/processes?metric=cpu` | 返回指定时间窗口的进程聚合排名。 |
| `GET /api/sessions/{id}/export?format=csv` | 导出会话级指标与进程指标 CSV。 |

## 6. 实施前提与待确认项

开发版默认支持 USB ADB 与已经连通的 TCP/IP ADB。Android 官方文档说明：多设备同时连接时需要以序列号指定目标设备；本工具会强制在每条 ADB 命令中传入该序列号。[1]

| 项目 | 默认处理 | 需要用户后续确认的内容 |
| --- | --- | --- |
| 目标设备 | 从已授权 ADB 设备列表选择 | 车机 Android 版本、OEM/SoC、是否启用 USB 调试或网络调试。 |
| ADB 权限 | 仅使用 shell 用户可用命令 | 是否需要 root 设备或厂商签名版本以读取受限的 SurfaceFlinger layer。 |
| 帧率口径 | 选定 SurfaceFlinger layer 的呈现帧率 | 是否需要“全屏合成帧率”、特定应用 layer，还是每个 layer 分别记录。 |
| 保存策略 | SQLite + JSON/CSV，30 天或 30 会话 | 数据目录、日志保留时长、是否需要自动上传到测试管理系统。 |

## References

[1] [Android Debug Bridge (adb) — Android Developers](https://developer.android.com/tools/adb)  
[2] [dumpsys — Android Developers](https://developer.android.com/tools/dumpsys)  
[3] [SurfaceFlinger and WindowManager — Android Open Source Project](https://source.android.com/docs/core/graphics/surfaceflinger-windowmanager)
