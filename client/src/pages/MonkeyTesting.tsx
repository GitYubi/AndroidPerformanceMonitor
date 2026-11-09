import { PerformanceReportDialog } from "@/components/PerformanceReportDialog";
import { useEffect, useRef, useState } from "react";
import { Download, Play, RefreshCw, Square, Shuffle } from "lucide-react";
import { toast } from "sonner";
import { BACKEND_URL } from "@/lib/backend";

type Ratios = Record<string, number>;
type Config = {
  serial: string;
  mode: "none" | "blacklist" | "whitelist";
  packages: string[];
  ratios: Ratios;
  duration_hours: number | null;
  throttle_ms: number;
  seed: number;
  interval_ms: number;
  memory_cycle_skip: number;
  fps: boolean;
  ignore_crashes: boolean;
  ignore_timeouts: boolean;
};
type Segment = {
  number: number;
  session_id: string;
  started_at_ms: number;
  ended_at_ms: number;
  sample_count: number;
  state: string;
  csv_ready?: boolean;
};
type Run = {
  id: string;
  serial: string;
  state: string;
  config?: Config;
  created_at_ms: number;
  started_at_ms: number | null;
  ended_at_ms: number | null;
  current_session_id: string | null;
  segment_count: number;
  message: string;
  cleanup_warning: string | null;
  segments?: Segment[];
  log_tail?: string;
};
type Metric = {
  average: number | null;
  peak: number | null;
  valid_samples: number;
  observed_ms: number;
  distribution?: {
    lower: number;
    upper: number | null;
    percent: number | null;
    observed_ms: number;
  }[];
};
type Summary = {
  sample_count: number;
  segment_count: number;
  metrics: Record<string, Metric>;
  processes: {
    process_name: string;
    cpu_average_pct: number | null;
    cpu_sampled_share_pct: number | null;
    pss_average_kb: number | null;
    pss_ram_average_pct: number | null;
  }[];
};
type Device = { serial: string; state: string; model?: string };

const labels: Record<string, string> = {
  touch: "点击",
  motion: "滑动",
  pinchzoom: "双指缩放",
  appswitch: "应用切换",
  nav: "方向导航",
  majornav: "主要导航",
  syskeys: "系统按键",
  trackball: "轨迹球",
  rotation: "屏幕旋转",
  flip: "键盘翻转",
  anyevent: "其他事件",
};
const initial: Config = {
  serial: "",
  mode: "blacklist",
  packages: [],
  ratios: {
    touch: 45,
    motion: 25,
    pinchzoom: 5,
    appswitch: 10,
    nav: 5,
    majornav: 5,
    syskeys: 5,
    trackball: 0,
    rotation: 0,
    flip: 0,
    anyevent: 0,
  },
  duration_hours: 1,
  throttle_ms: 300,
  seed: 42,
  interval_ms: 2000,
  memory_cycle_skip: 5,
  fps: false,
  ignore_crashes: true,
  ignore_timeouts: true,
};
const states: Record<string, string> = {
  starting: "准备中",
  running: "执行中",
  stopping: "停止并归档中",
  stopped: "已停止",
  completed: "已完成",
  failed: "执行失败",
  interrupted: "已中断",
};
const busy = (run?: Run | null) =>
  !!run && ["starting", "running", "stopping"].includes(run.state);
const fmt = (value: number | null | undefined, suffix = "") =>
  value == null ? "—" : `${value.toFixed(2)}${suffix}`;
const clock = (ms: number) => {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
};
const date = (ms: number | null) => (ms ? new Date(ms).toLocaleString() : "—");

async function api<T>(
  path: string,
  signal?: AbortSignal,
  body?: object
): Promise<T> {
  const response = await fetch(`${BACKEND_URL}/api${path}`, {
    signal,
    ...(body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  const value = await response.json();
  if (!response.ok) {
    const detail = value.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map(item => item.msg).join("；")
          : "请求失败"
    );
  }
  return value as T;
}

function restored(): Config {
  try {
    const saved = JSON.parse(localStorage.getItem("acpm.monkey.v1") || "null");
    if (saved && Array.isArray(saved.packages))
      return {
        ...initial,
        ...saved,
        ratios: { ...initial.ratios, ...saved.ratios },
      };
  } catch {
    /* A damaged draft does not prevent opening the tool. */
  }
  return initial;
}

const control =
  "w-full rounded border border-slate-700 bg-slate-950 px-2.5 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500 disabled:opacity-50";
const button =
  "inline-flex items-center justify-center gap-2 rounded border border-slate-700 px-3 py-2 text-sm hover:border-cyan-500 disabled:cursor-not-allowed disabled:opacity-40";
const panel = "rounded-lg border border-slate-800 bg-slate-900/50 p-5";

export default function MonkeyTesting() {
  const [config, setConfig] = useState<Config>(restored);
  const [packageText, setPackageText] = useState(() =>
    restored().packages.join("\n")
  );
  const [defaultBlacklist, setDefaultBlacklist] = useState<string[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Run | null>(null);
  const [result, setResult] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [segmentOffset, setSegmentOffset] = useState(0);
  const [customHours, setCustomHours] = useState(() => {
    const hours = restored().duration_hours;
    return hours != null && hours > 24;
  });
  const summaryLoaded = useRef("");
  const hydrated = useRef("");
  const hadDraft = useRef(!!localStorage.getItem("acpm.monkey.v1"));
  const active = runs.find(busy);
  const locked = pending || !!active || busy(selected);
  const total = Object.values(config.ratios).reduce(
    (sum, value) => sum + value,
    0
  );
  const packages = packageText
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line && !line.startsWith("#"));
  const valid =
    config.serial &&
    total === 100 &&
    (config.mode === "none" || packages.length > 0) &&
    (config.duration_hours === null || config.duration_hours >= 1);
  const file = (name: string) =>
    `${BACKEND_URL}/api/monkey/runs/${selectedId}/files/${name}`;

  const update = <K extends keyof Config>(key: K, value: Config[K]) =>
    setConfig(current => ({ ...current, [key]: value }));
  const selectRun = (id: string) => {
    setSelectedId(id);
    setSelected(null);
    setSegmentOffset(0);
    setResult(null);
    summaryLoaded.current = "";
  };

  const scan = async () => {
    setScanning(true);
    try {
      const found = await api<Device[]>("/devices");
      setDevices(found);
      setConfig(current =>
        current.serial
          ? current
          : {
              ...current,
              serial: found.find(d => d.state === "device")?.serial || "",
            }
      );
    } catch (exc) {
      toast.error((exc as Error).message);
    } finally {
      setScanning(false);
    }
  };

  useEffect(() => {
    const abort = new AbortController();
    void api<{ blacklist: string[] }>("/monkey/defaults", abort.signal)
      .then(defaults => {
        setDefaultBlacklist(defaults.blacklist);
        if (!hadDraft.current) setPackageText(defaults.blacklist.join("\n"));
      })
      .catch(() => {});
    void scan();
    return () => abort.abort();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        localStorage.setItem(
          "acpm.monkey.v1",
          JSON.stringify({ ...config, packages })
        );
      } catch {
        /* Configuration still works without browser storage. */
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [config, packageText]);

  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let disposed = false;
    const poll = async () => {
      try {
        if (document.hidden) return;
        const latest = await api<Run[]>(
          `/monkey/runs?offset=${historyOffset}`,
          abort.signal
        );
        if (disposed) return;
        setRuns(latest);
        if (!selectedId && latest.length) {
          setSelectedId((latest.find(busy) || latest[0]).id);
          return;
        }
        if (selectedId) {
          const detail = await api<Run>(
            `/monkey/runs/${selectedId}?offset=${segmentOffset}`,
            abort.signal
          );
          if (disposed) return;
          setSelected(detail);
          if (busy(detail) && detail.config && hydrated.current !== detail.id) {
            hydrated.current = detail.id;
            setConfig(detail.config);
            setPackageText(detail.config.packages.join("\n"));
            setCustomHours(
              detail.config.duration_hours != null &&
                detail.config.duration_hours > 24
            );
          }
          if (!busy(detail) && summaryLoaded.current !== detail.id) {
            try {
              const data = await api<Summary>(
                `/monkey/runs/${detail.id}/summary`,
                abort.signal
              );
              if (!disposed) {
                setResult(data);
                summaryLoaded.current = detail.id;
              }
            } catch {
              /* A failed startup may not have a summary. Keep the task error visible. */
            }
          }
        }
        setError("");
      } catch (exc) {
        if (!disposed) setError((exc as Error).message);
      } finally {
        if (!disposed) timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => {
      disposed = true;
      abort.abort();
      clearTimeout(timer);
    };
  }, [selectedId, segmentOffset, historyOffset]);

  const start = async () => {
    setPending(true);
    try {
      const run = await api<Run>("/monkey/runs", undefined, {
        ...config,
        packages: config.mode === "none" ? [] : packages,
      });
      setHistoryOffset(0);
      selectRun(run.id);
      setRuns(current => [run, ...current].slice(0, 50));
      toast.success("Monkey 任务已创建，性能采样会自动开始");
    } catch (exc) {
      toast.error((exc as Error).message);
    } finally {
      setPending(false);
    }
  };
  const stop = async () => {
    if (!selected) return;
    setPending(true);
    try {
      const run = await api<Run>(
        `/monkey/runs/${selected.id}/stop`,
        undefined,
        {}
      );
      setSelected(current => (current ? { ...current, ...run } : run));
      toast.success("正在停止输入、归档末段并合并记录");
    } catch (exc) {
      toast.error((exc as Error).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 p-4 text-slate-200 md:p-6">
      <div className="mx-auto max-w-[1600px] space-y-5">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="mb-2 text-xs font-medium tracking-[0.2em] text-cyan-400">
              ANDROID STRESS LAB
            </div>
            <h1 className="flex items-center gap-3 text-2xl font-semibold">
              <Shuffle className="text-cyan-400" />
              Monkey 压测
            </h1>
            <p className="mt-2 text-sm text-slate-400">
              随机事件执行 · 每小时性能归档 · 全程记录合并
            </p>
          </div>
          <div className="text-right text-xs leading-6 text-slate-400">
            CPU / 内存自动联动
            <br />
            按小时导出 CSV，结束合并 SQLite，不自动生成报告
          </div>
        </header>
        {error && (
          <div
            role="alert"
            className="rounded border border-rose-900 bg-rose-950/40 p-3 text-sm text-rose-300"
          >
            后端连接失败：{error}
          </div>
        )}
        <div className="grid items-start gap-5 xl:grid-cols-[350px_minmax(0,1fr)]">
          <section className={panel} aria-label="Monkey 执行配置">
            <h2 className="mb-4 font-semibold">执行配置</h2>
            <fieldset disabled={locked} className="space-y-5">
              <div>
                <label
                  htmlFor="monkey-device"
                  className="mb-2 block text-xs text-slate-400"
                >
                  目标设备
                </label>
                <div className="flex gap-2">
                  <select
                    id="monkey-device"
                    className={control}
                    value={config.serial}
                    onChange={event => update("serial", event.target.value)}
                  >
                    <option value="">请选择已授权设备</option>
                    {devices.map(device => (
                      <option
                        key={device.serial}
                        value={device.serial}
                        disabled={device.state !== "device"}
                      >
                        {device.model || device.serial} · {device.serial} (
                        {device.state})
                      </option>
                    ))}
                    {config.serial &&
                      !devices.some(
                        device => device.serial === config.serial
                      ) && (
                        <option value={config.serial}>
                          {config.serial} · 未扫描到
                        </option>
                      )}
                  </select>
                  <button
                    type="button"
                    className={button}
                    aria-label="刷新设备"
                    disabled={scanning}
                    onClick={() => void scan()}
                  >
                    <RefreshCw
                      size={16}
                      className={scanning ? "animate-spin" : ""}
                    />
                  </button>
                </div>
              </div>
              <div>
                <label
                  htmlFor="monkey-mode"
                  className="mb-2 block text-xs text-slate-400"
                >
                  包名单模式
                </label>
                <select
                  id="monkey-mode"
                  className={control}
                  value={config.mode}
                  onChange={event =>
                    update("mode", event.target.value as Config["mode"])
                  }
                >
                  <option value="blacklist">黑名单 · 排除指定应用</option>
                  <option value="whitelist">白名单 · 仅进入指定应用</option>
                  <option value="none">无名单 · 不限制应用</option>
                </select>
                {config.mode !== "none" && (
                  <div className="mt-3">
                    <label
                      htmlFor="monkey-packages"
                      className="mb-2 block text-xs text-slate-400"
                    >
                      每行一个完整包名，可粘贴 TXT 内容
                    </label>
                    <textarea
                      id="monkey-packages"
                      className={`${control} h-36 font-mono text-xs`}
                      maxLength={256000}
                      spellCheck={false}
                      value={packageText}
                      onChange={event => setPackageText(event.target.value)}
                      placeholder="com.example.app"
                    />
                    <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
                      <span>{new Set(packages).size} 个包</span>
                      {config.mode === "blacklist" && (
                        <button
                          type="button"
                          className="text-cyan-400 disabled:opacity-40"
                          disabled={!defaultBlacklist.length}
                          onClick={() =>
                            setPackageText(defaultBlacklist.join("\n"))
                          }
                        >
                          载入系统黑名单
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <div>
                <label
                  htmlFor="monkey-duration"
                  className="mb-2 block text-xs text-slate-400"
                >
                  执行时长
                </label>
                <select
                  id="monkey-duration"
                  className={control}
                  value={
                    customHours
                      ? "custom"
                      : config.duration_hours === null
                        ? "continuous"
                        : String(config.duration_hours)
                  }
                  onChange={event => {
                    const value = event.target.value;
                    setCustomHours(value === "custom");
                    if (value !== "custom")
                      update(
                        "duration_hours",
                        value === "continuous" ? null : Number(value)
                      );
                    else if (!config.duration_hours)
                      update("duration_hours", 1);
                  }}
                >
                  <option value="continuous">持续执行 · 手动停止</option>
                  {Array.from({ length: 24 }, (_, index) => index + 1).map(
                    hour => (
                      <option value={String(hour)} key={hour}>
                        {hour} 小时
                      </option>
                    )
                  )}
                  <option value="custom">自定义整小时</option>
                </select>
                {customHours && (
                  <input
                    aria-label="自定义执行小时数"
                    type="number"
                    min={1}
                    max={8760}
                    step={1}
                    className={`${control} mt-2`}
                    value={config.duration_hours || 1}
                    onChange={event =>
                      update("duration_hours", Number(event.target.value))
                    }
                  />
                )}
              </div>
              <div>
                <h3 className="mb-3 text-sm font-medium">
                  事件占比{" "}
                  <span
                    className={
                      total === 100 ? "text-emerald-400" : "text-rose-400"
                    }
                  >
                    {total}% / 100%
                  </span>
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  {Object.entries(labels).map(([key, label]) => (
                    <label key={key} className="text-xs text-slate-400">
                      {label} %
                      <input
                        aria-label={`${label}占比`}
                        type="number"
                        min={0}
                        max={100}
                        step={1}
                        className={`${control} mt-1`}
                        value={config.ratios[key]}
                        onChange={event =>
                          update("ratios", {
                            ...config.ratios,
                            [key]: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                  ))}
                </div>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  占比为事件生成概率，不保证实际耗时比例；滑动等动作可能产生多条底层事件。所有占比必须合计
                  100%。
                </p>
                <button
                  type="button"
                  className="mt-2 text-xs text-cyan-400"
                  onClick={() => update("ratios", initial.ratios)}
                >
                  恢复默认比例
                </button>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="text-xs text-slate-400">
                  事件间隔 ms
                  <input
                    aria-label="事件间隔"
                    className={`${control} mt-1`}
                    type="number"
                    min={50}
                    max={10000}
                    step={50}
                    value={config.throttle_ms}
                    onChange={event =>
                      update("throttle_ms", Number(event.target.value))
                    }
                  />
                </label>
                <label className="text-xs text-slate-400">
                  随机种子
                  <input
                    aria-label="随机种子"
                    className={`${control} mt-1`}
                    type="number"
                    min={0}
                    max={2147483647}
                    step={1}
                    value={config.seed}
                    onChange={event =>
                      update("seed", Number(event.target.value))
                    }
                  />
                </label>
              </div>
              <div className="border-t border-slate-800 pt-4">
                <h3 className="mb-3 text-sm font-medium">性能采样与负载</h3>
                <label className="block text-xs text-slate-400">
                  CPU 采样周期
                  <select
                    aria-label="性能采样周期"
                    className={`${control} mt-1`}
                    value={config.interval_ms}
                    onChange={event =>
                      update("interval_ms", Number(event.target.value))
                    }
                  >
                    <option value={1000}>1 秒</option>
                    <option value={2000}>2 秒（默认）</option>
                    <option value={5000}>5 秒</option>
                  </select>
                </label>
                <label className="mt-3 block text-xs text-slate-400">
                  每 N 个周期读取一次内存
                  <input
                    aria-label="内存采样降频"
                    className={`${control} mt-1`}
                    type="number"
                    min={1}
                    max={60}
                    value={config.memory_cycle_skip}
                    onChange={event =>
                      update("memory_cycle_skip", Number(event.target.value))
                    }
                  />
                </label>
                <p className="mt-2 text-xs text-slate-500">
                  当前约每{" "}
                  {(config.interval_ms * config.memory_cycle_skip) / 1000}{" "}
                  秒读取内存，CPU / 内存自动开启。
                </p>
                <label className="mt-3 flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={config.fps}
                    onChange={event => update("fps", event.target.checked)}
                  />
                  同时采集 FPS（增加设备诊断命令）
                </label>
                <label className="mt-3 flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={config.ignore_crashes}
                    onChange={event =>
                      update("ignore_crashes", event.target.checked)
                    }
                  />
                  应用 Crash 后继续 Monkey
                </label>
                <label className="mt-3 flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={config.ignore_timeouts}
                    onChange={event =>
                      update("ignore_timeouts", event.target.checked)
                    }
                  />
                  应用 ANR 后继续 Monkey
                </label>
              </div>
            </fieldset>
            <button
              type="button"
              className={`${button} mt-5 w-full border-cyan-700 bg-cyan-950 text-cyan-200`}
              disabled={locked || !valid}
              onClick={() => void start()}
            >
              <Play size={15} />
              {pending ? "正在处理…" : "启动 Monkey 与性能采样"}
            </button>
            <p className="mt-3 text-xs leading-5 text-slate-500">
              执行期间独占设备操作和性能采样；日志限制为两份各 8
              MiB。连接中断后设备端约 90 秒内停止输入，磁盘低于 128 MiB
              自动停止并保留已落盘数据。
            </p>
          </section>
          <div className="min-w-0 space-y-5">
            <section className={panel} aria-label="Monkey 当前任务">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-semibold">
                  {selected
                    ? states[selected.state] || selected.state
                    : "尚无执行任务"}
                </h2>
                {selected && busy(selected) && (
                  <button
                    className={`${button} border-rose-800 text-rose-300`}
                    disabled={pending || selected.state === "stopping"}
                    onClick={() => void stop()}
                  >
                    <Square size={14} />
                    停止并合并记录
                  </button>
                )}
              </div>
              {selected ? (
                <>
                  <p className="mt-3 break-all font-mono text-xs text-slate-500">
                    {selected.serial} · {selected.id}
                  </p>
                  <p
                    className={`mt-3 text-sm ${selected.state === "failed" ? "text-rose-300" : "text-slate-300"}`}
                  >
                    {selected.message}
                  </p>
                  {selected.cleanup_warning && (
                    <p role="alert" className="mt-3 text-sm text-amber-300">
                      {selected.cleanup_warning}
                    </p>
                  )}
                  <div className="mt-5 grid grid-cols-2 gap-4 md:grid-cols-4">
                    {[
                      [
                        "已执行",
                        clock(
                          (selected.ended_at_ms || Date.now()) -
                            (selected.started_at_ms || selected.created_at_ms)
                        ),
                      ],
                      [
                        "计划时长",
                        selected.config?.duration_hours
                          ? `${selected.config.duration_hours} 小时`
                          : "持续执行",
                      ],
                      ["已归档分段", String(selected.segment_count)],
                      [
                        "当前采样",
                        selected.current_session_id ? "运行中" : "—",
                      ],
                    ].map(([label, value]) => (
                      <div key={label}>
                        <div className="text-xs text-slate-500">{label}</div>
                        <div className="mt-2 font-mono text-xl text-cyan-200">
                          {value}
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="mt-4 text-xs text-slate-500">
                    开始：{date(selected.started_at_ms)} · 结束：
                    {date(selected.ended_at_ms)}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-3">
                    {!busy(selected) && selected.segment_count > 0 && (
                      <PerformanceReportDialog
                        url={`${BACKEND_URL}/api/monkey/runs/${selected.id}/report`}
                        enabledMetrics={{ fps: selected.config?.fps }}
                      >
                        <button type="button" className={button}>
                          <Download size={14} />
                          导出全程性能图
                        </button>
                      </PerformanceReportDialog>
                    )}
                    {!busy(selected) && selected.segment_count > 0 && (
                      <a href={file("combined.csv")} className={button}>
                        <Download size={14} />
                        下载全程性能 CSV
                      </a>
                    )}
                    {!busy(selected) && selected.segment_count > 0 && (
                      <a href={file("combined.db")} className={button}>
                        <Download size={14} />
                        下载合并 SQLite
                      </a>
                    )}
                    {result && (
                      <a href={file("summary.json")} className={button}>
                        <Download size={14} />
                        下载统计 JSON
                      </a>
                    )}
                    {selected.current_session_id && (
                      <a href="/" className={`${button} text-cyan-300`}>
                        查看实时性能曲线
                      </a>
                    )}
                  </div>
                  {!busy(selected) && selected.segment_count > 0 && <p className="mt-2 text-xs text-slate-500">勾选指标并生成预览后，可点击“保存为图片（PNG）”。</p>}
                </>
              ) : (
                <div className="py-12 text-center text-sm leading-7 text-slate-500">
                  选择设备并配置事件比例后启动。
                  <br />满 1 小时自动导出一次 CSV，结束时保存末段并生成全程性能 CSV。
                </div>
              )}
            </section>
            {result && (
              <section className={panel} aria-label="Monkey 全程性能汇总">
                <h2 className="font-semibold">
                  全程资源统计{" "}
                  <span className="ml-2 text-xs font-normal text-slate-500">
                    {result.sample_count.toLocaleString()} 个采样点 ·{" "}
                    {result.segment_count} 段
                  </span>
                </h2>
                <div className="mt-5 grid grid-cols-2 gap-4 md:grid-cols-4">
                  {[
                    ["平均 CPU", fmt(result.metrics.cpu?.average, "%")],
                    ["峰值 CPU", fmt(result.metrics.cpu?.peak, "%")],
                    [
                      "平均系统内存占用率",
                      fmt(result.metrics.memory_used_pct?.average, "%"),
                    ],
                    [
                      "峰值系统内存占用率",
                      fmt(result.metrics.memory_used_pct?.peak, "%"),
                    ],
                    [
                      "平均总 PSS",
                      fmt(
                        result.metrics.memory_pss?.average == null
                          ? null
                          : result.metrics.memory_pss.average / 1024,
                        " MiB"
                      ),
                    ],
                    [
                      "平均 PSS / RAM",
                      fmt(result.metrics.memory_pss_pct?.average, "%"),
                    ],
                  ].map(([label, value]) => (
                    <div key={label}>
                      <div className="text-xs text-slate-500">{label}</div>
                      <div className="mt-2 font-mono text-lg text-cyan-200">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="mt-6 grid gap-6 md:grid-cols-2">
                  {[
                    ["cpu", "CPU 占用区间时长占比"],
                    ["memory_used_pct", "系统内存占用区间时长占比"],
                  ].map(([key, label]) => (
                    <div key={key}>
                      <h3 className="text-sm">{label}</h3>
                      <p className="mb-3 mt-1 text-xs text-slate-500">
                        有效采样时间{" "}
                        {clock(result.metrics[key]?.observed_ms || 0)}
                      </p>
                      {result.metrics[key]?.distribution?.map(bin => (
                        <div
                          key={bin.lower}
                          className="mt-2 grid grid-cols-[65px_1fr_60px] items-center gap-2 text-xs"
                        >
                          <span className="text-slate-400">
                            {bin.upper == null
                              ? `${bin.lower}%+`
                              : `${bin.lower}–${bin.upper}%`}
                          </span>
                          <div className="h-2 rounded bg-slate-800">
                            <div
                              className="h-2 rounded bg-cyan-500"
                              style={{
                                width: `${Math.min(100, bin.percent || 0)}%`,
                              }}
                            />
                          </div>
                          <span className="text-right font-mono">
                            {fmt(bin.percent, "%")}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
                <p className="mt-5 text-xs leading-5 text-slate-500">
                  平均值与区间占比按实际采样间隔加权；单点最多计入 3
                  个采样间隔，缺失数据不按 0 计算。系统内存占用率 = (MemTotal −
                  MemAvailable) / MemTotal × 100%。缺失时显示“—”，不会用 PSS 或
                  MemFree 推算；PSS / RAM 保留用于进程分析。
                </p>
                <div className="mt-5 max-h-72 overflow-auto">
                  <table className="w-full whitespace-nowrap text-left text-xs">
                    <thead className="text-slate-500">
                      <tr>
                        {[
                          "进程（前 50）",
                          "平均 CPU",
                          "采样 CPU 份额",
                          "平均 PSS",
                          "PSS / RAM",
                        ].map(label => (
                          <th
                            className="border-b border-slate-800 px-2 py-3 font-normal"
                            key={label}
                          >
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.processes.map(process => (
                        <tr
                          key={process.process_name}
                          className="border-b border-slate-800/50"
                        >
                          <td
                            className="max-w-64 truncate px-2 py-3 font-mono"
                            title={process.process_name}
                          >
                            {process.process_name}
                          </td>
                          <td className="px-2">
                            {fmt(process.cpu_average_pct, "%")}
                          </td>
                          <td className="px-2">
                            {fmt(process.cpu_sampled_share_pct, "%")}
                          </td>
                          <td className="px-2">
                            {fmt(
                              process.pss_average_kb == null
                                ? null
                                : process.pss_average_kb / 1024,
                              " MiB"
                            )}
                          </td>
                          <td className="px-2">
                            {fmt(process.pss_ram_average_pct, "%")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-xs text-slate-500">
                  进程 CPU 份额 = 该进程采样 CPU 总量 / 所有已采到进程 CPU
                  总量，不是整机容量占比。完整明细保留在合并 SQLite 中。
                </p>
              </section>
            )}
            <section className={panel} aria-label="每小时性能记录">
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-semibold">每小时性能记录</h2>
                <span className="text-xs text-slate-500">
                  末段不足 1 小时也会归档
                </span>
              </div>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full whitespace-nowrap text-left text-xs">
                  <thead className="text-slate-500">
                    <tr>
                      {["分段", "开始 / 结束", "采样点", "状态", "记录"].map(
                        label => (
                          <th
                            key={label}
                            className="border-b border-slate-800 px-2 py-3 font-normal"
                          >
                            {label}
                          </th>
                        )
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {selected?.segments?.map(segment => (
                      <tr
                        key={segment.number}
                        className="border-b border-slate-800/50"
                      >
                        <td className="px-2 py-3">#{segment.number}</td>
                        <td className="px-2 leading-5">
                          {date(segment.started_at_ms)}
                          <br />
                          {date(segment.ended_at_ms)}
                        </td>
                        <td className="px-2 font-mono">
                          {segment.sample_count.toLocaleString()}
                        </td>
                        <td className="px-2">
                          {states[segment.state] || segment.state}
                        </td>
                        <td className="px-2">
                          {segment.csv_ready ? (
                            <a
                              className="text-cyan-400"
                              href={file(
                                `hour-${String(segment.number).padStart(4, "0")}.csv`
                              )}
                            >
                              下载 CSV
                            </a>
                          ) : (
                            "导出中"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!selected?.segments?.length && (
                  <p className="py-8 text-center text-sm text-slate-500">
                    完成首个小时或停止任务后，这里会出现记录。
                  </p>
                )}
              </div>
              {selected && (
                <div className="mt-3 flex items-center justify-end gap-3 text-xs">
                  <button
                    className={button}
                    disabled={segmentOffset === 0}
                    onClick={() =>
                      setSegmentOffset(Math.max(0, segmentOffset - 24))
                    }
                  >
                    更新分段
                  </button>
                  <button
                    className={button}
                    disabled={(selected.segments?.length || 0) < 24}
                    onClick={() => setSegmentOffset(segmentOffset + 24)}
                  >
                    更早分段
                  </button>
                </div>
              )}
            </section>
            <section className={panel}>
              <h2 className="font-semibold">Monkey 日志尾部</h2>
              <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-400">
                {selected?.log_tail ||
                  "尚无设备端输出。日志以低详细度采集，避免逐事件输出造成额外负载。"}
              </pre>
            </section>
            <section className={panel} aria-label="Monkey 历史任务">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold">历史任务</h2>
                <div className="flex gap-2">
                  <button
                    className={button}
                    disabled={historyOffset === 0}
                    onClick={() =>
                      setHistoryOffset(Math.max(0, historyOffset - 50))
                    }
                  >
                    较新
                  </button>
                  <button
                    className={button}
                    disabled={runs.length < 50}
                    onClick={() => setHistoryOffset(historyOffset + 50)}
                  >
                    更早
                  </button>
                </div>
              </div>
              <div className="mt-3 max-h-72 space-y-2 overflow-auto">
                {runs.map(run => (
                  <button
                    key={run.id}
                    onClick={() => selectRun(run.id)}
                    className={`flex w-full items-center justify-between gap-4 rounded border p-3 text-left text-xs ${selectedId === run.id ? "border-cyan-800 bg-cyan-950/30" : "border-slate-800 hover:border-slate-600"}`}
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-mono">
                        {run.serial}
                      </span>
                      <span className="mt-1 block text-slate-500">
                        {date(run.created_at_ms)} · {run.segment_count} 段
                      </span>
                    </span>
                    <span
                      className={busy(run) ? "text-cyan-300" : "text-slate-400"}
                    >
                      {states[run.state] || run.state}
                    </span>
                  </button>
                ))}
                {!runs.length && (
                  <p className="py-4 text-sm text-slate-500">暂无记录。</p>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </main>
  );
}
