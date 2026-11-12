import type { CanValue } from "@/lib/canCatalog";

const canStepTypes = ["can_send_once", "can_send_loop", "can_stop"] as const;
const assertions = [
  "element_assert",
  "image_assert",
  "screenshot_assert",
  "variable_assert",
  "assert",
] as const;

export const stepGroups = [
  { label: "通用步骤", types: ["delay", "assign", "if", "for"] },
  { label: "Midscene 操作", types: ["action", "tap", "input", "wait"] },
  { label: "ADB 操作", types: ["adb_tap", "adb_swipe", "screenshot"] },
  { label: "页面操作", types: ["settings", "back"] },
  { label: "元素操作", types: ["element_screenshot"] },
  { label: "页面探索", types: ["explore", "scan"] },
  { label: "CAN 总线", types: canStepTypes },
  { label: "断言", types: assertions },
] as const;

export type StepType = (typeof stepGroups)[number]["types"][number] | "else";
export const stepTypes: Record<StepType, string> = {
  action: "自然语言操作",
  tap: "视觉点击",
  input: "视觉输入",
  wait: "等待条件",
  delay: "等待时间",
  assign: "为变量赋值",
  if: "If 判断",
  else: "Else 分支",
  for: "For 循环",
  assert: "Midscene 断言",
  screenshot: "ADB 截图",
  element_screenshot: "元素截图",
  explore: "单页开关探索",
  scan: "结构扫描",
  settings: "打开系统设置",
  back: "返回上一页",
  adb_tap: "ADB 点击",
  adb_swipe: "ADB 滑动",
  element_assert: "元素断言",
  image_assert: "图像对比断言",
  screenshot_assert: "前后截图对比断言",
  variable_assert: "变量值判断",
  can_send_once: "单次发送",
  can_send_loop: "循环发送",
  can_stop: "停止发送",
};

export const isAssertion = (type: StepType) =>
  assertions.some(value => value === type);
export const conditionTypes = assertions;
export const isCapture = (type: StepType) =>
  type === "screenshot" || type === "element_screenshot";
export const isBlock = (type: StepType) =>
  type === "if" || type === "else" || type === "for";
export const isCanStep = (
  type: StepType
): type is (typeof canStepTypes)[number] =>
  canStepTypes.some(value => value === type);

export type Selector = {
  package?: string;
  className?: string;
  resourceId?: string;
  label?: string;
  xpath?: string;
};

export type StepOptions = {
  x?: number;
  y?: number;
  startX?: number;
  startY?: number;
  endX?: number;
  endY?: number;
  durationMs?: number;
  expected?: string;
  baselineId?: string | null;
  baselineProjectId?: string | null;
  captureName?: string;
  firstStepId?: string | null;
  secondStepId?: string | null;
  firstVariable?: string | null;
  secondVariable?: string | null;
  relation?: "same" | "different";
  minSimilarity?: number;
  pixelTolerance?: number;
  colorMode?: "grayscale" | "color";
  mode?: "screen" | "element" | "roi";
  roi?: Partial<Record<"left" | "top" | "right" | "bottom", number>>;
  dbcKey?: string;
  channel?: number;
  message?: string;
  signals?: Record<string, CanValue | null>;
  cycleMs?: number | null;
  count?: number;
  indexVariable?: string | null;
  variable?: string;
  valueType?: "boolean" | "number" | "image";
  booleanValue?: boolean;
  numberValue?: number;
  sourceStepId?: string | null;
  operator?: "eq" | "ne" | "gt" | "gte" | "lt" | "lte";
  expectedValue?: string;
};

export type ExploreScope = {
  allowedControls: string[];
  maxActions: number;
  allowedNavigation?: string[];
  allowedPackages?: string[];
  maxDepth?: number;
  maxPages?: number;
  autoNavigation?: boolean;
  visualAssertion?: string;
};

export type Step = {
  id: string;
  name?: string;
  type: StepType;
  prompt: string;
  value: string;
  timeoutMs: number;
  enabled: boolean;
  onFailure: "stop" | "continue";
  exploration?: ExploreScope | null;
  selector?: Selector | null;
  options?: StepOptions | null;
  parentId?: string | null;
  branch?: "else" | null;
  condition?: Step | null;
};

export function stepLayout(steps: Step[]) {
  const parents = new Map<
    string,
    {
      step: Step;
      path: string[];
      ancestors: Step[];
      active: boolean;
      indent: number;
    }
  >();
  const rows = steps.map(step => {
    const parent = step.parentId ? parents.get(step.parentId) : undefined;
    const row = {
      step,
      path: parent
        ? [...parent.path, `${parent.step.id}:${step.branch ?? ""}`]
        : [],
      ancestors: parent ? [...parent.ancestors, parent.step] : [],
      active: step.enabled && (parent?.active ?? true),
      indent: parent ? parent.indent + Number(step.type !== "else") : 0,
    };
    parents.set(step.id, row);
    return row;
  });
  return rows.map((row, index) => {
    let end = index + 1;
    while (end < rows.length && rows[end].path.length > row.path.length) end++;
    return { ...row, end };
  });
}

export function expandElseBlocks(
  steps: Step[],
  createElse: (parentId: string) => Step
): Step[] {
  const markers = new Map<string, Step>();
  return steps.flatMap(step => {
    if (step.type === "else" || step.branch !== "else" || !step.parentId)
      return [step];
    const marker = markers.get(step.parentId);
    const block = marker ?? createElse(step.parentId);
    markers.set(step.parentId, block);
    const child = { ...step, parentId: block.id, branch: null };
    return marker ? [child] : [block, child];
  });
}

export type StepDrop = { before: string } | { into: string | null };

export function childInsertion(
  rows: ReturnType<typeof stepLayout>,
  parentId: string | null,
  branch: "else" | null = null
) {
  if (parentId === null) return rows.length;
  const parent = rows.find(row => row.step.id === parentId);
  if (!parent) return -1;
  if (branch === null && parent.step.type === "if") {
    const elseIndex = rows.findIndex(
      row => row.step.parentId === parentId && row.step.branch === "else"
    );
    if (elseIndex >= 0) return elseIndex;
  }
  return parent.end;
}

export function relocateStep(
  steps: Step[],
  id: string,
  drop: StepDrop
): Step[] {
  const rows = stepLayout(steps);
  const index = rows.findIndex(row => row.step.id === id);
  if (index < 0) return steps;
  const { step, end } = rows[index];
  const targetId = "before" in drop ? drop.before : drop.into;
  const targetIndex =
    targetId === null ? -1 : rows.findIndex(row => row.step.id === targetId);
  if (targetId !== null && targetIndex < 0) return steps;
  if (targetIndex >= index && targetIndex < end) return steps;
  const target = rows[targetIndex]?.step;
  let parentId = targetId;
  let branch: "else" | null = null;
  let insertion;
  if (step.type === "else") {
    if (
      "before" in drop ||
      target?.type !== "if" ||
      steps.some(
        item =>
          item.id !== id && item.parentId === targetId && item.branch === "else"
      )
    )
      return steps;
    branch = "else";
    insertion = childInsertion(rows, parentId, branch);
  } else if ("before" in drop) {
    if (!target || target.type === "else") return steps;
    parentId = target.parentId ?? null;
    branch = target.branch ?? null;
    insertion = targetIndex;
  } else {
    if (target && !isBlock(target.type)) return steps;
    insertion = childInsertion(rows, parentId);
  }
  if (
    (insertion === index || insertion === end) &&
    (step.parentId ?? null) === parentId &&
    (step.branch ?? null) === branch
  )
    return steps;
  const moved = [{ ...step, parentId, branch }, ...steps.slice(index + 1, end)];
  const remaining = [...steps.slice(0, index), ...steps.slice(end)];
  const offset = insertion >= end ? insertion - (end - index) : insertion;
  return [...remaining.slice(0, offset), ...moved, ...remaining.slice(offset)];
}

export function changeStepType(step: Step, type: StepType): Partial<Step> {
  const exploration = type === "explore" || type === "scan";
  let options: StepOptions | null = null;
  if (type === "delay") options = { durationMs: 1000 };
  if (type === "assign")
    options = { variable: "", valueType: "boolean", booleanValue: false };
  if (type === "for") options = { count: 2 };
  if (type === "adb_tap") options = {};
  if (type === "adb_swipe") options = { durationMs: 300 };
  if (type === "element_assert") options = { expected: "exists" };
  if (type === "image_assert")
    options = {
      mode: "screen",
      colorMode: "grayscale",
      minSimilarity: 99,
      pixelTolerance: 16,
    };
  if (isCapture(type)) options = { captureName: "", mode: "screen" };
  if (type === "variable_assert")
    options = { variable: "", operator: "eq", expectedValue: "" };
  if (type === "screenshot_assert")
    options = { relation: "same", minSimilarity: 100, pixelTolerance: 0 };
  if (isCanStep(type)) {
    const { dbcKey, message, channel, signals, cycleMs } = step.options ?? {};
    options = {
      dbcKey,
      message,
      channel,
      ...(type === "can_stop" ? {} : { signals: signals ?? {} }),
      ...(type === "can_send_loop" ? { cycleMs } : {}),
    };
  }
  const structured =
    options !== null || type === "if" || type === "settings" || type === "back";
  return {
    type,
    prompt: structured ? stepTypes[type] : step.options ? "" : step.prompt,
    selector:
      type === "element_assert" || type === "element_screenshot" ? {} : null,
    condition:
      type === "if"
        ? {
            id: step.id,
            type: "variable_assert",
            prompt: stepTypes.variable_assert,
            value: "",
            timeoutMs: step.timeoutMs,
            enabled: true,
            onFailure: "stop",
            options: { variable: "", operator: "eq", expectedValue: "" },
          }
        : null,
    options,
    exploration: exploration ? { allowedControls: [], maxActions: 6 } : null,
    timeoutMs: exploration ? 300000 : step.timeoutMs,
    onFailure: isAssertion(type) ? step.onFailure : "stop",
  };
}
