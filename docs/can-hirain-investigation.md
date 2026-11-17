# TestBaseVCI 适配与周期发送调查

调查日期：2026-11-12。代码为第一阶段，尚未完成硬件联调，不能作为验收通过的依据。

## 可核对的来源

- [官方 Python 调用示例](https://intework.hirain.com/docs/she-bei-diao-yong-zhi-shi-yong-python-jue-ben-diao-yong-VCI-she-bei)：文中明确描述上位机发送和接收线程。
- [官方 python-can 适配说明](https://intework.hirain.com/docs/she-bei-diao-yong-zhi-PythonCAN-zu-jian-shi-pei)：示例及平台依赖。页面缓存版本之间内容有差异；本次还检查了 PyPI 上作者为 Jingwei Hirain 的 `python-can-hirain` 0.3.0 wheel：`can_hirain/hrcan.py`、`TestBaseVCI.py`、`BasicStructure.py` 和 `demo/Test_Canfd.py`。包可通过 `python -m pip download --no-deps python-can-hirain==0.3.0` 获取。
- [原 SDK 下载入口](https://intework.hirain.com/docs/VBA-ruan-jian-API-jie-kou-wen-dang-xia-zai) 当前返回 404。未取得 SDK 头文件、DLL 二进制或厂商详细 API 手册，因此没有检查导出表，也无法确定原生 ABI 和设备端执行机制。
- [官方产品说明](https://en.hirain.com/product/578.html) 列出 V60A 两个 CAN FD 端口和 Pro 四个端口；不能以此代替设备枚举或硬件验证。

## 调度证据与结论

0.3.0 的 `HRCANBus.send_periodic` 调用 `can_set_periodic_tx_msgs`，后者构造 `CanPeriodTxInfo`，传入 `H3CANSetPeriodicTxMsgs`；`period=0` 被用于停止。它没有在该 Python 方法内建立周期发送线程；这不证明执行在设备内，也不能排除 DLL 内部线程或系统服务。源码中的 `usPeriod` 为 16 位，python-can 包装把秒乘以 1000，包装还维护 150 条上限；这些是包装实现细节，不等于经过验证的设备限制。实现支持传入 CAN FD、BRS 和多个 ID，但 V60A / Pro 的实际支持、精度和动态更新语义均未验证。停止调用有返回码；包装的单任务 `stop()` 没有检查返回码，不宜把它当成已验证的安全清理方案。

因此 **设备调度能力为 UNVERIFIED**；目前应用选择 **SOFTWARE_SCHEDULED**，不调用该原生周期接口。10/20/50/100/500ms、多任务、动态更新、运行时间和时序精度均未做硬件测试。不能推断闪烁成因。需要厂商手册和独立监听器确认总线时间戳、ACK、终端、负载、编码、丢帧和控制器行为。

## 当前实现和限制

- `backend/can_adapters/hirain.py` 使用厂商 `H3GetDriverConfig` 包装获取实际通道，按 SDK 的 `hw_name` 选择设备；不推测实际型号。通过 `HRCANBus` 进行 CAN/CAN FD 收发，仲裁 500 kbit/s、数据段 2 Mbit/s，终端电阻启用（与现有默认配置一致）。手动连接模式使用现有 DBC 和周期任务；界面提供最多四个通道，实际可用数量以驱动枚举为准。CAN 模式和参数在后端构造函数可配置，但尚未连接至 UI。
- 在 Windows/Linux 安装 `python-can-hirain==0.3.0` 和厂商 TestBaseVCI 驱动后使用；仅模拟测试，尚未在 Windows/Linux 或 V60A 上运行。macOS 明确提示不支持。包为可选依赖，不能替代驱动。供应商适配包内部错误处理和资源释放仍需硬件复核，尤其是断线时。
- `GET /api/can/devices` 和 `backend/tools/can_discover.py` 返回恒润驱动实际枚举结果和错误；当前周立功没有实现可靠枚举，仍使用原有手动连接。UI 可刷新和选择已发现的恒润设备。SDK 以 `hw_name` 分组；发现重复硬件通道号时会拒绝识别，但仍不能保证发现所有重名设备，必须在硬件使用前验证。
- 现有 `zlgCan` 仍承载通用调度和业务，没有完成独立 PeriodicTxManager、统一设备管理和全部通道的重构。服务现在在接收或在线检查失败时停止任务并关闭句柄，允许重新手动连接；尚未完成硬件断线与自动重连验证，Mac USB 在线状态检查也不能保证识别所有断线。请勿在需要无人值守的场景使用。
- CAN 自动化测试包含模拟恒润基本收发、断线清理及周立功原有模拟回归；没有连接任何真实 CAN 设备。发现工具在当前 macOS 报告恒润平台不可用，周立功枚举未实现。没有执行 60 秒、10 分钟或任何真实总线发送测试。

建议先获取完整 SDK 和设备，完善双厂商枚举、通道映射、断线清理及重连，然后在获准的隔离台架使用独立监听设备完成所列周期测试。不得在未经确认的整车网络发送测试帧。

## 独立台架工具

`backend/tools/can_bench.py` 可运行 `single10`、`single20`、`dual`、`triple`、`multi` 和 `update` 场景；默认 60 秒，长测可设 `--duration 600`。示例（仅模拟，不访问总线）：

```sh
backend/.venv/bin/python backend/tools/can_bench.py --backend simulation --scenario update --duration 1 --base-id 0x100 --confirm-safe --output /tmp/can-bench
```

真实测试前须确认目标和 ID 可安全发送。使用 `--listener-interface` 和 `--listener-channel` 可接入独立的 python-can 监听器；监听器由调用者预先配置波特率，必须独立核实其时间戳来源。`host_calls.csv` 只是调用时间，`listener.csv` 才是监听记录；`summary.json` 基于监听时间计算间隔的绝对偏差，无法确定丢帧数量时标记为 null。该程序目前测试软件调度路径，不测试厂商原生周期发送。对于 ACK、负载、终端电阻和 DBC 编码仍须单独检查。
