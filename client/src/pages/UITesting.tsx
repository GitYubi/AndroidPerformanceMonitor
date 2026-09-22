import PageMapWorkbench from "@/components/PageMapWorkbench";
import { useCallback, useEffect, useRef, useState } from "react";
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
  Trash2,
  RefreshCw,
  FlaskConical,
} from "lucide-react";
import { toast } from "sonner";

const API = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8090";
type StepType =
  | "action"
  | "tap"
  | "input"
  | "wait"
  | "assert"
  | "screenshot"
  | "explore";
type Step = {
  id: string;
  type: StepType;
  prompt: string;
  value: string;
  timeoutMs: number;
  enabled: boolean;
  onFailure: "stop" | "continue";
  exploration?: { allowedControls: string[]; maxActions: number } | null;
};
type Parameter = { name: string; secret: boolean; default: string };
type Case = {
  schemaVersion: 1;
  id: string;
  version: number;
  name: string;
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
const stepTypes: Record<StepType, string> = {
  action: "自然语言操作",
  tap: "点击",
  input: "输入",
  wait: "等待条件",
  assert: "结果断言",
  screenshot: "记录截图",
  explore: "单页开关探索",
};
const active = (run: Run) => ["queued", "running"].includes(run.state);
const uid = () => crypto.randomUUID().replaceAll("-", "");
const newStep = (type: StepType = "action", prompt = "", value = ""): Step => ({
  id: uid(),
  type,
  prompt,
  value,
  timeoutMs: 60000,
  enabled: true,
  onFailure: "stop",
});
const blank = (): Case => ({
  schemaVersion: 1,
  id: uid(),
  version: 1,
  name: "新的 UI 用例",
  group: "",
  tags: [],
  parameters: [],
  steps: [newStep()],
});
const selectClass =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm";

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

function template(kind: string): Case {
  const test = blank();
  test.group = "系统设置";
  if (kind === "observe") {
    test.name = "只读画面检查";
    test.steps = [
      newStep("screenshot", "当前画面"),
      newStep("assert", "当前屏幕有可辨认的界面内容，不是黑屏或空白画面"),
    ];
  } else if (kind === "wifi") {
    test.name = "连接指定 Wi-Fi";
    test.parameters = [
      { name: "SSID", secret: false, default: "YuBiL" },
      { name: "PASSWORD", secret: true, default: "" },
    ];
    test.steps = [
      newStep("action", "打开应用列表，进入系统设置的 WLAN 页面"),
      newStep("action", "如果 WLAN 开关未打开，则打开；如果已打开则保持"),
      newStep("tap", "名称为 ${SSID} 的 Wi-Fi 网络"),
      newStep("input", "Wi-Fi 密码输入框", "${PASSWORD}"),
      newStep("tap", "连接按钮"),
      newStep("wait", "Wi-Fi ${SSID} 显示已连接"),
      newStep("assert", "当前连接的 Wi-Fi 是 ${SSID}，且显示已连接"),
    ];
  } else if (kind === "explore") {
    test.name = "显示设置 · 受限探索";
    test.steps = [
      newStep(
        "action",
        "打开 Android 系统设置的显示页面，并展开高级选项，让自动旋转屏幕开关可见"
      ),
      {
        ...newStep("explore", "Android 系统设置的显示设置页面"),
        timeoutMs: 300000,
        exploration: { allowedControls: ["自动旋转屏幕"], maxActions: 2 },
      },
    ];
  } else {
    test.name = "深色模式检查";
    test.steps = [
      newStep("action", "打开系统设置，找到显示或主题设置中的深色模式开关"),
      newStep("action", "确保深色模式已开启；如果已开启则保持"),
      newStep("assert", "系统设置页面已使用深色背景，主要文字清晰可读"),
    ];
  }
  return test;
}

export default function UITesting() {
  const [tab, setTab] = useState<"execute" | "cases" | "reports" | "structure">(
    "execute"
  );
  const [devices, setDevices] = useState<
    { serial: string; state: string; model?: string }[]
  >([]);
  const [serial, setSerial] = useState("");
  const [draft, setDraft] = useState<Case>(blank);
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
  const selection = useRef(runId);
  selection.current = runId;

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
    const poll = async () => {
      try {
        const [next, log, nextMetrics] = await Promise.all([
          api<Run>(`/ui/runs/${runId}`),
          api<Event[]>(`/ui/runs/${runId}/events`),
          api<StepMetric[]>(`/ui/runs/${runId}/metrics`).catch(() => []),
        ]);
        if (!disposed && selection.current === runId) {
          setRun(next);
          setEvents(log);
          setMetrics(nextMetrics);
        }
      } catch (e) {
        if (!disposed) setError((e as Error).message);
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
  function load(test: Case) {
    setDraft(structuredClone(test));
    setParameters({});
    setTab("execute");
  }
  function patchStep(index: number, update: Partial<Step>) {
    setDraft(previous => ({
      ...previous,
      steps: previous.steps.map((step, i) =>
        i === index ? { ...step, ...update } : step
      ),
    }));
  }
  function move(index: number, delta: number) {
    setDraft(previous => {
      const steps = [...previous.steps];
      [steps[index], steps[index + delta]] = [
        steps[index + delta],
        steps[index],
      ];
      return { ...previous, steps };
    });
  }
  async function start() {
    const next = await api<Run>("/ui/runs", {
      serial,
      testCase: draft,
      parameters,
    });
    setRunId(next.id);
    setRun(next);
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

  return (
    <main className="min-h-screen bg-background text-foreground px-5 py-6 md:px-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-5 mb-5">
        <div>
          <div className="text-xs tracking-widest text-cyan-400 mb-2">
            ANDROID / UI AUTOMATION
          </div>
          <h1 className="text-2xl font-semibold">UI 自动化测试</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            用自然语言描述操作，用独立断言检查结果。脚本可逐步编辑和复跑。
          </p>
        </div>
        <Button variant="outline" onClick={() => void perform(openSettings)}>
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
              onClick={() => {
                setRunId(item.id);
                setTab("execute");
              }}
            >
              {item.testCase.name} · {item.serial}
            </button>
          ))}
        </div>
      )}
      <nav aria-label="UI 测试功能" className="mb-6 flex gap-2">
        {(
          [
            ["execute", "执行与编辑"],
            ["cases", "用例库"],
            ["reports", "测试报告"],
            ["structure", "页面与元素"],
          ] as const
        ).map(([key, label]) => (
          <Button
            key={key}
            variant={tab === key ? "default" : "ghost"}
            onClick={() => setTab(key)}
          >
            {label}
          </Button>
        ))}
      </nav>

      {tab === "structure" && <PageMapWorkbench />}
      {tab === "execute" && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(340px,0.8fr)]">
          <section className="min-w-0 space-y-5">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => load(blank())}>
                <Plus className="size-4" />
                新用例
              </Button>
              {[
                ["observe", "只读检查"],
                ["wifi", "Wi-Fi 示例"],
                ["dark", "深色模式示例"],
                ["explore", "受限探索示例"],
              ].map(([key, label]) => (
                <Button
                  key={key}
                  variant="outline"
                  size="sm"
                  onClick={() => load(template(key))}
                >
                  {label}
                </Button>
              ))}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-2 text-sm">
                用例名称
                <Input
                  value={draft.name}
                  onChange={e => setDraft({ ...draft, name: e.target.value })}
                />
              </label>
              <label className="space-y-2 text-sm">
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
              <label className="space-y-2 text-sm">
                分组
                <Input
                  value={draft.group}
                  onChange={e => setDraft({ ...draft, group: e.target.value })}
                  placeholder="例如：系统设置"
                />
              </label>
              <label className="space-y-2 text-sm">
                标签（逗号分隔）
                <Input
                  value={draft.tags.join(",")}
                  onChange={e =>
                    setDraft({ ...draft, tags: e.target.value.split(",") })
                  }
                />
              </label>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h2 className="font-medium">执行参数</h2>
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
              <p className="text-xs text-muted-foreground">
                步骤内使用 {"${参数名}"}{" "}
                引用。敏感参数只在本次运行中使用；含敏感参数的任务暂不保存截图和
                Midscene 原始报告。
              </p>
              {draft.parameters.map((param, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[1fr_1fr_auto_auto] items-center gap-2"
                >
                  <Input
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
                              j === i ? { ...p, default: e.target.value } : p
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
                              ? { ...p, secret: e.target.checked, default: "" }
                              : p
                          ),
                        });
                      }}
                    />
                    敏感
                  </label>
                  <Button
                    aria-label="删除参数"
                    size="icon"
                    variant="ghost"
                    onClick={() =>
                      setDraft({
                        ...draft,
                        parameters: draft.parameters.filter((_, j) => j !== i),
                      })
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
            <div className="flex items-center justify-between">
              <h2 className="font-medium">
                测试步骤{" "}
                <span className="text-muted-foreground">
                  / {draft.steps.length}
                </span>
              </h2>
              <span className="text-xs text-muted-foreground">
                按顺序执行 · v{draft.version}
              </span>
            </div>
            {draft.steps.map((step, i) => (
              <article
                key={step.id}
                className={`rounded-lg border border-border p-4 space-y-3 ${!step.enabled ? "opacity-50" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      aria-label={`启用步骤 ${i + 1}`}
                      type="checkbox"
                      checked={step.enabled}
                      onChange={e =>
                        patchStep(i, { enabled: e.target.checked })
                      }
                    />
                    {String(i + 1).padStart(2, "0")}
                  </label>
                  <select
                    aria-label={`步骤 ${i + 1} 类型`}
                    className={selectClass}
                    value={step.type}
                    onChange={e =>
                      patchStep(i, {
                        type: e.target.value as StepType,
                        exploration:
                          e.target.value === "explore"
                            ? { allowedControls: [], maxActions: 6 }
                            : null,
                        timeoutMs:
                          e.target.value === "explore"
                            ? 300000
                            : step.timeoutMs,
                      })
                    }
                  >
                    {Object.entries(stepTypes).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label="上移步骤"
                    disabled={i === 0}
                    onClick={() => move(i, -1)}
                  >
                    <ArrowUp className="size-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label="下移步骤"
                    disabled={i === draft.steps.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <ArrowDown className="size-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label="删除步骤"
                    disabled={draft.steps.length === 1}
                    onClick={() =>
                      setDraft({
                        ...draft,
                        steps: draft.steps.filter((_, j) => j !== i),
                      })
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
                <Textarea
                  aria-label={`步骤 ${i + 1} 描述`}
                  value={step.prompt}
                  onChange={e => patchStep(i, { prompt: e.target.value })}
                  placeholder={
                    step.type === "assert"
                      ? "例如：Wi-Fi ${SSID} 显示已连接"
                      : "描述操作目标或检查条件"
                  }
                />
                {step.type === "explore" && (
                  <div className="space-y-2 text-sm">
                    <p className="text-xs text-muted-foreground">
                      描述填写当前页面名称。只操作下方列出的可见开关，切换后恢复原状态；不自动进入子页面。
                    </p>
                    <label className="block">
                      允许探索的开关（逗号分隔）
                      <Input
                        value={
                          step.exploration?.allowedControls.join(",") || ""
                        }
                        onChange={e =>
                          patchStep(i, {
                            exploration: {
                              allowedControls: e.target.value
                                .split(",")
                                .map(v => v.trim()),
                              maxActions: step.exploration?.maxActions || 6,
                            },
                          })
                        }
                      />
                    </label>
                    <label className="block">
                      最大点击次数（含恢复）
                      <Input
                        className="w-24"
                        type="number"
                        min={2}
                        max={20}
                        value={step.exploration?.maxActions || 6}
                        onChange={e =>
                          patchStep(i, {
                            exploration: {
                              allowedControls:
                                step.exploration?.allowedControls || [],
                              maxActions: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </label>
                  </div>
                )}
                {step.type === "input" && (
                  <Input
                    aria-label={`步骤 ${i + 1} 输入内容`}
                    placeholder="输入值，例如 ${PASSWORD}"
                    value={step.value}
                    onChange={e => patchStep(i, { value: e.target.value })}
                  />
                )}
                <div className="flex flex-wrap gap-4 items-center text-xs text-muted-foreground">
                  <label className="flex gap-2 items-center">
                    超时（秒）
                    <Input
                      className="w-24"
                      type="number"
                      min={1}
                      max={300}
                      value={step.timeoutMs / 1000}
                      onChange={e =>
                        patchStep(i, {
                          timeoutMs: Number(e.target.value) * 1000,
                        })
                      }
                    />
                  </label>
                  {step.type === "assert" && (
                    <label className="flex gap-2 items-center">
                      <input
                        type="checkbox"
                        checked={step.onFailure === "continue"}
                        onChange={e =>
                          patchStep(i, {
                            onFailure: e.target.checked ? "continue" : "stop",
                          })
                        }
                      />
                      断言失败后继续
                    </label>
                  )}
                </div>
              </article>
            ))}
            <Button
              variant="outline"
              onClick={() =>
                setDraft({ ...draft, steps: [...draft.steps, newStep()] })
              }
            >
              <Plus className="size-4" />
              添加步骤
            </Button>
            <div className="sticky bottom-0 flex flex-wrap gap-3 border-t border-border bg-background py-4">
              <Button
                disabled={
                  busy || !serial || activeRuns.some(r => r.serial === serial)
                }
                onClick={() => void perform(start)}
              >
                <Play className="size-4" />
                执行用例
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
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
          <section className="min-w-0 xl:border-l xl:border-border xl:pl-6">
            <div className="flex justify-between items-center mb-4">
              <h2 className="font-medium">执行记录</h2>
              {run && active(run) && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      setRun(await api<Run>(`/ui/runs/${run.id}/cancel`, {}));
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
                <p className="mt-2 text-xs">首次接入可先运行“只读检查”。</p>
              </div>
            ) : (
              <>
                <div className="mb-5 space-y-2 text-sm">
                  <h3 className="font-medium">{run.testCase.name}</h3>
                  <p className="text-cyan-400">
                    {states[run.state] || run.state} · {run.serial}
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
                    <a
                      className="block underline"
                      target="_blank"
                      rel="noreferrer"
                      href={`${API}/api/sessions/${run.performanceSessionId}/report`}
                    >
                      查看关联性能报告
                    </a>
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
                        result.warnings.forEach(warning => toast.info(warning));
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
                    const last = log.at(-1);
                    const screenshot = [...log]
                      .reverse()
                      .find(e => e.screenshot)?.screenshot;
                    return (
                      <li
                        key={step.id}
                        className="border-l-2 border-border pl-4"
                      >
                        <div className="flex justify-between gap-3 text-sm">
                          <span>
                            {i + 1}. {stepTypes[step.type]}
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
                              return run.parameters &&
                                Object.hasOwn(run.parameters, name)
                                ? run.parameters[name]
                                : placeholder;
                            }
                          )}
                        </p>
                        {events
                          .filter(
                            e => e.type === "navigation" && e.stepId === step.id
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
                              e.type === "exploration" && e.stepId === step.id
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
                          <p className="mt-2 text-xs text-destructive">
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
                            {new Date(last.time).toLocaleTimeString()}
                          </p>
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
          </section>
        </div>
      )}
      {tab === "cases" && (
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
          <ul className="divide-y divide-border">
            {cases.map(test => (
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
      )}
      {tab === "reports" && (
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
                    {item.serial} · {new Date(item.createdAt).toLocaleString()}{" "}
                    ·{" "}
                    {item.endedAt
                      ? `${Math.round((item.endedAt - item.createdAt) / 1000)} 秒`
                      : "运行中"}
                  </p>
                </div>
                <Button
                  variant="outline"
                  onClick={() => {
                    setRunId(item.id);
                    setTab("execute");
                  }}
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
