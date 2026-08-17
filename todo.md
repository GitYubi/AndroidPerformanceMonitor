# 性能采集修复待办

- [x] 获取模拟器 top、dumpsys meminfo、dumpsys SurfaceFlinger 的实际输出并复核口径。
- [x] 将 CPU 总占用按逻辑核数归一化为 0–100%。
- [x] 采集总内存容量并计算 PSS/RSS 的具体值与占系统总内存百分比。
- [x] 增加 SurfaceFlinger layer 选择、命令诊断和可用回退方案。
- [x] 在模拟器完成采集验证、构建检查并更新说明。

- [x] 将 RSS 汇总改为仅展示具体数值，并保持 PSS 的内存占比展示。
- [x] 将 FPS 汇总的峰值字段改为最低值，并同步调整页面文案。
- [x] 在应用切换时重新匹配活动 SurfaceFlinger layer，避免固定主页 layer 导致 FPS 空值。

- [x] 读取 CSV 导出时的后端异常与会话数据库结构。
- [x] 修复 CSV 序列化对新旧采样字段的兼容处理。
- [x] 在模拟器会话中验证下载的 CSV 内容和响应头。

- [x] 抓取设置页前台 Activity、候选 SurfaceFlinger layer 与延迟时间戳。
- [x] 比对滚动时各候选 layer 的有效 present 时间戳，并确定内容 layer 优先级。
- [x] 修复自动 layer 选择或添加有效 layer 回退后复测 FPS。

- [x] 探测 Android API、Perfetto FrameTimeline 与 gfxinfo framestats 的可用性。
- [x] 实现前台应用识别、渲染 FPS/Jank 采集和 SurfaceFlinger 呈现 FPS 的并行采样。
- [x] 扩展会话数据模型、SQLite 存储、CSV 导出和汇总统计。
- [x] 更新控制台以区分渲染 FPS、呈现 FPS、Jank 和采集降级原因。
- [x] 在 Android 11 模拟器执行降级验证，并完成构建检查。

- [x] 对比真实会话 JSON 样本与前端图表数据键。
- [x] 修复双曲线的数值转换、Y 轴域或折线配置。
- [x] 用模拟器会话验证渲染 FPS 和呈现 FPS 两条线均显示。

- [x] 从用户包名清单筛选 Android 原生与系统关键组件。
- [x] 生成 Monkey 可读取的黑名单文件。
- [x] 核验黑名单参数与 1 秒间隔点击/滑动 Monkey 命令。

- [x] 读取已连接车机的 Android 版本（SDK API level）与图形服务能力。
- [x] 探测 FrameTimeline、SurfaceFlinger、framestats 与 gfxinfo 的可用性（`/api/frame-capabilities`）。
- [x] 实现 FrameTimeline → framestats → SF latency 的多源自动降级采样与逐帧 Jank/P95 统计。
- [ ] 在 Android 12 模拟器或真实车机验证 FrameTimeline dump 的实际输出格式并复核解析。
- [ ] 验证低风险只读诊断命令的权限和输出格式。
- [ ] 输出该车机可实施的交互驱动帧性能方案。

- [x] 定义交互诊断会话、trace 文件与结果摘要的数据结构。
- [x] 实现本地后端 Perfetto 交互 trace 采集、状态查询和文件导出接口。
- [x] 在控制台加入启动诊断、诊断时长、状态和结果摘要展示。
- [ ] 在当前 Android 11 车机验证真实操作 trace 的采集和下载流程。
