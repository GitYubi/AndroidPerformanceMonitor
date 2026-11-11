import type { CanValue } from "@/lib/canCatalog";

const canStepTypes = ["can_send_once", "can_send_loop", "can_stop"] as const;
const assertions = [
  "element_assert",
  "image_assert",
  "screenshot_assert",
  "assert",
] as const;

export const stepGroups = [
  { label: "通用步骤", types: ["delay"] },
  { label: "Midscene 操作", types: ["action", "tap", "input", "wait"] },
  { label: "ADB 操作", types: ["adb_tap", "adb_swipe", "screenshot"] },
  { label: "页面操作", types: ["settings", "back"] },
  { label: "页面探索", types: ["explore", "scan"] },
  { label: "CAN 总线", types: canStepTypes },
  { label: "断言", types: assertions },
] as const;

export type StepType = (typeof stepGroups)[number]["types"][number];
export const stepTypes: Record<StepType, string> = {
  action: "自然语言操作",
  tap: "视觉点击",
  input: "视觉输入",
  wait: "等待条件",
  delay: "等待时间",
  assert: "Midscene 断言",
  screenshot: "ADB 截图",
  explore: "单页开关探索",
  scan: "结构扫描",
  settings: "打开系统设置",
  back: "返回上一页",
  adb_tap: "ADB 点击",
  adb_swipe: "ADB 滑动",
  element_assert: "元素断言",
  image_assert: "图像对比断言",
  screenshot_assert: "前后截图对比断言",
  can_send_once: "单次发送",
  can_send_loop: "循环发送",
  can_stop: "停止发送",
};

export const isAssertion = (type: StepType) =>
  assertions.some(value => value === type);
export const isCanStep = (
  type: StepType
): type is (typeof canStepTypes)[number] =>
  canStepTypes.some(value => value === type);

export type Selector = {
  package?: string;
  className?: string;
  resourceId?: string;
  label?: string;
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
  baselineId?: string;
  captureName?: string;
  firstStepId?: string;
  secondStepId?: string;
  relation?: "same" | "different";
  minSimilarity?: number;
  pixelTolerance?: number;
  mode?: "screen" | "element" | "roi";
  roi?: Partial<Record<"left" | "top" | "right" | "bottom", number>>;
  dbcKey?: string;
  channel?: number;
  message?: string;
  signals?: Record<string, CanValue | null>;
  cycleMs?: number | null;
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
  type: StepType;
  prompt: string;
  value: string;
  timeoutMs: number;
  enabled: boolean;
  onFailure: "stop" | "continue";
  exploration?: ExploreScope | null;
  selector?: Selector | null;
  options?: StepOptions | null;
};

export function changeStepType(step: Step, type: StepType): Partial<Step> {
  const exploration = type === "explore" || type === "scan";
  let options: StepOptions | null = null;
  if (type === "delay") options = { durationMs: 1000 };
  if (type === "adb_tap") options = {};
  if (type === "adb_swipe") options = { durationMs: 300 };
  if (type === "element_assert") options = { expected: "exists" };
  if (type === "image_assert")
    options = { mode: "screen", minSimilarity: 99, pixelTolerance: 16 };
  if (type === "screenshot") options = { captureName: "" };
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
  const structured = options !== null || type === "settings" || type === "back";
  return {
    type,
    prompt: structured ? stepTypes[type] : step.options ? "" : step.prompt,
    selector: type === "element_assert" ? {} : null,
    options,
    exploration: exploration ? { allowedControls: [], maxActions: 6 } : null,
    timeoutMs: exploration ? 300000 : step.timeoutMs,
    onFailure: isAssertion(type) ? step.onFailure : "stop",
  };
}
