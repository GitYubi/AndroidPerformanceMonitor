import { matchingNodes } from "./hierarchy.mjs";
import { setTimeout as sleep } from "node:timers/promises";

export const STEP_TYPES = [
  "action",
  "tap",
  "input",
  "wait",
  "delay",
  "assign",
  "if",
  "else",
  "for",
  "assert",
  "screenshot",
  "element_screenshot",
  "explore",
  "scan",
  "settings",
  "back",
  "adb_tap",
  "adb_swipe",
  "element_assert",
  "image_assert",
  "screenshot_assert",
  "variable_assert",
  "can_send_once",
  "can_send_loop",
  "can_stop",
];
export const isCanStep = type =>
  ["can_send_once", "can_send_loop", "can_stop"].includes(type);
export const stepDeadlineMs = step =>
  step.type === "delay" ? step.options.durationMs + 1000 : step.timeoutMs;
export const isAssertion = type =>
  [
    "assert",
    "element_assert",
    "image_assert",
    "screenshot_assert",
    "variable_assert",
  ].includes(type);
export const isCapture = type =>
  type === "screenshot" || type === "element_screenshot";
export const requiresDevice = step =>
  !isCanStep((step.condition ?? step).type) &&
  ![
    "for",
    "else",
    "delay",
    "assign",
    "variable_assert",
    "screenshot_assert",
  ].includes((step.condition ?? step).type);
export const requiresModel = step =>
  step.condition
    ? requiresModel(step.condition)
    : ["action", "input", "wait", "assert"].includes(step.type) ||
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
const variableName = /^[a-zA-Z0-9_]{1,64}$/;
const variableOperators = ["eq", "ne", "gt", "gte", "lt", "lte"];
const imageId = /^[a-f0-9]{64}$/;

export function captureSourceIds(step) {
  if (step.type === "assign")
    return step.options.sourceStepId ? [step.options.sourceStepId] : [];
  if (step.type === "screenshot_assert")
    return [step.options.firstStepId, step.options.secondStepId].filter(
      Boolean
    );
  return [];
}

function validRegion(region) {
  return (
    region &&
    [region.left, region.top, region.right, region.bottom].every(value =>
      validInteger(value, 0, 32767)
    ) &&
    region.left < region.right &&
    region.top < region.bottom
  );
}

function validateOptions(step) {
  const options = step.options;
  switch (step.type) {
    case "assign": {
      const {
        variable,
        valueType,
        booleanValue,
        numberValue,
        sourceStepId,
        baselineId,
      } = options ?? {};
      const populated = [
        booleanValue,
        numberValue,
        sourceStepId,
        baselineId,
      ].filter(value => value != null);
      const valid = {
        boolean: typeof booleanValue === "boolean",
        number: Number.isFinite(numberValue),
        image:
          (typeof sourceStepId === "string" && identifier.test(sourceStepId)) ||
          (typeof baselineId === "string" && imageId.test(baselineId)),
      };
      if (
        typeof variable !== "string" ||
        !variableName.test(variable) ||
        populated.length !== 1 ||
        valid[valueType] !== true
      )
        throw new Error("步骤变量赋值需要名称及对应类型的值");
      break;
    }
    case "for":
      if (
        !validInteger(options?.count, 1, 100) ||
        (options.indexVariable != null &&
          !variableName.test(options.indexVariable))
      )
        throw new Error("步骤循环次数或索引变量无效");
      break;
    case "variable_assert":
      if (
        !variableName.test(options?.variable ?? "") ||
        !variableOperators.includes(options.operator ?? "eq") ||
        typeof (options.expectedValue ?? "") !== "string"
      )
        throw new Error("步骤变量判断参数无效");
      break;
    case "delay":
      if (!validInteger(options?.durationMs, 1, 300000))
        throw new Error("步骤等待时长须为 1–300000 毫秒");
      break;
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
    case "screenshot":
    case "element_screenshot": {
      const mode = options?.mode ?? "screen";
      if (
        options != null &&
        (typeof (options.captureName ?? "") !== "string" ||
          [...(options.captureName ?? "")].length > 100)
      )
        throw new Error("步骤截图名称须为 100 字以内的文本");
      if (
        !["screen", "roi"].includes(mode) ||
        (mode === "roi" && !validRegion(options.roi)) ||
        (mode === "screen" && options?.roi != null)
      )
        throw new Error("步骤截图范围或 ROI 坐标无效");
      if (
        step.type === "element_screenshot" &&
        (mode !== "screen" || !step.selector)
      )
        throw new Error("步骤元素截图需要定位条件");
      break;
    }
    case "screenshot_assert": {
      const {
        firstStepId,
        secondStepId,
        firstVariable,
        secondVariable,
        relation = "same",
        minSimilarity = 100,
        pixelTolerance = 0,
      } = options ?? {};
      if (
        ![
          [firstStepId, firstVariable],
          [secondStepId, secondVariable],
        ].every(([id, variable]) =>
          id != null
            ? variable == null && typeof id === "string" && identifier.test(id)
            : typeof variable === "string" && variableName.test(variable)
        ) ||
        ((firstStepId ?? null) === (secondStepId ?? null) &&
          (firstVariable ?? null) === (secondVariable ?? null)) ||
        !["same", "different"].includes(relation) ||
        !Number.isFinite(minSimilarity) ||
        minSimilarity < 0 ||
        minSimilarity > 100 ||
        !validInteger(pixelTolerance, 0, 255)
      )
        throw new Error("步骤截图对比需要两张不同截图及有效的对比参数");
      break;
    }
    case "image_assert": {
      if (
        options?.expected != null &&
        !["exists", "absent"].includes(options.expected)
      )
        throw new Error("步骤图像断言的预期状态无效");
      if (
        options?.colorMode != null &&
        !["grayscale", "color"].includes(options.colorMode)
      )
        throw new Error("步骤图像断言的颜色模式无效");
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
        if (!validRegion(options.roi)) throw new Error("步骤 ROI 四角坐标无效");
      }
      break;
    }
  }
}
const identifier = /^[a-zA-Z0-9_-]{1,64}$/;

export function buildStepPlan(steps) {
  const byId = new Map();
  const groups = new Map();
  const scopes = new Map();
  const active = new Set();
  const children = (parent = null, branch = null) =>
    groups.get(parent)?.get(branch) ?? [];
  for (const step of steps) {
    const parentId = step.parentId ?? null;
    const branch = step.branch ?? null;
    const parent = byId.get(parentId);
    if (!identifier.test(step.id ?? "") || byId.has(step.id))
      throw new Error("步骤 ID 非法或重复");
    if (
      (parentId !== null && !["if", "else", "for"].includes(parent?.type)) ||
      (branch !== null && (branch !== "else" || parent?.type !== "if"))
    )
      throw new Error("步骤的分支或循环归属无效");
    if (step.type === "else" && (parent?.type !== "if" || branch !== "else"))
      throw new Error("步骤 Else 必须归属于 If");
    const siblings = children(parentId, branch);
    if (
      branch === "else" &&
      siblings.length &&
      (step.type === "else" || siblings[0].type === "else")
    )
      throw new Error("步骤 If 只能有一个 Else 步骤块");
    if (!groups.has(parentId)) groups.set(parentId, new Map());
    const branches = groups.get(parentId);
    if (!branches.has(branch)) branches.set(branch, []);
    branches.get(branch).push(step);
    byId.set(step.id, step);
    scopes.set(
      step.id,
      parent ? [...scopes.get(parentId), { parent, branch }] : []
    );
    if (step.enabled !== false && (!parent || active.has(parentId)))
      active.add(step.id);
  }
  function* ordered(parent, branch) {
    for (const step of children(parent, branch)) {
      yield step;
      yield* ordered(step.id, null);
      yield* ordered(step.id, "else");
    }
  }
  if ([...ordered()].some((step, index) => step !== steps[index]))
    throw new Error("步骤的分支和循环子步骤须连续排列");
  return {
    children,
    scopes,
    enabled: steps.filter(step => active.has(step.id)),
  };
}

export function validateJob(job) {
  const test = job?.testCase;
  if (
    !test ||
    test.schemaVersion !== 1 ||
    !Array.isArray(test.steps) ||
    !test.steps.length ||
    test.steps.length > 200 ||
    test.steps.filter(step => step.type !== "else").length > 100
  )
    throw new Error("用例需要 schemaVersion=1 和 1–100 个步骤");
  const { enabled, scopes } = buildStepPlan(test.steps);
  const deviceFree =
    enabled.length > 0 && enabled.every(step => !requiresDevice(step));
  if (
    typeof job.serial !== "string" ||
    !(
      deviceFree ? /^[a-zA-Z0-9._:-]{0,128}$/ : /^[a-zA-Z0-9._:-]{1,128}$/
    ).test(job.serial)
  )
    throw new Error("必须指定合法设备序列号");
  for (const step of test.steps) {
    if (
      (step.type === "if") !== Boolean(step.condition) ||
      (step.condition &&
        (!isAssertion(step.condition.type) ||
          step.condition.condition ||
          step.condition.parentId ||
          step.condition.branch))
    )
      throw new Error("步骤 If 需要有效的布尔判断条件");
  }
  for (const step of test.steps.flatMap(step =>
    step.condition ? [step, step.condition] : [step]
  )) {
    if (!STEP_TYPES.includes(step.type)) throw new Error("不支持的步骤类型");
    if (
      step.name !== undefined &&
      (typeof step.name !== "string" || [...step.name].length > 120)
    )
      throw new Error("步骤名称须为 120 字以内的文本");
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
        ["tap", "element_assert", "element_screenshot"].includes(step.type) ||
        (step.type === "image_assert" && step.options?.mode === "element")
      ) ||
        !Object.values(step.selector).some(
          value => typeof value === "string" && value.trim()
        ) ||
        Object.entries(step.selector).some(
          ([key, value]) =>
            !["package", "className", "resourceId", "label", "xpath"].includes(
              key
            ) ||
            typeof value !== "string" ||
            (key === "xpath" &&
              value !== "" &&
              !/^\/hierarchy(?:\/node\[[1-9][0-9]{0,4}\])+$/.test(value))
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
  const captured = new Map();
  const assigned = new Set();
  const images = new Set();
  for (const step of enabled) {
    const scope = scopes.get(step.id);
    const variables = {
      ...params,
      ...Object.fromEntries([...assigned].map(name => [name, ""])),
    };
    for (const { parent } of scope) {
      const name = parent.type === "for" && parent.options.indexVariable;
      if (name)
        Object.defineProperty(variables, name, {
          value: "1",
          enumerable: true,
        });
    }
    if (
      step.type === "for" &&
      step.options.indexVariable &&
      Object.hasOwn(variables, step.options.indexVariable)
    )
      throw new Error("步骤循环变量与现有变量重名");
    for (const operation of step.condition ? [step, step.condition] : [step]) {
      substitute(operation.prompt, variables);
      if (operation.type === "input") substitute(operation.value, variables);
      if (operation.type === "variable_assert") {
        if (!Object.hasOwn(variables, operation.options.variable))
          throw new Error("步骤引用了未声明或不可见的变量");
        substitute(operation.options.expectedValue ?? "", variables);
      }
      if (
        operation.type === "screenshot_assert" &&
        [
          operation.options.firstVariable,
          operation.options.secondVariable,
        ].some(name => name && !images.has(name))
      )
        throw new Error("步骤引用的图片变量须在前面赋值");
      if (
        !captureSourceIds(operation).every(id => {
          const source = captured.get(id);
          return (
            source &&
            source.every(
              (entry, index) =>
                entry.parent === scope[index]?.parent &&
                entry.branch === scope[index]?.branch
            )
          );
        })
      )
        throw new Error(
          "步骤截图对比只能引用前面已启用的截图步骤，且须位于当前分支或外层"
        );
    }
    if (isCapture(step.type)) captured.set(step.id, scope);
    if (step.type === "assign") {
      const { variable, valueType } = step.options;
      if (
        scope.some(
          ({ parent }) =>
            parent.type === "for" && parent.options.indexVariable === variable
        )
      )
        throw new Error("步骤不能给循环索引变量赋值");
      assigned.add(variable);
      if (valueType === "image") images.add(variable);
    }
  }
  return job;
}

export function substitute(value, parameters) {
  return value.replace(/\$\{([a-zA-Z0-9_]+)\}/g, (_, name) => {
    if (!Object.hasOwn(parameters, name)) throw new Error(`缺少参数 ${name}`);
    const value = scalarText(parameters[name]);
    if (value === null) throw new Error(`参数 ${name} 是图片，不能用作文本`);
    return value;
  });
}

function scalarText(value) {
  return typeof value === "string" ||
    typeof value === "boolean" ||
    Number.isFinite(value)
    ? String(value)
    : null;
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
    loadScreenshot,
    setVariable,
    sensitive = false,
    isCondition = false,
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
    case "delay":
      await sleep(step.options.durationMs);
      onResult(`已等待 ${step.options.durationMs / 1000} 秒`);
      break;
    case "assert": {
      // A false result is a test failure; a rejected model call is an execution error.
      const passed = await agent.aiBoolean(prompt);
      if (typeof passed !== "boolean") throw new Error("模型未返回布尔判断");
      return passed ? "passed" : "failed";
    }
    case "assign": {
      const {
        variable,
        valueType,
        booleanValue,
        numberValue,
        sourceStepId,
        baselineId,
      } = step.options;
      const value =
        valueType === "boolean"
          ? booleanValue
          : valueType === "number"
            ? numberValue
            : sourceStepId
              ? loadScreenshot?.(sourceStepId)
              : await loadBaseline(baselineId);
      if (valueType === "image" && !Buffer.isBuffer(value)) {
        onResult("图片来源未生成，请检查前面的截图步骤");
        return "error";
      }
      setVariable(variable, value);
      onResult(`已为 ${variable} 赋值`);
      break;
    }
    case "variable_assert": {
      const { variable, operator = "eq", expectedValue = "" } = step.options;
      const left = parameters[variable];
      const right = substitute(expectedValue, parameters);
      if (!Object.hasOwn(parameters, variable)) {
        onResult("变量缺少运行值");
        return "error";
      }
      const text = scalarText(left);
      if (text === null) {
        onResult("图片变量请使用前后截图对比断言");
        return "error";
      }
      if ((operator === "eq" || operator === "ne") && typeof left !== "number")
        return (text ===
          (typeof left === "boolean" ? right.trim().toLowerCase() : right)) ===
          (operator === "eq")
          ? "passed"
          : "failed";
      const values = [text, right].map(value =>
        value.trim() ? Number(value) : NaN
      );
      if (!values.every(Number.isFinite)) {
        onResult("数值比较需要两个有效数字");
        return "error";
      }
      const [a, b] = values;
      return {
        eq: a === b,
        ne: a !== b,
        gt: a > b,
        gte: a >= b,
        lt: a < b,
        lte: a <= b,
      }[operator]
        ? "passed"
        : "failed";
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
      const { compareScreenshots, cropScreenshot, searchScreenshot } =
        await import("./image-assertion.mjs");
      const baseline = await loadBaseline(
        step.options.baselineId,
        step.options.baselineProjectId
      );
      let actual;
      if (step.options.mode === "element") {
        const capture = await captureElement(step.selector);
        if (!capture.image) {
          onResult(capture.message);
          return isCondition ? "error" : capture.state;
        }
        actual = capture.image;
      } else {
        actual = await captureImage();
        if (step.options.mode === "roi") {
          const cropped = await cropScreenshot(actual, step.options.roi);
          if (!cropped.image) {
            onImage(null);
            onResult(cropped.message);
            return isCondition ? "error" : "failed";
          }
          actual = cropped.image;
        }
      }
      onImage(actual);
      const colorMode = step.options.colorMode ?? "grayscale";
      const result =
        (step.options.mode ?? "screen") === "screen"
          ? await searchScreenshot(
              actual,
              baseline,
              step.options.pixelTolerance,
              step.options.minSimilarity,
              colorMode
            )
          : await compareScreenshots(
              actual,
              baseline,
              step.options.pixelTolerance,
              "基准图",
              colorMode
            );
      const { similarity, message } = result;
      const isSearch = (step.options.mode ?? "screen") === "screen";
      if (!isSearch && similarity === null && isCondition) {
        onResult(message);
        return "error";
      }
      onResult(
        message ||
          `图像相似度 ${similarity.toFixed(2)}%，阈值 ${step.options.minSimilarity}%${result.x != null ? `，位置 (${result.x}, ${result.y})` : ""}`
      );
      const matched = isSearch
        ? result.found
        : similarity !== null && similarity >= step.options.minSimilarity;
      const expected = step.options.expected ?? "exists";
      return (expected === "absent" ? !matched : matched) ? "passed" : "failed";
    }
    case "screenshot_assert": {
      const {
        firstStepId,
        secondStepId,
        firstVariable,
        secondVariable,
        relation = "same",
        minSimilarity = 100,
        pixelTolerance = 0,
      } = step.options;
      const first = firstVariable
        ? parameters[firstVariable]
        : loadScreenshot?.(firstStepId);
      const second = secondVariable
        ? parameters[secondVariable]
        : loadScreenshot?.(secondStepId);
      if (!Buffer.isBuffer(first) || !Buffer.isBuffer(second)) {
        onResult("对比图片未生成，请检查前面的截图和赋值步骤");
        return "error";
      }
      const { compareScreenshots } = await import("./image-assertion.mjs");
      const { similarity, message } = await compareScreenshots(
        second,
        first,
        pixelTolerance,
        "第一张截图"
      );
      const same = similarity !== null && similarity >= minSimilarity;
      const expected = relation === "same" ? "相同" : "不同";
      onResult(
        similarity === null
          ? `期望${expected} · 尺寸不同 · ${message}`
          : `期望${expected} · 相似度 ${Number(similarity.toFixed(6))}% · 相同阈值 ${minSimilarity}%`
      );
      return same === (relation === "same") ? "passed" : "failed";
    }
    case "settings":
      await device.settings();
      break;
    case "back":
      await device.back();
      break;
    case "screenshot":
    case "element_screenshot": {
      let image;
      if (step.type === "element_screenshot") {
        const capture = await captureElement(step.selector);
        if (!capture.image) {
          onImage(null);
          onResult(capture.message);
          return "error";
        }
        image = capture.image;
      } else {
        image = await captureImage();
        if (step.options?.mode === "roi") {
          const { cropScreenshot } = await import("./image-assertion.mjs");
          const cropped = await cropScreenshot(image, step.options.roi);
          if (!cropped.image) {
            onImage(null);
            onResult(cropped.message);
            return "error";
          }
          image = cropped.image;
        }
      }
      onImage(image);
      const name = step.options?.captureName?.trim();
      if (name) onResult(`已截图 · ${name}`);
      if (agent && !sensitive) {
        await agent.recordToReport(prompt, {
          screenshots: [
            { base64: image.toString("base64"), description: name },
          ],
        });
      }
      break;
    }
  }
  return "passed";
}
