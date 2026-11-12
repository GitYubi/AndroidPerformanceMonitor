import { PerformanceReportDialog } from "@/components/PerformanceReportDialog";
import { BACKEND_URL } from "@/lib/backend";
import type { CanCatalog } from "@/lib/canCatalog";
import { getUiSection } from "@/lib/uiNavigation";
import { UiStepFields } from "@/components/UiStepFields";
import { UiImageLibrary, UiProjectOptions } from "@/components/UiImageLibrary";
import {
  defaultProject,
  uiImagesPath,
  type UiImage,
  type UiProject,
} from "@/lib/uiImages";
import {
  UiDevicePreview,
  type UiDevicePreviewHandle,
} from "@/components/UiDevicePreview";
import {
  changeStepType,
  childInsertion,
  expandElseBlocks,
  isAssertion,
  isBlock,
  isCanStep,
  isCapture,
  relocateStep,
  stepLayout,
  stepGroups,
  stepTypes,
  type Selector,
  type Step,
  type StepDrop,
  type StepType,
} from "@/lib/uiSteps";
import PageMapWorkbench from "@/components/PageMapWorkbench";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type DragEvent,
} from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Play,
  Square,
  Plus,
  Save,
  Settings,
  Download,
  ArrowUp,
  ArrowDown,
  ChevronDown,
  ChevronRight,
  GripVertical,
  Trash2,
  RefreshCw,
  FlaskConical,
} from "lucide-react";
import { toast } from "sonner";
import { useLocation, useSearchParams } from "wouter";

const API = BACKEND_URL;
type Parameter = { name: string; secret: boolean; default: string };
type Case = {
  schemaVersion: 1;
  id: string;
  version: number;
  name: string;
  projectId?: string;
  group: string;
  tags: string[];
  parameters: Parameter[];
  steps: Step[];
};
type Run = {
  id: string;
  serial: string;
  state: string;
  createdAt: number;
  endedAt: number | null;
  testCase: Case;
  report: string | null;
  message?: string;
  sensitive: boolean;
  performanceSessionId: string | null;
  parameters?: Record<string, string>;
};
type Event = {
  seq: number;
  type: string;
  stepId?: string;
  state: string;
  time: number;
  screenshot?: string;
  message?: string;
  action?: string;
  target?: string;
  control?: string;
  original?: boolean;
  elapsedMs?: number;
  iterations?: number[];
};
type Config = {
  baseUrl: string;
  model: string;
  family: string;
  reportRoot: string;
  hasKey: boolean;
  envFileExists: boolean;
  apiKey?: string;
};
type StepMetric = {
  stepId: string;
  sessionId: string;
  sampleCount: number;
  cpuAverage: number | null;
  cpuPeak: number | null;
  memoryAverageMb: number | null;
  fpsAverage: number | null;
};
const states: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  passed: "通过",
  failed: "断言失败",
  error: "执行异常",
  cancelled: "已取消",
  interrupted: "已中断",
  needs_review: "待复核",
  skipped: "已跳过",
  restored: "已恢复",
};
const active = (run: Run) => ["queued", "running"].includes(run.state);
const uid = () => crypto.randomUUID().replaceAll("-", "");
const newStep = (type: StepType = "action", prompt = ""): Step => ({
  id: uid(),
  type,
  prompt,
  value: "",
  timeoutMs: 60000,
  enabled: true,
  onFailure: "stop",
});
const newElse = (parentId: string): Step => ({
  ...newStep("else", stepTypes.else),
  parentId,
  branch: "else",
});
const stepLimitReached = (steps: Step[]) =>
  steps.filter(step => step.type !== "else").length >= 100;
const blank = (projectId = defaultProject.id): Case => ({
  schemaVersion: 1,
  id: uid(),
  version: 1,
  name: "新的 UI 用例",
  projectId,
  group: "",
  tags: [],
  parameters: [],
  steps: [newStep()],
});
const selectClass =
  "h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm";
const compactInput = "h-8 px-2 shadow-none";
const sameScope = (a: Step, b: Step) =>
  (a.parentId ?? null) === (b.parentId ?? null) &&
  (a.branch ?? null) === (b.branch ?? null);

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `${API}/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
  );
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "请求无效，请检查必填内容、参数名和步骤描述"
    );
  return data;
}

export default function UITesting() {
  const [search] = useSearchParams();
  const [, navigate] = useLocation();
  const section = getUiSection(search);
  const [devices, setDevices] = useState<
    { serial: string; state: string; model?: string }[]
  >([]);
  const [serial, setSerial] = useState("");
  const [rightPanel, setRightPanel] = useState<"device" | "records">("device");
  const [pickStepId, setPickStepId] = useState("");
  const devicePreview = useRef<UiDevicePreviewHandle>(null);
  const [draft, setDraft] = useState<Case>(() => blank());
  const [projects, setProjects] = useState<UiProject[]>([defaultProject]);
  const [projectId, setProjectId] = useState(defaultProject.id);
  const [catalog, setCatalog] = useState<{
    id: string;
    items: UiImage[];
  } | null>(null);
  const caseProjectId = draft.projectId ?? defaultProject.id;
  const catalogId = section === "execute" ? caseProjectId : projectId;
  const images = catalog?.id === catalogId ? catalog.items : [];
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [dragged, setDragged] = useState<string | null>(null);
  const [hoverDrop, setHoverDrop] = useState<string | null>(null);
  const [canCatalog, setCanCatalog] = useState<CanCatalog | null>(null);
  const [canChannels, setCanChannels] = useState<number[]>([]);
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [cases, setCases] = useState<Case[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [metrics, setMetrics] = useState<StepMetric[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [config, setConfig] = useState<Config | null>(null);
  const [doctor, setDoctor] = useState<{
    adb: boolean;
    midscene: boolean;
    model: { keyConfigured: boolean };
    node: string;
  } | null>(null);
  const stepList = useRef<HTMLDivElement>(null);
  const selection = useRef(runId);
  selection.current = runId;

  useLayoutEffect(() => {
    const list = stepList.current;
    if (!dragged || !list) return;
    let frame = 0;
    let point: { x: number; y: number } | null = null;
    function scroll() {
      frame = 0;
      if (!point || !list) return;
      const box = list.getBoundingClientRect();
      const top = Math.max(0, box.top);
      const bottom = Math.min(window.innerHeight, box.bottom);
      if (
        point.x < box.left ||
        point.x > box.right ||
        point.y < top ||
        point.y > bottom ||
        top >= bottom
      )
        return;
      const edge = Math.min(56, (bottom - top) / 3);
      const delta =
        point.y < top + edge
          ? point.y - top - edge
          : point.y > bottom - edge
            ? point.y - bottom + edge
            : 0;
      if (!delta) return;
      const previous = list.scrollTop;
      list.scrollTop +=
        Math.sign(delta) * Math.ceil((18 * Math.abs(delta)) / edge);
      if (list.scrollTop !== previous) frame = requestAnimationFrame(scroll);
    }
    function track(event: globalThis.DragEvent) {
      point = { x: event.clientX, y: event.clientY };
      if (!frame) frame = requestAnimationFrame(scroll);
    }
    function leave(event: globalThis.DragEvent) {
      if (!event.relatedTarget && event.target === document.documentElement)
        point = null;
    }
    document.addEventListener("dragover", track, true);
    document.addEventListener("dragleave", leave, true);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("dragover", track, true);
      document.removeEventListener("dragleave", leave, true);
    };
  }, [dragged, section]);

  const editCanSteps =
    section === "execute" && draft.steps.some(step => isCanStep(step.type));
  useEffect(() => {
    let disposed = false;
    api<UiProject[]>("/ui/projects")
      .then(items => {
        if (!disposed) setProjects(items);
      })
      .catch(e => {
        if (!disposed) toast.error(e.message);
      });
    return () => {
      disposed = true;
    };
  }, []);
  useEffect(() => {
    if (section !== "images" && section !== "execute") return;
    let disposed = false;
    setCatalog(null);
    api<UiImage[]>(uiImagesPath(catalogId))
      .then(items => {
        if (!disposed) setCatalog({ id: catalogId, items });
      })
      .catch(e => {
        if (!disposed) {
          setCatalog({ id: catalogId, items: [] });
          toast.error(e.message);
        }
      });
    return () => {
      disposed = true;
    };
  }, [catalogId, section]);
  useEffect(() => {
    if (!editCanSteps) return;
    let disposed = false;
    const refreshCan = async () => {
      try {
        const [catalog, status] = await Promise.all([
          api<CanCatalog>("/can/dbc"),
          api<{ channels?: number[] }>("/can/status"),
        ]);
        if (!disposed) {
          setCanCatalog(catalog);
          setCanChannels(status.channels ?? []);
        }
      } catch {
        if (!disposed) {
          setCanCatalog(null);
          setCanChannels([]);
        }
      }
    };
    void refreshCan();
    window.addEventListener("focus", refreshCan);
    return () => {
      disposed = true;
      window.removeEventListener("focus", refreshCan);
    };
  }, [editCanSteps]);

  const refresh = useCallback(async () => {
    try {
      const [nextDevices, nextCases, nextRuns] = await Promise.all([
        api<typeof devices>("/devices"),
        api<Case[]>("/ui/cases"),
        api<Run[]>("/ui/runs"),
      ]);
      setDevices(nextDevices);
      setCases(nextCases);
      setRuns(nextRuns);
      setError("");
      setRunId(current => current || nextRuns.find(active)?.id || "");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => {
    if (!runId) {
      setRun(null);
      setEvents([]);
      return;
    }
    let disposed = false;
    let polling = false;
    let after = 0;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const [next, log, nextMetrics] = await Promise.all([
          api<Run>(`/ui/runs/${runId}`),
          api<Event[]>(`/ui/runs/${runId}/events?after=${after}`),
          api<StepMetric[]>(`/ui/runs/${runId}/metrics`).catch(() => []),
        ]);
        if (!disposed && selection.current === runId) {
          setRun(next);
          if (log.length) {
            after = log[log.length - 1].seq;
            setEvents(previous => [...previous, ...log]);
          }
          setMetrics(nextMetrics);
        }
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      } finally {
        polling = false;
      }
    };
    setRun(null);
    setEvents([]);
    setMetrics([]);
    void poll();
    const timer = window.setInterval(poll, 1500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [runId]);

  async function perform(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function createProject(name: string) {
    let created = false;
    await perform(async () => {
      const project = await api<UiProject>("/ui/projects", { name });
      setProjects(previous => [...previous, project]);
      setProjectId(project.id);
      created = true;
    });
    return created;
  }
  function load(test: Case) {
    const copy = structuredClone(test);
    setDraft({ ...copy, steps: expandElseBlocks(copy.steps, newElse) });
    setProjectId(copy.projectId ?? defaultProject.id);
    setParameters({});
    setCollapsed(new Set());
    showExecution();
  }
  function showExecution() {
    if (section !== "execute") navigate("/ui");
  }
  function addStep(parentId: string | null = null, asElse = false) {
    const step =
      asElse && parentId ? newElse(parentId) : { ...newStep(), parentId };
    setDraft(previous => {
      const rows = stepLayout(previous.steps);
      if (step.type !== "else" && stepLimitReached(previous.steps))
        return previous;
      if (
        step.type === "else" &&
        (previous.steps.find(item => item.id === parentId)?.type !== "if" ||
          previous.steps.some(
            item => item.parentId === parentId && item.branch === "else"
          ))
      )
        return previous;
      const insert = childInsertion(rows, parentId, step.branch ?? null);
      if (insert < 0) return previous;
      return {
        ...previous,
        steps: [
          ...previous.steps.slice(0, insert),
          step,
          ...previous.steps.slice(insert),
        ],
      };
    });
    window.requestAnimationFrame(() => {
      stepList.current
        ?.querySelector(`[data-step-id="${step.id}"]`)
        ?.scrollIntoView({ block: "nearest" });
    });
  }
  function patchStep(
    id: string,
    update: Partial<Step> | ((current: Step) => Partial<Step>)
  ) {
    setDraft(previous => ({
      ...previous,
      steps: previous.steps.map(step =>
        step.id === id
          ? {
              ...step,
              ...(typeof update === "function" ? update(step) : update),
            }
          : step
      ),
    }));
  }
  function move(index: number, delta: number) {
    setDraft(previous => {
      const { steps } = previous;
      const step = steps[index];
      const siblings = steps.filter(item => sameScope(item, step));
      const target = siblings[siblings.indexOf(step) + delta];
      if (!target) return previous;
      const before =
        delta < 0 ? target : siblings[siblings.indexOf(target) + 1];
      const drop = before
        ? { before: before.id }
        : { into: step.parentId ?? null };
      return { ...previous, steps: relocateStep(steps, step.id, drop) };
    });
  }
  function stopDragging() {
    setDragged(null);
    setHoverDrop(null);
  }
  function toggleCollapsed(id: string) {
    setCollapsed(previous => {
      const next = new Set(previous);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }
  function dropEvents(drop: StepDrop, key: string) {
    return {
      onDragOver(event: DragEvent<HTMLElement>) {
        event.stopPropagation();
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setHoverDrop(key);
      },
      onDragLeave(event: DragEvent<HTMLElement>) {
        if (
          !(
            event.relatedTarget instanceof Node &&
            event.currentTarget.contains(event.relatedTarget)
          )
        )
          setHoverDrop(current => (current === key ? null : current));
      },
      onDrop(event: DragEvent<HTMLElement>) {
        event.stopPropagation();
        event.preventDefault();
        if (dragged && event.dataTransfer.getData("text/plain") === dragged) {
          setDraft(previous => {
            const steps = relocateStep(previous.steps, dragged, drop);
            return steps === previous.steps ? previous : { ...previous, steps };
          });
          if ("into" in drop && drop.into !== null && collapsed.has(drop.into))
            toggleCollapsed(drop.into);
        }
        stopDragging();
      },
    };
  }
  function canDrop(drop: StepDrop) {
    return (
      dragged !== null &&
      relocateStep(draft.steps, dragged, drop) !== draft.steps
    );
  }
  function insertionZone(drop: StepDrop, key: string, label: string) {
    if (!canDrop(drop)) return null;
    return (
      <div
        {...dropEvents(drop, key)}
        data-drop-zone={key}
        className={`rounded border border-dashed px-2 py-0.5 text-xs ${hoverDrop === key ? "border-primary bg-primary/15 text-primary" : "border-border text-muted-foreground"}`}
      >
        {label}
      </div>
    );
  }
  async function showRecords(id?: string) {
    await devicePreview.current?.stop();
    if (id) setRunId(id);
    showExecution();
    setRightPanel("records");
  }
  async function start() {
    validateStepDraft();
    await devicePreview.current?.stop();
    const next = await api<Run>("/ui/runs", {
      serial: runSerial,
      testCase: draft,
      parameters,
    });
    setRunId(next.id);
    setRun(next);
    setRightPanel("records");
    setParameters({});
    await refresh();
  }
  async function openSettings() {
    setConfig(await api<Config>("/ui/settings"));
    setSettingsOpen(true);
  }
  async function exportCases() {
    const result = await fetch(`${API}/api/ui/cases/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: selected }),
    });
    if (!result.ok) throw new Error("导出失败，请刷新用例列表后重试");
    const url = URL.createObjectURL(await result.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "ui-cases.zip";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const activeRuns = runs.filter(active);
  const rows = stepLayout(draft.steps);
  const stepsFull = stepLimitReached(draft.steps);
  const draggingElse = draft.steps.some(
    step => step.id === dragged && step.type === "else"
  );
  const enabledSteps = rows
    .filter(row => row.active)
    .map(row => row.step.condition ?? row.step);
  const needsDevice =
    !enabledSteps.length ||
    enabledSteps.some(
      step =>
        !isCanStep(step.type) &&
        ![
          "delay",
          "for",
          "else",
          "assign",
          "variable_assert",
          "screenshot_assert",
        ].includes(step.type)
    );
  const runSerial = needsDevice ? serial : "";
  const runBusy =
    activeRuns.some(item => item.serial === runSerial) ||
    Boolean(run && run.serial === runSerial && active(run));
  const deviceRunning =
    !!serial &&
    (activeRuns.some(item => item.serial === serial) ||
      Boolean(run && run.serial === serial && active(run)));
  function validateStepDraft() {
    rows.forEach((row, index) => {
      const { step } = row;
      const prefix = `第 ${index + 1} 步`;
      const operation = step.condition ?? step;
      const options = operation.options;
      if (step.type === "for") {
        const { count, indexVariable } = options ?? {};
        if (
          count === undefined ||
          !Number.isInteger(count) ||
          count < 1 ||
          count > 100
        )
          throw new Error(`${prefix}请输入 1–100 次循环`);
        if (
          indexVariable &&
          (!/^[a-zA-Z0-9_]{1,64}$/.test(indexVariable) ||
            visibleVariables(row, index).includes(indexVariable))
        )
          throw new Error(`${prefix}的索引变量名称无效或与现有变量重名`);
      }
      if (
        operation.type === "variable_assert" &&
        (!options?.variable ||
          !visibleVariables(row, index).includes(options.variable))
      )
        throw new Error(`${prefix}请选择一个可用变量`);
      if (operation.type === "assign") {
        const {
          variable,
          valueType,
          booleanValue,
          numberValue,
          baselineId,
          sourceStepId,
        } = options ?? {};
        if (!variable || !/^[a-zA-Z0-9_]{1,64}$/.test(variable))
          throw new Error(`${prefix}的变量名称只能使用字母、数字或下划线`);
        if (
          row.ancestors.some(
            parent => parent.options?.indexVariable === variable
          )
        )
          throw new Error(`${prefix}不能给循环索引变量赋值`);
        if (valueType === "boolean" && typeof booleanValue !== "boolean")
          throw new Error(`${prefix}请选择布尔值`);
        if (valueType === "number" && !Number.isFinite(numberValue))
          throw new Error(`${prefix}请输入有效数值`);
        if (
          valueType === "image" &&
          !baselineId &&
          (!sourceStepId ||
            (row.active &&
              !priorCaptures(row, index).some(
                source => source.id === sourceStepId
              )))
        )
          throw new Error(`${prefix}请选择前面的截图或上传 PNG`);
      }
      if (operation.type === "assert" && !operation.prompt.trim())
        throw new Error(`${prefix}请填写断言条件`);
      if (operation.type === "image_assert" && !options?.baselineId)
        throw new Error(`${prefix}请从图片库选择基准图`);
      if (
        (operation.type === "element_screenshot" ||
          operation.type === "element_assert" ||
          (operation.type === "image_assert" && options?.mode === "element")) &&
        !Object.values(operation.selector ?? {}).some(value => value?.trim())
      )
        throw new Error(`${prefix}请从投屏选取元素或填写定位条件`);
      if (options?.mode === "roi") {
        const { left, top, right, bottom } = options.roi ?? {};
        if (
          left === undefined ||
          top === undefined ||
          right === undefined ||
          bottom === undefined ||
          [left, top, right, bottom].some(
            value => !Number.isInteger(value) || value < 0 || value > 32767
          ) ||
          left >= right ||
          top >= bottom
        )
          throw new Error(`${prefix}请填写有效的 ROI 左上角和右下角坐标`);
      }
      if (step.type === "delay") {
        const duration = options?.durationMs;
        if (
          duration === undefined ||
          !Number.isInteger(duration) ||
          duration < 1 ||
          duration > 300000
        )
          throw new Error(`${prefix}请输入 0.001–300 秒的等待时长`);
      }
      if (operation.type === "screenshot_assert") {
        const { firstStepId, secondStepId, firstVariable, secondVariable } =
          options ?? {};
        const first = firstVariable ? `var:${firstVariable}` : firstStepId;
        const second = secondVariable ? `var:${secondVariable}` : secondStepId;
        if (!first || !second || first === second)
          throw new Error(`${prefix}请选择两个不同的图片来源`);
        if (
          row.active &&
          ![first, second].every(source =>
            source.startsWith("var:")
              ? assignedVariables(index, "image").includes(source.slice(4))
              : priorCaptures(row, index).some(item => item.id === source)
          )
        )
          throw new Error(`${prefix}请选择前面已启用的截图或图片变量`);
      }
      if (!isCanStep(step.type)) return;
      if (!options?.dbcKey || !options.message || options.channel === undefined)
        throw new Error(`${prefix}请选择 DBC 报文和通道`);
      if (step.type !== "can_stop") {
        const signals = Object.values(options.signals ?? {});
        if (
          !signals.length ||
          signals.some(
            value =>
              value == null ||
              (typeof value === "number" && !Number.isFinite(value))
          )
        )
          throw new Error(`${prefix}请添加至少一个信号并填写信号值`);
      }
      if (
        step.type === "can_send_loop" &&
        options.cycleMs != null &&
        (!Number.isInteger(options.cycleMs) || options.cycleMs < 1)
      )
        throw new Error(`${prefix}的周期必须是正整数毫秒`);
    });
  }
  function assignedVariables(index: number, valueType?: "image") {
    return Array.from(
      new Set(
        rows
          .slice(0, index)
          .flatMap(({ step, active }) =>
            active &&
            step.type === "assign" &&
            step.options?.variable &&
            (!valueType || step.options.valueType === valueType)
              ? [step.options.variable]
              : []
          )
      )
    );
  }
  function visibleVariables(row: (typeof rows)[number], index: number) {
    return Array.from(
      new Set([
        ...draft.parameters.map(parameter => parameter.name).filter(Boolean),
        ...assignedVariables(index),
        ...row.ancestors.flatMap(parent =>
          parent.options?.indexVariable ? [parent.options.indexVariable] : []
        ),
      ])
    );
  }
  function priorCaptures(row: (typeof rows)[number], index: number) {
    return rows.slice(0, index).flatMap((source, position) =>
      isCapture(source.step.type) &&
      source.step.enabled &&
      (!row.active || source.active) &&
      source.path.every((entry, depth) => entry === row.path[depth])
        ? [
            {
              id: source.step.id,
              label: `第 ${position + 1} 步 · ${source.step.options?.captureName?.trim() || source.step.name?.trim() || source.step.prompt.trim() || stepTypes[source.step.type]}`,
            },
          ]
        : []
    );
  }
  const pickTargets = draft.steps.flatMap((step, index) => {
    const operation = step.condition ?? step;
    const label =
      operation.type === "image_assert" && operation.options?.mode === "element"
        ? "元素区域断言"
        : operation.type === "tap" && operation.selector
          ? "结构点击"
          : ["element_assert", "element_screenshot"].includes(operation.type)
            ? stepTypes[operation.type]
            : "";
    return label
      ? [{ id: step.id, label: `第 ${index + 1} 步 · ${label}` }]
      : [];
  });
  const pickTarget = pickTargets.some(item => item.id === pickStepId)
    ? pickStepId
    : pickTargets.length === 1
      ? pickTargets[0].id
      : "";
  function applyElement(selector: Selector) {
    if (!pickTarget || !Object.values(selector).some(Boolean)) return;
    patchStep(pickTarget, step =>
      step.condition
        ? { condition: { ...step.condition, selector } }
        : { selector }
    );
    toast.success("元素定位条件已回填到步骤");
  }

  return (
    <main
      className={`min-h-screen bg-background text-foreground ${section === "execute" ? "px-3 py-3 md:px-5" : "px-5 py-6 md:px-8"}`}
    >
      <header
        className={`flex flex-wrap items-center justify-between gap-2 border-b border-border ${section === "execute" ? "mb-3 pb-2" : "mb-5 pb-5"}`}
      >
        <div>
          <h1
            className={`font-semibold ${section === "execute" ? "text-lg" : "text-2xl"}`}
          >
            {section === "images" ? "图片库" : "UI 自动化测试"}
          </h1>
          {section !== "execute" && section !== "images" && (
            <p className="mt-2 text-sm text-muted-foreground">
              用自然语言描述操作，用独立断言检查结果。脚本可逐步编辑和复跑。
            </p>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void perform(openSettings)}
        >
          <Settings className="size-4" />
          设置
        </Button>
      </header>
      {error && (
        <p
          role="alert"
          className="mb-4 rounded border border-destructive/40 p-3 text-sm text-destructive"
        >
          {error}
        </p>
      )}
      {activeRuns.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded border border-cyan-700/50 bg-cyan-950/20 p-3 text-sm">
          <span>{activeRuns.length} 个 UI 任务正在运行</span>
          {activeRuns.map(item => (
            <button
              key={item.id}
              className="underline"
              onClick={() => void perform(() => showRecords(item.id))}
            >
              {item.testCase.name} · {item.serial || "本机执行"}
            </button>
          ))}
        </div>
      )}
      {section === "structure" && (
        <PageMapWorkbench
          projects={projects}
          projectId={projectId}
          onProjectChange={setProjectId}
        />
      )}
      {section === "images" && (
        <UiImageLibrary
          projects={projects}
          projectId={projectId}
          onSelect={setProjectId}
          images={images}
          loading={catalog?.id !== catalogId}
          busy={busy}
          onCreate={createProject}
        />
      )}
      {section === "execute" && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(300px,0.7fr)]">
          <section className="@container min-w-0 space-y-2.5">
            <div className="flex flex-wrap gap-1.5">
              <Button
                variant="outline"
                size="sm"
                onClick={() => load(blank(projectId))}
              >
                <Plus className="size-4" />
                新用例
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-x-2.5 gap-y-1.5 @min-[620px]:grid-cols-[minmax(0,1.25fr)_minmax(0,1.25fr)_minmax(0,0.8fr)_minmax(0,0.75fr)_minmax(0,0.75fr)]">
              <label className="grid min-w-0 gap-1 text-xs font-medium text-muted-foreground">
                用例名称
                <Input
                  className={compactInput}
                  value={draft.name}
                  onChange={e => setDraft({ ...draft, name: e.target.value })}
                />
              </label>
              <label className="grid min-w-0 gap-1 text-xs font-medium text-muted-foreground">
                目标设备
                <select
                  className={selectClass}
                  value={serial}
                  onChange={e => setSerial(e.target.value)}
                >
                  <option value="">选择已授权的设备</option>
                  {devices.map(device => (
                    <option
                      key={device.serial}
                      value={device.serial}
                      disabled={device.state !== "device"}
                    >
                      {device.model || device.serial} · {device.serial} ·{" "}
                      {device.state}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid min-w-0 gap-1 text-xs font-medium text-muted-foreground">
                项目
                <select
                  className={selectClass}
                  value={caseProjectId}
                  onChange={e => {
                    const id = e.target.value;
                    setDraft(previous => ({ ...previous, projectId: id }));
                    setProjectId(id);
                  }}
                >
                  <UiProjectOptions projects={projects} />
                </select>
              </label>
              <label className="grid min-w-0 gap-1 text-xs font-medium text-muted-foreground">
                分组
                <Input
                  className={compactInput}
                  value={draft.group}
                  onChange={e => setDraft({ ...draft, group: e.target.value })}
                  placeholder="例如：系统设置"
                />
              </label>
              <label className="grid min-w-0 gap-1 text-xs font-medium text-muted-foreground">
                标签（逗号分隔）
                <Input
                  className={compactInput}
                  value={draft.tags.join(",")}
                  onChange={e =>
                    setDraft({ ...draft, tags: e.target.value.split(",") })
                  }
                />
              </label>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-medium">执行参数</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      parameters: [
                        ...draft.parameters,
                        {
                          name: `PARAM_${draft.parameters.length + 1}`,
                          secret: false,
                          default: "",
                        },
                      ],
                    })
                  }
                >
                  <Plus className="size-4" />
                  添加参数
                </Button>
              </div>
              {draft.parameters.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  步骤内使用 {"${参数名}"}{" "}
                  引用。敏感参数只在本次运行中使用；含敏感参数的任务暂不保存截图和
                  Midscene 原始报告。
                </p>
              )}
              {draft.parameters.length > 0 && (
                <div className="max-h-32 space-y-1 overflow-y-auto overscroll-contain pr-1">
                  {draft.parameters.map((param, i) => (
                    <div
                      key={i}
                      className="grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2"
                    >
                      <Input
                        className={compactInput}
                        aria-label={`参数 ${i + 1} 名称`}
                        value={param.name}
                        onChange={e => {
                          setParameters({});
                          setDraft({
                            ...draft,
                            parameters: draft.parameters.map((p, j) =>
                              j === i ? { ...p, name: e.target.value } : p
                            ),
                          });
                        }}
                      />
                      <Input
                        className={compactInput}
                        aria-label={`${param.name} 参数值`}
                        type={param.secret ? "password" : "text"}
                        autoComplete="off"
                        placeholder={param.secret ? "仅本次使用" : "默认值"}
                        value={
                          param.secret
                            ? parameters[param.name] || ""
                            : param.default
                        }
                        onChange={e =>
                          param.secret
                            ? setParameters({
                                ...parameters,
                                [param.name]: e.target.value,
                              })
                            : setDraft({
                                ...draft,
                                parameters: draft.parameters.map((p, j) =>
                                  j === i
                                    ? { ...p, default: e.target.value }
                                    : p
                                ),
                              })
                        }
                      />
                      <label className="text-xs flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={param.secret}
                          onChange={e => {
                            setParameters({});
                            setDraft({
                              ...draft,
                              parameters: draft.parameters.map((p, j) =>
                                j === i
                                  ? {
                                      ...p,
                                      secret: e.target.checked,
                                      default: "",
                                    }
                                  : p
                              ),
                            });
                          }}
                        />
                        敏感
                      </label>
                      <Button
                        aria-label="删除参数"
                        size="icon-sm"
                        variant="ghost"
                        onClick={() =>
                          setDraft({
                            ...draft,
                            parameters: draft.parameters.filter(
                              (_, j) => j !== i
                            ),
                          })
                        }
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-medium">
                测试步骤{" "}
                <span className="text-muted-foreground">
                  / {draft.steps.length}
                </span>
              </h2>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  v{draft.version}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={stepsFull}
                  onClick={() => addStep()}
                >
                  <Plus className="size-3.5" />
                  添加步骤
                </Button>
              </div>
            </div>
            <div
              ref={stepList}
              aria-label="测试步骤列表"
              onDragOver={event => {
                if (dragged) event.preventDefault();
              }}
              className="max-h-[clamp(260px,calc(100dvh-255px),365px)] space-y-1.5 overflow-y-auto overscroll-contain p-0.5"
            >
              {rows.map((row, i) => {
                const { step, indent, end } = row;
                if (
                  row.ancestors.some(
                    (ancestor, depth) =>
                      isBlock(ancestor.type) &&
                      collapsed.has(ancestor.id) &&
                      row.path[depth] !== `${ancestor.id}:else`
                  )
                )
                  return null;
                const folded = isBlock(step.type) && collapsed.has(step.id);
                const childCount =
                  (step.type === "if" ? childInsertion(rows, step.id) : end) -
                  i -
                  1;
                const hasPrevious = draft.steps
                  .slice(0, i)
                  .some(other => sameScope(other, step));
                const hasNext = draft.steps
                  .slice(end)
                  .some(other => sameScope(other, step));
                const into = { into: step.id };
                const insideKey = `into-${step.id}`;
                const acceptsChildren = isBlock(step.type) && canDrop(into);
                const hasElse =
                  step.type === "if" &&
                  draft.steps.some(
                    child =>
                      child.parentId === step.id && child.branch === "else"
                  );
                return (
                  <article
                    key={step.id}
                    data-step-id={step.id}
                    data-step-type={step.type}
                    data-drop-parent={acceptsChildren ? step.id : undefined}
                    {...(acceptsChildren ? dropEvents(into, insideKey) : {})}
                    style={{
                      marginInlineStart: Math.min(indent * 14, 126),
                    }}
                    className={`space-y-1.5 rounded-md border border-border bg-card p-1.5 text-card-foreground ${indent ? "border-l-2 border-l-primary/40" : ""} ${!row.active || dragged === step.id ? "opacity-50" : ""} ${hoverDrop === insideKey ? "ring-2 ring-primary/60" : ""}`}
                  >
                    {insertionZone(
                      { before: step.id },
                      `before-${step.id}`,
                      "放在此步骤之前"
                    )}
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span
                        draggable
                        aria-label={`拖动步骤 ${i + 1}`}
                        title={
                          step.type === "else"
                            ? "拖到另一个没有 Else 的 If"
                            : "拖动步骤，靠近列表上下边缘可自动滚动"
                        }
                        className="flex size-6 shrink-0 cursor-grab items-center justify-center rounded text-muted-foreground hover:bg-muted active:cursor-grabbing"
                        onDragStart={event => {
                          event.dataTransfer.effectAllowed = "move";
                          event.dataTransfer.setData("text/plain", step.id);
                          setDragged(step.id);
                        }}
                        onDragEnd={stopDragging}
                      >
                        <GripVertical aria-hidden="true" className="size-4" />
                      </span>
                      <label className="flex shrink-0 items-center gap-1.5 font-mono text-xs">
                        <input
                          aria-label={`启用步骤 ${i + 1}`}
                          type="checkbox"
                          checked={step.enabled}
                          onChange={e =>
                            patchStep(step.id, { enabled: e.target.checked })
                          }
                        />
                        {String(i + 1).padStart(2, "0")}
                      </label>
                      <Input
                        className="h-7 min-w-24 flex-1 px-2 shadow-none"
                        aria-label={`步骤 ${i + 1} 名称`}
                        placeholder="步骤名称"
                        maxLength={120}
                        value={step.name ?? ""}
                        onChange={event =>
                          patchStep(step.id, { name: event.target.value })
                        }
                      />
                      {step.type === "else" ? (
                        <div className="min-w-28 flex-1 rounded-md bg-muted px-2 py-1 text-sm font-medium">
                          {stepTypes.else}
                        </div>
                      ) : (
                        <div className="relative min-w-28 flex-1">
                          <select
                            aria-label={`步骤 ${i + 1} 类型`}
                            className={`${selectClass} h-7 appearance-none pr-9`}
                            disabled={end > i + 1}
                            title={
                              end > i + 1 ? "移除子步骤后可更改类型" : undefined
                            }
                            value={
                              step.type === "tap" && step.selector
                                ? "structured_tap"
                                : step.type
                            }
                            onChange={event =>
                              patchStep(
                                step.id,
                                changeStepType(
                                  step,
                                  event.target.value as StepType
                                )
                              )
                            }
                          >
                            {stepGroups.map(group => (
                              <optgroup key={group.label} label={group.label}>
                                {group.types.map(type => (
                                  <option key={type} value={type}>
                                    {stepTypes[type]}
                                  </option>
                                ))}
                                {group.label === "页面操作" &&
                                  step.type === "tap" &&
                                  step.selector && (
                                    <option value="structured_tap">
                                      结构点击
                                    </option>
                                  )}
                              </optgroup>
                            ))}
                          </select>
                          <ChevronDown
                            aria-hidden="true"
                            className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
                          />
                        </div>
                      )}
                      {step.type !== "delay" &&
                        step.type !== "for" &&
                        step.type !== "else" && (
                          <label className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
                            超时（秒）
                            <Input
                              className="h-7 w-14 px-1.5 shadow-none"
                              aria-label={`步骤 ${i + 1} 超时（秒）`}
                              type="number"
                              min={1}
                              max={300}
                              value={step.timeoutMs / 1000}
                              onChange={e =>
                                patchStep(step.id, {
                                  timeoutMs: Number(e.target.value) * 1000,
                                })
                              }
                            />
                          </label>
                        )}
                      {isAssertion(step.type) && (
                        <label className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
                          <input
                            type="checkbox"
                            checked={step.onFailure === "continue"}
                            onChange={e =>
                              patchStep(step.id, {
                                onFailure: e.target.checked
                                  ? "continue"
                                  : "stop",
                              })
                            }
                          />
                          断言失败后继续
                        </label>
                      )}
                      <div className="ml-auto flex shrink-0 items-center gap-0.5">
                        {isBlock(step.type) && (
                          <Button
                            type="button"
                            size="sm"
                            className="h-7 gap-1 px-1.5"
                            variant="ghost"
                            aria-label={`${folded ? "展开" : "收起"}步骤 ${i + 1}`}
                            aria-expanded={!folded}
                            onClick={() => toggleCollapsed(step.id)}
                          >
                            {folded ? (
                              <ChevronRight className="size-4" />
                            ) : (
                              <ChevronDown className="size-4" />
                            )}
                            {folded && childCount > 0 && (
                              <span className="text-xs text-muted-foreground">
                                {childCount} 步
                              </span>
                            )}
                          </Button>
                        )}
                        <Button
                          size="icon-sm"
                          className="size-7"
                          variant="ghost"
                          aria-label="上移步骤"
                          disabled={!hasPrevious}
                          onClick={() => move(i, -1)}
                        >
                          <ArrowUp className="size-4" />
                        </Button>
                        <Button
                          size="icon-sm"
                          className="size-7"
                          variant="ghost"
                          aria-label="下移步骤"
                          disabled={!hasNext}
                          onClick={() => move(i, 1)}
                        >
                          <ArrowDown className="size-4" />
                        </Button>
                        <Button
                          size="icon-sm"
                          className="size-7"
                          variant="ghost"
                          aria-label={
                            end > i + 1 ? "删除步骤及子步骤" : "删除步骤"
                          }
                          disabled={draft.steps.length === end - i}
                          onClick={() =>
                            setDraft({
                              ...draft,
                              steps: [
                                ...draft.steps.slice(0, i),
                                ...draft.steps.slice(end),
                              ],
                            })
                          }
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </div>
                    {!folded && (
                      <UiStepFields
                        key={step.type}
                        step={step}
                        index={i}
                        canCatalog={canCatalog}
                        canChannels={canChannels}
                        images={images}
                        projectId={caseProjectId}
                        screenshots={
                          ["screenshot_assert", "assign"].includes(
                            (step.condition ?? step).type
                          )
                            ? priorCaptures(row, i)
                            : []
                        }
                        variables={visibleVariables(row, i)}
                        imageVariables={assignedVariables(i, "image")}
                        onPickElement={
                          serial && !deviceRunning
                            ? () => {
                                setPickStepId(step.id);
                                setRightPanel("device");
                              }
                            : undefined
                        }
                        pickingElement={
                          rightPanel === "device" && pickTarget === step.id
                        }
                        onChange={patch => patchStep(step.id, patch)}
                      />
                    )}
                    {isBlock(step.type) && !folded && (
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={stepsFull}
                          onClick={() => addStep(step.id)}
                        >
                          <Plus className="size-3.5" />
                          {step.type === "for"
                            ? "添加循环步骤"
                            : step.type === "else"
                              ? "添加 Else 子步骤"
                              : "添加 If 步骤"}
                        </Button>
                        {step.type === "if" && !hasElse && (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => addStep(step.id, true)}
                          >
                            <Plus className="size-3.5" />
                            添加 Else 步骤
                          </Button>
                        )}
                      </div>
                    )}
                    {acceptsChildren && (
                      <div className="rounded border border-dashed border-primary/50 bg-primary/5 px-2 py-1 text-xs text-primary">
                        {draggingElse
                          ? "拖到此处作为 Else 分支"
                          : "拖到此处作为子步骤"}
                      </div>
                    )}
                  </article>
                );
              })}
              {insertionZone({ into: null }, "root-end", "放到最外层末尾")}
            </div>
            <div className="sticky bottom-0 z-10 flex flex-wrap gap-2 border-t border-border bg-background py-2">
              <Button
                size="sm"
                disabled={busy || (needsDevice && !serial) || runBusy}
                onClick={() => void perform(start)}
              >
                <Play className="size-4" />
                执行用例
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    validateStepDraft();
                    const saved = await api<Case>("/ui/cases", draft);
                    setDraft(saved);
                    await refresh();
                    toast.success("用例已保存");
                  })
                }
              >
                <Save className="size-4" />
                保存到用例库
              </Button>
            </div>
          </section>
          <section className="min-w-0 xl:border-l xl:border-border xl:pl-4">
            <div aria-label="右侧面板" className="mb-3 flex gap-1.5">
              <Button
                type="button"
                size="sm"
                variant={rightPanel === "device" ? "secondary" : "ghost"}
                aria-pressed={rightPanel === "device"}
                onClick={() => setRightPanel("device")}
              >
                车机投屏
              </Button>
              <Button
                type="button"
                size="sm"
                variant={rightPanel === "records" ? "secondary" : "ghost"}
                aria-pressed={rightPanel === "records"}
                disabled={busy}
                onClick={() => void perform(() => showRecords())}
              >
                执行记录
              </Button>
            </div>
            {rightPanel === "device" ? (
              <UiDevicePreview
                key={serial}
                ref={devicePreview}
                serial={serial}
                disabled={busy}
                running={deviceRunning}
                targets={pickTargets}
                target={pickTarget}
                onTargetChange={setPickStepId}
                onApply={applyElement}
              />
            ) : (
              <>
                <div className="flex justify-between items-center mb-4">
                  <h2 className="font-medium">执行记录</h2>
                  {run && active(run) && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          setRun(
                            await api<Run>(`/ui/runs/${run.id}/cancel`, {})
                          );
                          await refresh();
                        })
                      }
                    >
                      <Square className="size-3" />
                      取消执行
                    </Button>
                  )}
                </div>
                {!run ? (
                  <div className="py-14 text-center text-muted-foreground">
                    <FlaskConical className="mx-auto mb-4 size-9" />
                    <p>选择设备并执行，步骤结果会显示在这里。</p>
                  </div>
                ) : (
                  <>
                    <div className="mb-5 space-y-2 text-sm">
                      <h3 className="font-medium">{run.testCase.name}</h3>
                      <p className="text-cyan-400">
                        {states[run.state] || run.state} ·{" "}
                        {run.serial || "本机执行"}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {new Date(run.createdAt).toLocaleString()} · 脚本 v
                        {run.testCase.version}
                      </p>
                      {run.message && (
                        <p role="alert" className="text-destructive">
                          {run.message}
                        </p>
                      )}
                      {run.performanceSessionId && (
                        <PerformanceReportDialog
                          url={`${API}/api/sessions/${run.performanceSessionId}/report`}
                        >
                          <button type="button" className="block underline">
                            查看关联性能报告
                          </button>
                        </PerformanceReportDialog>
                      )}
                      {run.report && (
                        <a
                          className="block underline"
                          target="_blank"
                          rel="noreferrer"
                          href={`${API}/api/ui/runs/${run.id}/artifacts/report.html`}
                        >
                          打开 Midscene 原始报告
                        </a>
                      )}
                      <a
                        className="block underline"
                        href={`${API}/api/ui/runs/${run.id}/report`}
                      >
                        下载结构化报告
                      </a>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy || active(run)}
                        onClick={() =>
                          void perform(async () => {
                            const result = await api<{
                              testCase: Case;
                              warnings: string[];
                            }>(`/ui/runs/${run.id}/draft`);
                            load(result.testCase);
                            result.warnings.forEach(warning =>
                              toast.info(warning)
                            );
                          })
                        }
                      >
                        从实际执行生成可编辑草稿
                      </Button>
                    </div>
                    <ol className="space-y-4">
                      {run.testCase.steps.map((step, i) => {
                        const log = events.filter(
                          e => e.type === "step" && e.stepId === step.id
                        );
                        const completed = log.filter(
                          e => !["running", "skipped"].includes(e.state)
                        );
                        const last =
                          log.at(-1)?.state === "running"
                            ? log.at(-1)
                            : (completed.at(-1) ?? log.at(-1));
                        const screenshot = [...log]
                          .reverse()
                          .find(e => e.screenshot)?.screenshot;
                        const stepType =
                          step.type === "tap" && step.selector
                            ? "结构点击"
                            : stepTypes[step.type];
                        const stepName = step.name?.trim();
                        return (
                          <li
                            key={step.id}
                            className="border-l-2 border-border pl-4"
                          >
                            <div className="flex justify-between gap-3 text-sm">
                              <span className="min-w-0 break-words">
                                {i + 1}. {stepName || stepType}
                                {stepName && (
                                  <span className="ml-2 text-xs text-muted-foreground">
                                    {stepType}
                                  </span>
                                )}
                              </span>
                              <span
                                className={
                                  last?.state === "passed"
                                    ? "text-emerald-400"
                                    : last?.state === "failed" ||
                                        last?.state === "error"
                                      ? "text-red-400"
                                      : "text-muted-foreground"
                                }
                              >
                                {last
                                  ? states[last.state] || last.state
                                  : active(run)
                                    ? "等待执行"
                                    : "未执行"}
                              </span>
                            </div>
                            <p className="mt-2 text-sm break-words">
                              {step.prompt.replace(
                                /\$\{([a-zA-Z0-9_]+)\}/g,
                                (placeholder, name: string) => {
                                  if (
                                    run.testCase.parameters.some(
                                      p => p.name === name && p.secret
                                    )
                                  )
                                    return "[敏感参数]";
                                  if (
                                    run.testCase.steps.some(
                                      item =>
                                        item.type === "assign" &&
                                        item.options?.variable === name
                                    )
                                  )
                                    return placeholder;
                                  return run.parameters &&
                                    Object.hasOwn(run.parameters, name)
                                    ? run.parameters[name]
                                    : placeholder;
                                }
                              )}
                            </p>
                            {events
                              .filter(
                                e =>
                                  e.type === "navigation" &&
                                  e.stepId === step.id
                              )
                              .map(e => (
                                <p
                                  key={e.seq}
                                  className="mt-1 text-xs text-muted-foreground"
                                >
                                  {e.message} · 导航总耗时{" "}
                                  {((e.elapsedMs || 0) / 1000).toFixed(1)} 秒
                                </p>
                              ))}
                            {events
                              .filter(
                                e => e.type === "action" && e.stepId === step.id
                              )
                              .map(e => (
                                <p
                                  key={e.seq}
                                  className="mt-1 text-xs text-muted-foreground"
                                >
                                  已执行：{e.action} · {e.target || "设备操作"}
                                </p>
                              ))}
                            {events
                              .filter(
                                e =>
                                  e.type === "exploration" &&
                                  e.stepId === step.id
                              )
                              .map(e => (
                                <p
                                  key={e.seq}
                                  className="mt-1 text-xs text-muted-foreground"
                                >
                                  {e.control}：{states[e.state] || e.state} ·{" "}
                                  {e.message}
                                </p>
                              ))}
                            {last?.message && (
                              <p
                                className={`mt-2 text-xs ${last.state === "passed" ? "text-muted-foreground" : "text-destructive"}`}
                              >
                                {last.message}
                              </p>
                            )}
                            {metrics
                              .filter(metric => metric.stepId === step.id)
                              .map(metric => (
                                <p
                                  key={metric.sessionId}
                                  className="mt-2 rounded bg-muted/40 p-2 text-xs text-muted-foreground"
                                >
                                  步骤时间窗 · {metric.sampleCount} 个采样点
                                  <br />
                                  平均 CPU{" "}
                                  {metric.cpuAverage == null
                                    ? "—"
                                    : metric.cpuAverage.toFixed(1) + "%"}{" "}
                                  · 平均 PSS{" "}
                                  {metric.memoryAverageMb == null
                                    ? "—"
                                    : metric.memoryAverageMb.toFixed(1) +
                                      " MiB"}{" "}
                                  · 平均呈现帧率{" "}
                                  {metric.fpsAverage == null
                                    ? "—"
                                    : metric.fpsAverage.toFixed(1) + " FPS"}
                                </p>
                              ))}
                            {last && (
                              <p className="mt-1 text-xs text-muted-foreground">
                                {last.iterations?.length
                                  ? `循环第 ${last.iterations.join(".")} 轮 · `
                                  : ""}
                                {new Date(last.time).toLocaleTimeString()}
                              </p>
                            )}
                            {completed.length > 1 && (
                              <details className="mt-1 text-xs text-muted-foreground">
                                <summary className="cursor-pointer">
                                  各轮执行结果（{completed.length}）
                                </summary>
                                <ul className="mt-1 space-y-1 pl-4">
                                  {completed.map(event => (
                                    <li key={event.seq}>
                                      {event.iterations?.join(".")} ·{" "}
                                      {states[event.state] || event.state}
                                      {event.message && ` · ${event.message}`}
                                      {event.screenshot && (
                                        <a
                                          className="ml-2 underline"
                                          target="_blank"
                                          rel="noreferrer"
                                          href={`${API}/api/ui/runs/${run.id}/artifacts/${event.screenshot}`}
                                        >
                                          查看截图
                                        </a>
                                      )}
                                    </li>
                                  ))}
                                </ul>
                              </details>
                            )}
                            {screenshot && (
                              <a
                                href={`${API}/api/ui/runs/${run.id}/artifacts/${screenshot}`}
                                target="_blank"
                                rel="noreferrer"
                              >
                                <img
                                  className="mt-3 max-h-64 rounded border border-border object-contain"
                                  src={`${API}/api/ui/runs/${run.id}/artifacts/${screenshot}`}
                                  alt={`步骤 ${i + 1} 执行后截图`}
                                />
                              </a>
                            )}
                          </li>
                        );
                      })}
                    </ol>
                  </>
                )}
              </>
            )}
          </section>
        </div>
      )}
      {section === "cases" && (
        <section>
          <div className="flex justify-between items-center mb-5">
            <h2 className="font-medium">已保存用例 · {cases.length}</h2>
            <Button
              variant="outline"
              disabled={!selected.length || busy}
              onClick={() => void perform(exportCases)}
            >
              <Download className="size-4" />
              导出已选 ZIP（{selected.length}）
            </Button>
          </div>
          {!cases.length && (
            <p className="py-12 text-muted-foreground">
              尚未保存用例。在“执行与编辑”中创建步骤后保存。
            </p>
          )}
          {projects.map(project => {
            const projectCases = cases.filter(
              test => (test.projectId ?? defaultProject.id) === project.id
            );
            if (!projectCases.length) return null;
            return (
              <section
                key={project.id}
                className="mb-5 rounded-md border border-border"
              >
                <h3 className="border-b border-border bg-muted/50 px-4 py-2 text-sm font-medium">
                  {project.name}{" "}
                  <span className="text-muted-foreground">
                    · {projectCases.length} 个用例
                  </span>
                </h3>
                <ul className="divide-y divide-border px-4">
                  {projectCases.map(test => (
                    <li
                      key={test.id}
                      className="flex flex-wrap items-center gap-4 py-4"
                    >
                      <input
                        aria-label={`选择 ${test.name}`}
                        type="checkbox"
                        checked={selected.includes(test.id)}
                        onChange={e =>
                          setSelected(
                            e.target.checked
                              ? [...selected, test.id]
                              : selected.filter(id => id !== test.id)
                          )
                        }
                      />
                      <div className="flex-1">
                        <p>{test.name}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {test.group || "未分组"} · {test.steps.length} 步 · v
                          {test.version}{" "}
                          {test.tags
                            .filter(Boolean)
                            .map(t => `#${t}`)
                            .join(" ")}
                        </p>
                      </div>
                      <Button variant="outline" onClick={() => load(test)}>
                        编辑 / 复跑
                      </Button>
                      <Button
                        aria-label={`删除 ${test.name}`}
                        variant="ghost"
                        size="icon"
                        onClick={() =>
                          void perform(async () => {
                            await api(`/ui/cases/${test.id}/delete`, {});
                            setSelected(selected.filter(id => id !== test.id));
                            await refresh();
                          })
                        }
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </section>
      )}
      {section === "reports" && (
        <section>
          <h2 className="font-medium mb-5">运行历史 · {runs.length}</h2>
          {!runs.length && (
            <p className="py-12 text-muted-foreground">
              尚无运行记录，实际执行后生成报告。
            </p>
          )}
          <ul className="divide-y divide-border">
            {runs.map(item => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-4 py-4"
              >
                <div>
                  <p>
                    {item.testCase.name}{" "}
                    <span className="ml-2 text-sm text-cyan-400">
                      {states[item.state] || item.state}
                    </span>
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.serial || "本机执行"} ·{" "}
                    {new Date(item.createdAt).toLocaleString()} ·{" "}
                    {item.endedAt
                      ? `${Math.round((item.endedAt - item.createdAt) / 1000)} 秒`
                      : "运行中"}
                  </p>
                </div>
                <Button
                  variant="outline"
                  onClick={() => void perform(() => showRecords(item.id))}
                >
                  查看步骤与报告
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>UI 自动化设置</DialogTitle>
            <DialogDescription>
              模型接收设备截图进行判断。配置保存在测试主机，密钥不会返回到页面。
            </DialogDescription>
          </DialogHeader>
          {config && (
            <div className="space-y-4">
              {(
                [
                  ["baseUrl", "模型接口地址"],
                  ["model", "模型名称"],
                  ["family", "模型族"],
                  ["reportRoot", "主机报告输出目录"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="block space-y-2 text-sm">
                  {label}
                  <Input
                    value={config[key]}
                    placeholder={
                      key === "reportRoot"
                        ? "留空使用 backend/ui-data/runs"
                        : ""
                    }
                    onChange={e =>
                      setConfig({ ...config, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
              <label className="block space-y-2 text-sm">
                API Key
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={config.apiKey || ""}
                  placeholder={
                    config.hasKey
                      ? "已配置；留空保留"
                      : "在本机填写，或通过 .env 提供"
                  }
                  onChange={e =>
                    setConfig({
                      ...config,
                      apiKey: e.target.value || undefined,
                    })
                  }
                />
              </label>
              <p className="text-xs text-muted-foreground">
                {config.hasKey
                  ? "已保存密钥"
                  : config.envFileExists
                    ? "发现 .env 文件，请用环境检查确认密钥配置"
                    : "尚未配置密钥"}
                。报告路径必须是主机绝对路径。
              </p>
              <div className="flex gap-2">
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      const { hasKey, envFileExists, ...value } = config;
                      setConfig(await api<Config>("/ui/settings", value));
                      toast.success("设置已保存");
                    })
                  }
                >
                  保存设置
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      setDoctor(await api("/ui/doctor"));
                    })
                  }
                >
                  <RefreshCw className="size-4" />
                  检查已保存配置
                </Button>
              </div>
              {doctor && (
                <div className="rounded border border-border p-3 text-sm space-y-1">
                  <p>Node.js：{doctor.node}</p>
                  <p>ADB：{doctor.adb ? "可用" : "不可用"}</p>
                  <p>Midscene：{doctor.midscene ? "已安装" : "未安装"}</p>
                  <p>
                    模型密钥：{doctor.model.keyConfigured ? "已配置" : "未配置"}
                  </p>
                  <p className="pt-2 text-xs text-muted-foreground">
                    环境检查不会调用模型。保存设置后运行只读用例，验证真实视觉判断。
                  </p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </main>
  );
}
