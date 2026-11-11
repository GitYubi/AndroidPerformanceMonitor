import { matchingNodes } from "./hierarchy.mjs";

export const STEP_TYPES = [
  "action",
  "tap",
  "input",
  "wait",
  "assert",
  "screenshot",
  "explore",
  "scan",
  "settings",
  "back",
  "adb_tap",
  "adb_swipe",
  "element_assert",
  "image_assert",
];
export const isAssertion = type =>
  ["assert", "element_assert", "image_assert"].includes(type);
export const requiresModel = step =>
  ["action", "input", "wait", "assert"].includes(step.type) ||
  (step.type === "tap" && !step.selector) ||
  (step.type === "explore" && Boolean(step.exploration?.visualAssertion));

const validInteger = (value, min, max) =>
  Number.isInteger(value) && value >= min && value <= max;
const elementStates = [
  "exists",
  "absent",
  "enabled",
  "disabled",
  "checked",
  "unchecked",
];

function validateOptions(step) {
  const options = step.options;
  switch (step.type) {
    case "adb_tap":
      if (
        !options ||
        ![options.x, options.y].every(v => validInteger(v, 0, 32767))
      )
        throw new Error("步骤 ADB 点击需要非负整数像素坐标");
      break;
    case "adb_swipe":
      if (
        !options ||
        ![options.startX, options.startY, options.endX, options.endY].every(v =>
          validInteger(v, 0, 32767)
        ) ||
        !validInteger(options.durationMs, 1, 10000)
      )
        throw new Error("步骤 ADB 滑动坐标或时长无效");
      break;
    case "element_assert":
      if (
        !options ||
        !elementStates.includes(options.expected) ||
        !step.selector
      )
        throw new Error("步骤元素断言需要定位条件和预期状态");
      break;
    case "image_assert": {
      if (
        !options ||
        !/^[a-f0-9]{64}$/.test(options.baselineId || "") ||
        !Number.isFinite(options.minSimilarity) ||
        options.minSimilarity < 0 ||
        options.minSimilarity > 100 ||
        !validInteger(options.pixelTolerance, 0, 255)
      )
        throw new Error("步骤图像断言的基准图或容差无效");
      const mode = options.mode ?? "screen";
      if (!["screen", "element", "roi"].includes(mode))
        throw new Error("步骤图像断言的截图范围无效");
      if (mode === "element" && !step.selector)
        throw new Error("步骤元素区域断言需要定位条件");
      if (mode === "roi") {
        const region = options.roi;
        if (
          !region ||
          ![region.left, region.top, region.right, region.bottom].every(value =>
            validInteger(value, 0, 32767)
          ) ||
          region.left >= region.right ||
          region.top >= region.bottom
        )
          throw new Error("步骤 ROI 四角坐标无效");
      }
      break;
    }
  }
}
const identifier = /^[a-zA-Z0-9_-]{1,64}$/;

export function validateJob(job) {
  if (!job || !/^[a-zA-Z0-9._:-]{1,128}$/.test(job.serial || ""))
    throw new Error("必须指定合法设备序列号");
  const test = job.testCase;
  if (
    !test ||
    test.schemaVersion !== 1 ||
    !Array.isArray(test.steps) ||
    !test.steps.length ||
    test.steps.length > 100
  )
    throw new Error("用例需要 schemaVersion=1 和 1–100 个步骤");
  const ids = new Set();
  for (const step of test.steps) {
    if (!identifier.test(step.id || "") || ids.has(step.id))
      throw new Error("步骤 ID 非法或重复");
    ids.add(step.id);
    if (!STEP_TYPES.includes(step.type)) throw new Error("不支持的步骤类型");
    if (
      typeof step.prompt !== "string" ||
      !step.prompt.trim() ||
      step.prompt.length > 8000
    )
      throw new Error("步骤描述不能为空或过长");
    if (
      !Number.isInteger(step.timeoutMs) ||
      step.timeoutMs < 1000 ||
      step.timeoutMs > 300000
    )
      throw new Error("步骤超时范围为 1000–300000 ms");
    if (step.type === "input" && typeof step.value !== "string")
      throw new Error("输入步骤需要 value");
    if (["explore", "scan"].includes(step.type)) {
      const scope = step.exploration;
      if (!scope) throw new Error("步骤缺少探索范围");
      for (const [field, max] of [
        ["allowedControls", 10],
        ["allowedNavigation", 100],
        ["allowedPackages", 10],
      ]) {
        scope[field] ??=
          field === "allowedPackages" ? ["com.android.settings"] : [];
        if (
          !Array.isArray(scope[field]) ||
          scope[field].length > max ||
          scope[field].some(
            v => typeof v !== "string" || !v.trim() || v.length > 200
          ) ||
          new Set(scope[field]).size !== scope[field].length
        )
          throw new Error("步骤探索范围无效");
      }
      if (!scope.allowedPackages.length)
        throw new Error("步骤必须限制包名范围");
      for (const [field, fallback, min, max] of [
        ["maxActions", 20, 2, 100],
        ["maxDepth", 2, 0, 5],
        ["maxPages", 10, 1, 50],
      ]) {
        scope[field] ??= fallback;
        if (
          !Number.isInteger(scope[field]) ||
          scope[field] < min ||
          scope[field] > max
        )
          throw new Error("步骤探索预算无效");
      }
      scope.autoNavigation ??= false;
      scope.visualAssertion ??= "";
      if (
        typeof scope.autoNavigation !== "boolean" ||
        typeof scope.visualAssertion !== "string" ||
        scope.visualAssertion.length > 2000
      )
        throw new Error("步骤探索配置无效");
      if (
        step.type === "scan" &&
        (scope.allowedControls.length || scope.visualAssertion)
      )
        throw new Error("步骤结构扫描不能操作开关或调用模型");
    }
    if (
      step.selector &&
      (!(
        ["tap", "element_assert"].includes(step.type) ||
        (step.type === "image_assert" && step.options?.mode === "element")
      ) ||
        !Object.values(step.selector).some(
          value => typeof value === "string" && value.trim()
        ) ||
        Object.entries(step.selector).some(
          ([key, value]) =>
            !["package", "className", "resourceId", "label"].includes(key) ||
            typeof value !== "string"
        ))
    )
      throw new Error("步骤结构定位条件无效");
    validateOptions(step);
    if (step.enabled !== undefined && typeof step.enabled !== "boolean")
      throw new Error("enabled 必须为布尔值");
    if (
      step.onFailure !== undefined &&
      !["stop", "continue"].includes(step.onFailure)
    )
      throw new Error("失败策略非法");
  }
  const params = job.parameters || {};
  for (const step of test.steps.filter(s => s.enabled !== false)) {
    substitute(step.prompt, params);
    if (step.type === "input") substitute(step.value, params);
  }
  return job;
}

export function substitute(value, parameters) {
  return value.replace(/\$\{([a-zA-Z0-9_]+)\}/g, (_, name) => {
    if (
      !Object.hasOwn(parameters, name) ||
      typeof parameters[name] !== "string"
    )
      throw new Error(`缺少参数 ${name}`);
    return parameters[name];
  });
}

export async function executeStep(
  agent,
  step,
  parameters,
  device,
  {
    captureImage = () => device.screenshot(),
    captureElement,
    loadBaseline,
    onResult = () => {},
    onImage = () => {},
  } = {}
) {
  const prompt = substitute(step.prompt, parameters);
  switch (step.type) {
    case "action":
      await agent.aiAct(prompt);
      break;
    case "tap":
      if (step.selector) await device.tap(step.selector);
      else await agent.aiTap(prompt);
      break;
    case "input":
      await agent.aiInput(prompt, {
        value: substitute(step.value, parameters),
        mode: "replace",
      });
      break;
    case "wait":
      await agent.aiWaitFor(prompt, { timeoutMs: step.timeoutMs });
      break;
    case "assert": {
      // A false result is a test failure; a rejected model call is an execution error.
      const passed = await agent.aiBoolean(prompt);
      if (typeof passed !== "boolean") throw new Error("模型未返回布尔判断");
      return passed ? "passed" : "failed";
    }
    case "adb_tap":
      await device.tapCoordinates(step.options);
      break;
    case "adb_swipe":
      await device.swipeCoordinates(step.options);
      break;
    case "element_assert": {
      const nodes = matchingNodes(await device.read(), step.selector);
      const expected = step.options.expected;
      const node = nodes.length === 1 ? nodes[0] : undefined;
      const passed = {
        exists: nodes.length > 0,
        absent: nodes.length === 0,
        enabled: node?.enabled === true,
        disabled: node?.enabled === false,
        checked: node?.checkable === true && node.checked,
        unchecked: node?.checkable === true && !node.checked,
      }[expected];
      onResult(`匹配到 ${nodes.length} 个元素`);
      return passed ? "passed" : "failed";
    }
    case "image_assert": {
      const { compareScreenshots, cropScreenshot } = await import(
        "./image-assertion.mjs"
      );
      const baseline = await loadBaseline(step.options.baselineId);
      let actual;
      if (step.options.mode === "element") {
        const capture = await captureElement(step.selector);
        if (!capture.image) {
          onResult(capture.message);
          return capture.state;
        }
        actual = capture.image;
      } else {
        actual = await captureImage();
        if (step.options.mode === "roi") {
          const cropped = await cropScreenshot(actual, step.options.roi);
          if (!cropped.image) {
            onImage(null);
            onResult(cropped.message);
            return "failed";
          }
          actual = cropped.image;
        }
      }
      onImage(actual);
      const { similarity, message } = await compareScreenshots(
        actual,
        baseline,
        step.options.pixelTolerance
      );
      onResult(
        message ||
          `图像相似度 ${similarity.toFixed(2)}%，阈值 ${step.options.minSimilarity}%`
      );
      return similarity !== null && similarity >= step.options.minSimilarity
        ? "passed"
        : "failed";
    }
    case "settings":
      await device.settings();
      break;
    case "back":
      await device.back();
      break;
    case "screenshot":
      await captureImage();
      await agent?.recordToReport(prompt);
      break;
  }
  return "passed";
}
