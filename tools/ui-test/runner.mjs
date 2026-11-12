import { readFileSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { loadConfig } from "./config.mjs";
import { captureElement } from "./element-capture.mjs";
import {
  validateJob,
  executeStep,
  buildStepPlan,
  captureSourceIds,
  isCanStep,
  isCapture,
  requiresModel,
  stepDeadlineMs,
  substitute,
} from "./protocol.mjs";

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
const ipc =
  process.argv[2] === "--ipc"
    ? createInterface({ input: process.stdin })
    : null;
const replies = ipc?.[Symbol.asyncIterator]();
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
    JSON.parse(
      replies
        ? (await replies.next()).value
        : readFileSync(process.argv[2] || 0, "utf8")
    )
  );
  watchdog = setTimeout(
    () => {
      finish("error", { message: "任务达到总超时" });
      process.exit(124);
    },
    Math.min(3600, Math.max(10, job.timeoutSeconds || 900)) * 1000
  );
  const { children, enabled } = buildStepPlan(job.testCase.steps);
  const screenshots = new Map();
  const assigned = new Map();
  const operations = enabled.map(step => step.condition ?? step);
  for (const step of operations) {
    for (const id of captureSourceIds(step)) screenshots.set(id, null);
  }
  if (enabled.some(step => isCanStep(step.type)) && !replies)
    throw new Error("步骤包含 CAN 操作，请通过工作台运行");
  const needsModel = enabled.some(requiresModel);
  if (needsModel && !process.env.MIDSCENE_MODEL_API_KEY?.trim())
    throw new Error("缺少 MIDSCENE_MODEL_API_KEY，请在本机配置");
  const baselineDir = job.baselineDir ? resolve(job.baselineDir) : null;
  const baselinePath = (id, projectId) =>
    (projectId && job.baselineFiles?.[`${projectId}/${id}`]) ||
    (baselineDir && resolve(baselineDir, `${id}.png`));
  for (const step of operations.filter(step => step.options?.baselineId)) {
    const path = baselinePath(step.options.baselineId, step.options.baselineProjectId);
    const file = path && statSync(path, { throwIfNoEntry: false });
    if (!file || !file.isFile() || file.size > 8 * 1024 * 1024)
      throw new Error("步骤缺少有效的 PNG 图片");
  }
  const root = resolve(job.outputDir || "midscene_run");
  mkdirSync(root, { recursive: true });
  process.chdir(root);
  const { runCase } = await import("./framework.mjs");
  const { exploreTree } = await import("./explore.mjs");
  const { createTreeDevice } = await import("./hierarchy.mjs");
  const treeDevice = job.serial ? createTreeDevice(job.serial) : null;
  // Sensitive input is omitted from our events. Disable SDK report for these runs,
  // because SDK reports may retain raw inputs. Explicit captures stay in memory
  // for comparisons, but sensitive runs never write screenshot files.
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
    async (step, variables, occurrence, iterations) => {
      currentStep = step.id;
      const emitStep = event =>
        emit({ type: "step", stepId: step.id, iterations, ...event });
      emitStep({ state: "running" });
      // Exit the worker on deadline: Promise.race alone would leave UI actions running.
      const deadline = setTimeout(() => {
        emitStep({
          state: "error",
          message: "步骤超时，执行进程已终止",
        });
        finish("error");
        process.exit(124);
      }, stepDeadlineMs(step));
      try {
        if (step.type === "for") {
          emitStep({
            state: "passed",
            message: `循环 ${step.options.count} 次`,
          });
          return "passed";
        }
        if (step.type === "else") {
          emitStep({ state: "passed" });
          return "passed";
        }
        if (isCanStep(step.type)) {
          emit({ type: "can_request", stepId: step.id });
          const { value, done } = await replies.next();
          const response = done ? null : JSON.parse(value);
          if (response?.stepId !== step.id) throw new Error("CAN 步骤响应无效");
          const state = response.ok ? "passed" : "error";
          emitStep({
            state,
            message: response.message,
          });
          return state;
        }
        const operation = step.condition
          ? { ...step.condition, timeoutMs: step.timeoutMs }
          : step;
        const parameters = {
          ...job.parameters,
          ...Object.fromEntries(assigned),
          ...variables,
        };
        let capturedImage;
        let message;
        const execute = () =>
          executeStep(agent, operation, parameters, treeDevice, {
            captureImage: async () =>
              (capturedImage ??= await treeDevice.screenshot()),
            captureElement: selector =>
              captureElement(
                job.pythonExecutable ||
                  (process.platform === "win32" ? "python" : "python3"),
                job.serial,
                selector,
                step.timeoutMs
              ),
            onImage: image => {
              capturedImage = image;
            },
            loadBaseline: (id, projectId) => readFileSync(baselinePath(id, projectId)),
            setVariable: (name, value) => assigned.set(name, value),
            sensitive,
            loadScreenshot: id => {
              const capture = screenshots.get(id);
              return typeof capture === "string"
                ? readFileSync(capture)
                : capture;
            },
            onResult: text => {
              message = text;
            },
          });
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
                substitute(step.prompt, parameters),
                execute,
                name =>
                  agent.aiBoolean(
                    `当前是否已到达用户要求的“${name}”页面？只检查当前画面，不执行操作。`
                  )
              )
            : await execute();
        let screenshot;
        if (
          !sensitive &&
          !["delay", "assign", "screenshot_assert", "variable_assert"].includes(
            operation.type
          ) &&
          (!(operation.type === "image_assert" || isCapture(operation.type)) ||
            capturedImage)
        ) {
          try {
            screenshot = `${step.id}${occurrence > 1 ? `.${occurrence}` : ""}.png`;
            const png = capturedImage ?? (await treeDevice.screenshot());
            writeFileSync(resolve(root, screenshot), png);
          } catch {
            screenshot = undefined;
          }
        }
        if (screenshots.has(step.id))
          screenshots.set(
            step.id,
            screenshot ? resolve(root, screenshot) : capturedImage
          );
        let branchMessage;
        if (step.type === "if" && result === "passed") {
          branchMessage = "进入 If 分支";
        } else if (step.type === "if" && result === "failed") {
          branchMessage = children(step.id, "else").some(
            child => child.enabled !== false
          )
            ? "进入 Else 分支"
            : "条件不成立，跳过 If";
        }
        emitStep({
          state: branchMessage ? "passed" : result,
          screenshot,
          message: branchMessage
            ? [branchMessage, message].filter(Boolean).join(" · ")
            : message,
        });
        return result;
      } catch {
        // Never echo SDK errors, which can include raw input or authorization headers.
        emitStep({
          state: "error",
          message: "执行异常：请检查设备、模型配置及页面状态",
        });
        return "error";
      } finally {
        clearTimeout(deadline);
      }
    },
    (step, iterations) =>
      emit({ type: "step", stepId: step.id, state: "skipped", iterations })
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
  ipc?.close();
}
