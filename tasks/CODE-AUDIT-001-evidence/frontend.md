# 前端及构建依赖证据

2026-11-16，macOS arm64，本机现有 Node、TS 和 pnpm。静态检查和 React SSR 替身未访问设备或远端服务。

- 通过独立 git ls-files 获取现行文件，对后缀 TS/TSX/JS/MJS 用 TypeScript AST 解析并断言无语法诊断。解析相对路径和 @/、@shared/、@assets/ 别名。忽略纯类型 import/export，纳入可解析字面量动态 import。111 文件、204 条边、0 环、0 计算型 dynamic import。范围是首方可解析边，不含第三方插件、网络加载的脚本或 Python 间接调度。
- 从 client/src/main.tsx 和 server/index.ts DFS，44 个客户端文件可达。通用组件可达 13 个：alert-dialog、button、card、command、dialog、input、popover、scroll-area、slider、sonner、switch、textarea、tooltip。其余 40 个由各文件台账和这组入口的差集确定。
- A51：`pnpm exec tsx -e ...` 导入实际 ThemeProvider 和 react-dom/server 的 renderToString，挂设 localStorage.getItem 抛 fixture-storage-denied。assert.throws 校验 switchable=true；assert.match 校验 switchable=false 的对照输出包含 ready。没有使用真实浏览器隐私配置做复验。

- A52：使用 TypeScript AST 取 Home.tsx 中 refreshCore 的 useCallback 第一个参数，通过 ts.transpileModule 编译、Node vm 执行回调。替身 requestApi 第一次状态返回 running，series?limit=180 等待手动 resolve。第二次状态 completed，series?full=true 立即返回并设置完成。放行旧 series 后，断言状态变回 running。全程同一个 activeSessionIdRef 无网络。
