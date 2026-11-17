# 发布多屏 Monkey 便携版

## 任务 ID

RELEASE-MONKEY-001

## 当前状态

COMPLETED

## 目标与非目标

- 目标：将当前多屏 Monkey 版本推送到 GitHub main，发布 Windows x64 ZIP 和 SHA-256。
- 非目标：发布本地实车 DBC、运行日志和测试数据。

## 工作范围

基于 v1.0.2 发布多屏 Monkey 改动，使用仓库的 Windows 便携包流程。

## 逐文件审计台账

无

## 前置依赖

项目构建环境、GitHub 仓库写权限。

## 阶段计划

- [x] S1 输入：当前源码和固定版本运行时。输出：Windows x64 ZIP、SHA-256 和本地验证。完成条件：回归、启动烟测及 ZIP 内容校验通过。恢复：构建日志。
- [x] S2 输入：已验证的源码和便携包。输出：远程 main、版本标签和 Release 资产。完成条件：远程 main 包含发布提交，标签指向该提交，远程资产 SHA-256 与本地一致。恢复：远程分支和发布资产状态。

## 验收标准

- V01 | AUTO | 执行回归，校验 ZIP 内容及启动烟测，确认新版设备执行器在包内。
- V02 | AUTO | 核对远程 main、标签以及 Release 资产和摘要。

## 验证方式及证据

- V01 | PASS | 2026-11-17 | macOS 构建主机 | 后端 248 项及 18 项子测试、Node 48 项、发布工具 18 项测试、Java RunnerTest、pnpm check 和任务校验通过。ZIP 校验 18,998 个清单文件摘要，包内执行器和后端源码与工作区一致。本机启动烟测通过，Windows 可执行文件未在 Windows 主机运行。ZIP 共 148806742 字节，SHA-256 为 540a55d10d46acc4d35c710c870587364b34e239fabdad0fa3f38b950dfb0cf3。
- V02 | PASS | 2026-11-17 | GitHub 远程与 Release API | 确认 main 包含发布提交 `4c74264615b8ad0e7a81bcd57343cfee1d07e5c8`，`v1.0.3` 标签指向该提交。Release 为公开正式版本，ZIP 和 SHA-256 资产已上传。GitHub 返回的两个文件摘要均与本地一致。

## 已完成工作

- 2026-11-17 确认本次从 v1.0.2 基线发布多屏 Monkey 工作区，选择 v1.0.3。
- 2026-11-17 发布 [v1.0.3](https://github.com/GitYubi/AndroidPerformanceMonitor/releases/tag/v1.0.3)，保留本机实车 DBC 与运行数据。功能实车验收状态见 [多屏 Monkey 任务](MONKEY-MULTISCREEN-001.md)。

## 剩余工作

无

## 阻塞原因

无

## 下一步动作

无
