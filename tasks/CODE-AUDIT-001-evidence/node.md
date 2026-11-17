# Node UI 执行证据

2026-11-16，macOS arm64，使用仓库已有 Node 和 Python 解释器。未连接设备、CAN 或模型服务。

- 回归：`node --test tools/ui-test/*.test.mjs`，40 项通过。
- A13：构造用例，声明字符串参数 gate。以 gate 等于 true 为 If 条件，仅在真分支把数字 7 赋给 cached。紧随 If 的外层断言检查 cached 等于 7。独立用 app.ui_testing.TestCase 构造成功；以 gate=false、空 serial 和临时 outputDir 作为 stdin JSON 调用 `node tools/ui-test/runner.mjs`。运行器发出 choose 通过且条件不成立、save skipped、after error「变量缺少运行值」、finished error，退出 1。
- A49：用 `node --input-type=module` 导入 exploreTree，向根树提供一个可点击 Details 节点，白名单只允许 fixture 包。为设备替身设置 read 在点击前返回稳定根树、点击后抛出 post-tap observation failure，tap 将 moved 设为 true，back 计数。断言 promise 拒绝、moved=true、back=0；仅收到 graph-page 事件。
- 锁文件：使用 Node 内置断言，首层 dependencies、engines 与 package.json 一致。457 个依赖版本条目均含 integrity 并使用 npm registry，lockfileVersion=3。
