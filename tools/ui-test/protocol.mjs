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
];
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
      (step.type !== "tap" ||
        !Object.values(step.selector).some(Boolean) ||
        Object.entries(step.selector).some(
          ([key, value]) =>
            !["package", "className", "resourceId", "label"].includes(key) ||
            typeof value !== "string"
        ))
    )
      throw new Error("步骤结构定位条件无效");
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

export async function executeStep(agent, step, parameters, device) {
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
    case "settings":
      await device.settings();
      break;
    case "back":
      await device.back();
      break;
    case "screenshot":
      await agent?.recordToReport(prompt);
      break;
  }
  return "passed";
}
