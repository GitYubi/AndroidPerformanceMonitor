import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { loadConfig } from "./config.mjs";
import { validateJob, executeStep, substitute } from "./protocol.mjs";

// stdout is exclusively JSONL. SDK diagnostics may contain prompts/credentials.
const emit = event =>
  process.stdout.write(
    JSON.stringify({ protocol: 1, time: Date.now(), ...event }) + "\n"
  );
console.log =
  console.info =
  console.warn =
  console.error =
  console.debug =
    () => {};
let agent;
let currentStep;
let navigation;
let terminal = false;
let watchdog;
const finish = (state, extra = {}) => {
  if (terminal) return;
  terminal = true;
  emit({ type: "finished", state, ...extra });
};
process.on("SIGTERM", () => {
  finish("cancelled");
  process.exit(130);
});
process.on("SIGINT", () => {
  finish("cancelled");
  process.exit(130);
});

try {
  loadConfig();
  const job = validateJob(
    JSON.parse(readFileSync(process.argv[2] || 0, "utf8"))
  );
  watchdog = setTimeout(
    () => {
      finish("error", { message: "任务达到总超时" });
      process.exit(124);
    },
    Math.min(3600, Math.max(10, job.timeoutSeconds || 900)) * 1000
  );
  const needsModel = job.testCase.steps.some(
    s =>
      s.enabled !== false &&
      (["action", "input", "wait", "assert"].includes(s.type) ||
        (s.type === "tap" && !s.selector) ||
        (s.type === "explore" && s.exploration?.visualAssertion))
  );
  if (needsModel && !process.env.MIDSCENE_MODEL_API_KEY?.trim())
    throw new Error("缺少 MIDSCENE_MODEL_API_KEY，请在本机配置");
  const root = resolve(job.outputDir || "midscene_run");
  mkdirSync(root, { recursive: true });
  process.chdir(root);
  const { runCase } = await import("./framework.mjs");
  const { exploreTree } = await import("./explore.mjs");
  const { createTreeDevice } = await import("./hierarchy.mjs");
  const treeDevice = createTreeDevice(job.serial);
  // Sensitive input is omitted from our events. Disable SDK report for these runs,
  // because SDK reports may retain raw inputs; screenshots are also suppressed.
  const sensitive = Boolean(
    job.sensitive || job.testCase.parameters?.some(p => p.secret)
  );
  if (needsModel) {
    const { AndroidAgent, AndroidDevice } = await import("@midscene/android");
    const device = new AndroidDevice(job.serial);
    if (!sensitive && job.navigation) {
      const { NavigationMemory, attachNavigationObserver } = await import(
        "./navigation.mjs"
      );
      navigation = new NavigationMemory({
        ...job.navigation,
        device: treeDevice,
        emit: event => emit({ ...event, stepId: currentStep }),
      });
      attachNavigationObserver(device, navigation);
    }
    agent = new AndroidAgent(device, {
      generateReport: !sensitive,
      autoPrintReportMsg: false,
      reportFileName: "execution",
      cache: false,
    });
    let actionCount = 0;
    agent.addProgressListener(event => {
      if (
        sensitive ||
        event.scope !== "aiAct" ||
        event.phase !== "action_done" ||
        !currentStep ||
        ++actionCount > 300
      )
        return;
      const action = event.data?.action;
      if (!action || typeof action.name !== "string") return;
      const parameters = {};
      if (action.param && typeof action.param === "object") {
        for (const key of ["value", "text", "key", "direction", "distance"]) {
          if (["string", "number"].includes(typeof action.param[key]))
            parameters[key] = String(action.param[key]).slice(0, 8000);
        }
      }
      emit({
        type: "action",
        stepId: currentStep,
        state: "passed",
        action: action.name.slice(0, 100),
        target:
          typeof action.target === "string" ? action.target.slice(0, 8000) : "",
        parameters,
      });
    });
    await device.connect();
  }
  emit({ type: "started", state: "running" });
  const state = await runCase(
    job.testCase,
    async step => {
      currentStep = step.id;
      emit({ type: "step", stepId: step.id, state: "running" });
      // Exit the worker on deadline: Promise.race alone would leave UI actions running.
      const deadline = setTimeout(() => {
        emit({
          type: "step",
          stepId: step.id,
          state: "error",
          message: "步骤超时，执行进程已终止",
        });
        finish("error");
        process.exit(124);
      }, step.timeoutMs);
      try {
        const result = ["explore", "scan"].includes(step.type)
          ? await exploreTree(agent, treeDevice, step, event => {
              const { tree, ...summary } = event;
              if (tree && !sensitive) {
                summary.artifact = `tree-${tree.fingerprint}.json`;
                writeFileSync(
                  resolve(root, summary.artifact),
                  JSON.stringify(tree)
                );
              }
              emit({ ...summary, stepId: step.id });
            })
          : navigation && step.type === "action"
            ? await navigation.run(
                substitute(step.prompt, job.parameters || {}),
                () =>
                  executeStep(agent, step, job.parameters || {}, treeDevice),
                name =>
                  agent.aiBoolean(
                    `当前是否已到达用户要求的“${name}”页面？只检查当前画面，不执行操作。`
                  )
              )
            : await executeStep(agent, step, job.parameters || {}, treeDevice);
        let screenshot;
        if (!sensitive) {
          try {
            screenshot = `${step.id}.png`;
            const png = execFileSync(
              "adb",
              ["-s", job.serial, "exec-out", "screencap", "-p"],
              {
                timeout: 10000,
                maxBuffer: 32 * 1024 * 1024,
                stdio: ["ignore", "pipe", "ignore"],
              }
            );
            writeFileSync(resolve(root, screenshot), png);
          } catch {
            screenshot = undefined;
          }
        }
        emit({ type: "step", stepId: step.id, state: result, screenshot });
        return result;
      } catch {
        // Never echo SDK errors, which can include raw input or authorization headers.
        emit({
          type: "step",
          stepId: step.id,
          state: "error",
          message: "执行异常：请检查设备、模型配置及页面状态",
        });
        return "error";
      } finally {
        clearTimeout(deadline);
      }
    },
    step => emit({ type: "step", stepId: step.id, state: "skipped" })
  );
  await agent?.destroy();
  finish(state, { report: sensitive ? null : agent?.reportFile || null });
  process.exitCode = ["passed", "needs_review"].includes(state) ? 0 : 1;
} catch (error) {
  if (agent) {
    try {
      await agent.destroy();
    } catch {}
  }
  // Only pre-SDK validation errors have actionable safe text.
  const message =
    !agent &&
    /^(缺少|必须|用例|步骤|不支持|输入|enabled|失败|指定)/.test(error.message)
      ? error.message
      : "执行器初始化失败，请检查依赖、配置和设备";
  finish("error", { stepId: currentStep, message });
  process.exitCode = 1;
} finally {
  clearTimeout(watchdog);
}
