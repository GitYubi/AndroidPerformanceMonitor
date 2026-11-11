export const stepGroups = [
  { label: "Midscene 操作", types: ["action", "tap", "input", "wait"] },
  {
    label: "ADB 操作",
    types: [
      "adb_tap",
      "adb_swipe",
      "screenshot",
      "settings",
      "back",
      "explore",
      "scan",
    ],
  },
  { label: "断言", types: ["element_assert", "image_assert", "assert"] },
] as const;

export type StepType = (typeof stepGroups)[number]["types"][number];
export const stepTypes: Record<StepType, string> = {
  action: "自然语言操作",
  tap: "视觉点击",
  input: "视觉输入",
  wait: "等待条件",
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
};

export const isAssertion = (type: StepType) =>
  stepGroups[2].types.some(value => value === type);

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
  minSimilarity?: number;
  pixelTolerance?: number;
  mode?: "screen" | "element" | "roi";
  roi?: Partial<Record<"left" | "top" | "right" | "bottom", number>>;
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
  if (type === "adb_tap") options = {};
  if (type === "adb_swipe") options = { durationMs: 300 };
  if (type === "element_assert") options = { expected: "exists" };
  if (type === "image_assert")
    options = { mode: "screen", minSimilarity: 99, pixelTolerance: 16 };
  const structured = options !== null || type === "settings" || type === "back";
  return {
    type,
    prompt:
      structured || type === "screenshot"
        ? stepTypes[type]
        : step.options
          ? ""
          : step.prompt,
    selector: type === "element_assert" ? {} : null,
    options,
    exploration: exploration ? { allowedControls: [], maxActions: 6 } : null,
    timeoutMs: exploration ? 300000 : step.timeoutMs,
    onFailure: isAssertion(type) ? step.onFailure : "stop",
  };
}
