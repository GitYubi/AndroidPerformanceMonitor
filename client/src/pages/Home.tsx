/**
 * 设计提示：驾驶舱遥测仪。左侧为会话骨架，右侧为随启用模块数弹性重排的时间轨迹带；不使用虚构性能数据填充空态。
 */
import { Button } from "@/components/ui/button";
import { InteractionDiagnostic } from "@/components/InteractionDiagnostic";
import { Switch } from "@/components/ui/switch";
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronRight,
  CircleStop,
  Cpu,
  Download,
  Gauge,
  HardDrive,
  Layers3,
  Loader2,
  Play,
  RefreshCw,
  SlidersHorizontal,
  TimerReset,
  Usb,
  Wifi,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";

type MetricKey = "cpu" | "memory" | "fps";
type ProcessMetric = "cpu" | "pss" | "rss";

interface Device {
  serial: string;
  state: string;
  model?: string | null;
  product?: string | null;
  device?: string | null;
  android_version?: string | null;
}

interface Health {
  status: "ok" | "degraded";
  adb: string;
  device_count: number;
  active_sessions: number;
  detail?: string;
}

interface SessionPoint {
  ts_ms: number;
  cpu_total_pct: number | null;
  pss_kb: number | null;
  rss_kb: number | null;
  total_ram_kb: number | null;
  fps: number | null;
  app_render_fps: number | null;
  app_jank_pct: number | null;
  frame_source: string | null;
  frame_count: number | null;
  jank_count: number | null;
  jank_pct: number | null;
  avg_frame_time_ms: number | null;
  p95_frame_time_ms: number | null;
  p99_frame_time_ms: number | null;
  input_latency_ms: number | null;
}

interface FrameCapabilities {
  sdk_version: number | null;
  android_release: string | null;
  foreground_package: string | null;
  sources: { frametimeline: boolean; framestats: boolean; sf_latency: boolean };
  recommended_source: string;
  notes: string[];
}

const FRAME_SOURCE_LABELS: Record<string, string> = {
  frametimeline: "FrameTimeline",
  framestats: "framestats",
  sf_latency: "SF --latency",
  gfxinfo: "gfxinfo 计数器",
};

// 应用渲染 R 线低于该值时视为“无有效渲染（空闲）”：曲线以虚线呈现并保持
// 最后一次有效值，避免空闲期的低频读数与操作期连线形成剧烈起伏。
const RENDER_IDLE_FPS = 5;

function frameSourceLabel(source: string | null | undefined): string {
  return source ? FRAME_SOURCE_LABELS[source] || source : "—";
}

interface SummaryMetric {
  average: number | null;
  peak: number | null;
  valid_count: number;
  unit: string;
}

interface MonitorSession {
  session_id: string;
  state: "running" | "completed" | "stopped" | "interrupted" | "failed";
  serial: string;
  created_at_ms: number;
  started_at_ms: number;
  ended_at_ms?: number | null;
  duration_seconds: number;
  interval_ms: number;
  enabled_metrics: Record<MetricKey, boolean>;
  surface_layer?: string | null;
  summary: { sample_count?: number; metrics?: Record<string, SummaryMetric> };
}

interface ProcessRow {
  process_name: string;
  pid: number | null;
  average: number | null;
  peak: number | null;
  samples: number;
}

interface MonitorEvent {
  ts_ms: number;
  severity: string;
  code: string;
  message: string;
}

const API_BASE = (import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8090").replace(/\/$/, "");
const METRIC_META: Record<MetricKey, { label: string; unit: string; color: string; icon: typeof Cpu; apiKey: string }> = {
  cpu: { label: "CPU 整体占用（逻辑核归一）", unit: "%", color: "#39D6D3", icon: Cpu, apiKey: "cpu_total_pct" },
  memory: { label: "Memory 占用", unit: "MiB", color: "#88D66C", icon: HardDrive, apiKey: "pss_kb" },
  fps: { label: "帧链路：渲染 / 呈现", unit: "fps", color: "#F4B942", icon: Gauge, apiKey: "fps" },
};

async function requestApi<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(timestamp);
}

function formatValue(value: number | null | undefined, unit = "", fractionDigits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(fractionDigits)}${unit}`;
}

function displayMetricValue(metric: MetricKey, value: number | null | undefined): string {
  if (metric === "memory" && value !== null && value !== undefined) return `${(value / 1024).toFixed(1)} MiB`;
  return formatValue(value, METRIC_META[metric].unit);
}

function memoryPercent(value: number | null | undefined, totalRamKb: number | null | undefined): string {
  if (value === null || value === undefined || !totalRamKb) return "—";
  return (value / totalRamKb * 100).toFixed(1) + "%";
}

function formatMemorySummary(item: SummaryMetric, totalRamKb: number | null): string {
  return [formatValue(item.average === null ? null : item.average / 1024, " MiB"), memoryPercent(item.average, totalRamKb), "/", formatValue(item.peak === null ? null : item.peak / 1024, " MiB"), memoryPercent(item.peak, totalRamKb)].join(" · ");
}

function metricSummary(session: MonitorSession | null, key: string): SummaryMetric | undefined {
  return session?.summary?.metrics?.[key];
}

function StatusPill({ health }: { health: Health | null }) {
  const connected = health?.status === "ok";
  return (
    <div className={`flex items-center gap-2 rounded-sm border px-2.5 py-1 text-[11px] font-medium ${connected ? "border-cyan-300/25 bg-cyan-300/8 text-cyan-200" : "border-amber-300/25 bg-amber-300/8 text-amber-200"}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-cyan-300 telemetry-live" : "bg-amber-300"}`} />
      {connected ? "LOCAL ADB READY" : "LOCAL ADB CHECK"}
    </div>
  );
}

function MetricToggle({ metric, checked, onCheckedChange }: { metric: MetricKey; checked: boolean; onCheckedChange: (checked: boolean) => void }) {
  const meta = METRIC_META[metric];
  const Icon = meta.icon;
  return (
    <label className={`group flex cursor-pointer items-center justify-between rounded-md border px-3 py-2.5 transition-all duration-200 ${checked ? "border-cyan-300/30 bg-cyan-300/7" : "border-slate-600/60 bg-slate-950/15 hover:border-slate-500"}`}>
      <span className="flex items-center gap-2.5">
        <span className={`grid h-7 w-7 place-items-center rounded-sm ${checked ? "bg-cyan-300/14 text-cyan-200" : "bg-slate-800 text-slate-500"}`}><Icon size={15} /></span>
        <span>
          <span className="block text-xs font-medium text-slate-200">{meta.label}</span>
          <span className="font-telemetry text-[10px] text-slate-500">{metric === "memory" ? "PSS + RSS" : meta.unit}</span>
        </span>
      </span>
      <Switch checked={checked} onCheckedChange={onCheckedChange} aria-label={`启用${meta.label}`} />
    </label>
  );
}

function MetricChart({ metric, points, height }: { metric: MetricKey; points: SessionPoint[]; height: number }) {
  const meta = METRIC_META[metric];
  const dataKey = meta.apiKey as keyof SessionPoint;
  const isFramePipeline = metric === "fps";
  const hasData = isFramePipeline
    ? points.some((point) => point.fps !== null || point.app_render_fps !== null || point.jank_pct !== null)
    : points.some((point) => point[dataKey] !== null);
  // 应用渲染 R 线拆分为两条：活跃期实线（真实值）与空闲期虚线（保持最后一次活跃值）。
  let lastRenderFps: number | null = null;
  const renderFpsActive: (number | null)[] = [];
  const renderFpsHeld: (number | null)[] = [];
  for (const point of points) {
    const value = point.app_render_fps;
    if (value !== null && value >= RENDER_IDLE_FPS) {
      lastRenderFps = value;
      renderFpsActive.push(value);
      renderFpsHeld.push(null);
    } else {
      renderFpsActive.push(null);
      renderFpsHeld.push(lastRenderFps);
    }
  }
  const chartData = points.map((point, index) => ({
    ...point,
    viewValue: metric === "memory" && point.pss_kb !== null ? Number((point.pss_kb / 1024).toFixed(2)) : point[dataKey],
    renderFpsActive: isFramePipeline ? renderFpsActive[index] : undefined,
    renderFpsHeld: isFramePipeline ? renderFpsHeld[index] : undefined,
  }));
  const latest = points.at(-1);
  const latestText = isFramePipeline
    ? `R ${formatValue(latest?.app_render_fps, " fps")} · P ${formatValue(latest?.fps, " fps")} · J ${formatValue(latest?.jank_pct, "%")}`
    : displayMetricValue(metric, latest?.[dataKey] as number | null);
  return (
    <section className="telemetry-panel relative min-h-0 overflow-hidden rounded-md border border-slate-700/70 bg-slate-900/80" style={{ height }}>
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/50 to-transparent" />
      <div className="flex items-start justify-between px-4 pb-1 pt-3">
        <div className="flex items-center gap-2.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: meta.color, boxShadow: `0 0 12px ${meta.color}` }} />
          <div>
            <h2 className="text-xs font-semibold tracking-[0.08em] text-slate-100">{meta.label}</h2>
            <p className="font-telemetry text-[10px] uppercase tracking-wider text-slate-500">{isFramePipeline ? `LIVE TRACE · SOURCE ${frameSourceLabel(latest?.frame_source)} · RENDER / PRESENT / JANK` : `LIVE TRACE · ${meta.unit}`}</p>
          </div>
        </div>
        <span className="font-telemetry text-[11px] text-slate-300">{hasData ? latestText : "等待首个采样点"}</span>
      </div>
      <div className="h-[calc(100%-58px)] px-1 pb-2">
        {hasData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 4, right: 20, left: -15, bottom: 0 }}>
              <CartesianGrid stroke="#314158" strokeDasharray="2 5" vertical={false} />
              <XAxis dataKey="ts_ms" tickFormatter={formatTime} minTickGap={46} tick={{ fill: "#718096", fontSize: 10, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} />
              <YAxis yAxisId="fps" width={44} tick={{ fill: "#718096", fontSize: 10, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} />
              {isFramePipeline && <YAxis yAxisId="jank" orientation="right" width={36} domain={[0, 100]} tick={{ fill: "#fb7185", fontSize: 9, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} tickFormatter={(value: number) => `${value}%`} />}
              <Tooltip cursor={{ stroke: "#64748b", strokeDasharray: "3 3" }} contentStyle={{ background: "#111b2b", border: "1px solid #3b4b62", borderRadius: 4, fontFamily: "IBM Plex Mono", fontSize: 11 }} labelFormatter={(label) => formatTime(Number(label))} formatter={(value: number, name: string) => [formatValue(value, name === "逐帧 Jank" ? "%" : " fps"), name]} />
              {isFramePipeline && <Line yAxisId="fps" type="monotone" dataKey="renderFpsActive" name="应用渲染" stroke="#7dd3fc" strokeWidth={2} dot={false} activeDot={{ r: 3, fill: "#7dd3fc", stroke: "#101928", strokeWidth: 2 }} isAnimationActive={false} connectNulls={false} />}
              {isFramePipeline && <Line yAxisId="fps" type="monotone" dataKey="renderFpsHeld" name="渲染（空闲保持）" stroke="#7dd3fc" strokeWidth={1.5} strokeDasharray="5 4" dot={false} activeDot={false} isAnimationActive={false} connectNulls={false} opacity={0.65} />}
              <Line yAxisId="fps" type="monotone" dataKey={isFramePipeline ? "fps" : "viewValue"} name={isFramePipeline ? "内容呈现" : meta.label} stroke={isFramePipeline ? "#f4b942" : meta.color} strokeWidth={2} dot={false} activeDot={{ r: 3, fill: isFramePipeline ? "#f4b942" : meta.color, stroke: "#101928", strokeWidth: 2 }} isAnimationActive={false} connectNulls />
              {isFramePipeline && <Line yAxisId="jank" type="monotone" dataKey="jank_pct" name="逐帧 Jank" stroke="#fb7185" strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls isAnimationActive={false} />}
            </LineChart>
          </ResponsiveContainer>
        ) : <div className="scanline-accent grid h-full place-items-center border-t border-dashed border-slate-700/60"><div className="text-center"><Activity className="mx-auto mb-2 text-slate-600" size={18} /><p className="font-telemetry text-[11px] tracking-wider text-slate-500">NO TELEMETRY YET</p></div></div>}
      </div>
    </section>
  );
}
export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedSerial, setSelectedSerial] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(60);
  const [intervalMs, setIntervalMs] = useState(1000);
  const [metrics, setMetrics] = useState<Record<MetricKey, boolean>>({ cpu: true, memory: true, fps: true });
  const [surfaceLayer, setSurfaceLayer] = useState("");
  const [layers, setLayers] = useState<string[]>([]);
  const [frameCapability, setFrameCapability] = useState<FrameCapabilities | null>(null);
  const [logPaths, setLogPaths] = useState({ anr: "", crash: "", tombstone: "", exportRoot: "" });
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<MonitorSession | null>(null);
  const [points, setPoints] = useState<SessionPoint[]>([]);
  const [processMetric, setProcessMetric] = useState<ProcessMetric>("cpu");
  const [processes, setProcesses] = useState<ProcessRow[]>([]);
  const [events, setEvents] = useState<MonitorEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState("");

  const enabledMetrics = useMemo(() => (Object.keys(metrics) as MetricKey[]).filter((metric) => metrics[metric]), [metrics]);
  const chartHeight = enabledMetrics.length === 1 ? 400 : enabledMetrics.length === 2 ? 278 : 202;
  const selectedDevice = devices.find((device) => device.serial === selectedSerial);
  const running = session?.state === "running";
  const totalRamKb = points.at(-1)?.total_ram_kb ?? null;

  const refreshDevices = useCallback(async () => {
    try {
      const [healthPayload, devicePayload, activePayload] = await Promise.all([
        requestApi<Health>("/api/health"),
        requestApi<Device[]>("/api/devices"),
        requestApi<MonitorSession | null>("/api/sessions/active").catch(() => null),
      ]);
      setHealth(healthPayload);
      setDevices(devicePayload);
      setSelectedSerial((current) => current || devicePayload.find((device) => device.state === "device")?.serial || "");
      // 后端可能仍有正在运行的采样会话（例如前端刷新）：恢复接管，而不是显示“未开始”状态。
      if (activePayload) {
        setActiveSessionId(activePayload.session_id);
        setSession(activePayload);
      }
      setApiError("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "无法连接本地后端";
      setHealth({ status: "degraded", adb: "unavailable", device_count: 0, active_sessions: 0, detail: message });
      setApiError(message);
    }
  }, []);

  const refreshSession = useCallback(async (sessionId: string) => {
    try {
      const [sessionPayload, seriesPayload, processPayload, eventPayload] = await Promise.all([
        requestApi<MonitorSession>(`/api/sessions/${sessionId}`),
        requestApi<{ points: SessionPoint[] }>(`/api/sessions/${sessionId}/series?limit=180`),
        requestApi<{ processes: ProcessRow[] }>(`/api/sessions/${sessionId}/processes?metric=${processMetric}&limit=10`),
        requestApi<{ events: MonitorEvent[] }>(`/api/sessions/${sessionId}/events`),
      ]);
      setSession(sessionPayload);
      setPoints(seriesPayload.points);
      setProcesses(processPayload.processes);
      setEvents(eventPayload.events);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "刷新会话失败");
    }
  }, [processMetric]);

  useEffect(() => { void refreshDevices(); }, [refreshDevices]);

  useEffect(() => {
    if (!selectedSerial || !metrics.fps) {
      setLayers([]);
      return;
    }
    void requestApi<{ layers: string[] }>(`/api/surface-layers?serial=${encodeURIComponent(selectedSerial)}`)
      .then((payload) => setLayers(payload.layers.slice(0, 80)))
      .catch(() => setLayers([]));
  }, [selectedSerial, metrics.fps]);

  useEffect(() => {
    if (!selectedSerial || !metrics.fps) {
      setFrameCapability(null);
      return;
    }
    void requestApi<FrameCapabilities>(`/api/frame-capabilities?serial=${encodeURIComponent(selectedSerial)}`)
      .then(setFrameCapability)
      .catch(() => setFrameCapability(null));
  }, [selectedSerial, metrics.fps]);

  useEffect(() => {
    if (!activeSessionId) return;
    void refreshSession(activeSessionId);
    const timer = window.setInterval(() => { void refreshSession(activeSessionId); }, 1000);
    return () => window.clearInterval(timer);
  }, [activeSessionId, refreshSession]);

  const startSession = async () => {
    if (!selectedSerial) {
      toast.error("请先连接并授权一台 ADB 设备");
      return;
    }
    if (!enabledMetrics.length) {
      toast.error("请至少启用一项测试");
      return;
    }
    setLoading(true);
    try {
      const started = await requestApi<MonitorSession>("/api/sessions", {
        method: "POST",
        body: JSON.stringify({
          serial: selectedSerial,
          duration_seconds: durationMinutes * 60,
          interval_ms: intervalMs,
          metrics,
          surface_layer: metrics.fps && surfaceLayer ? surfaceLayer : null,
          anr_path: logPaths.anr.trim() || null,
          crash_path: logPaths.crash.trim() || null,
          tombstone_path: logPaths.tombstone.trim() || null,
          log_export_root: logPaths.exportRoot.trim() || null,
        }),
      });
      setActiveSessionId(started.session_id);
      setSession(started);
      setPoints([]);
      setProcesses([]);
      setEvents([]);
      toast.success("采样会话已启动", { description: "数据将按设定节拍落盘，并最多运行 60 分钟。" });
    } catch (error) {
      toast.error("无法启动采样", { description: error instanceof Error ? error.message : "未知错误" });
    } finally {
      setLoading(false);
    }
  };

  const stopSession = async () => {
    if (!activeSessionId) return;
    setLoading(true);
    try {
      await requestApi(`/api/sessions/${activeSessionId}/stop`, { method: "POST" });
      await refreshSession(activeSessionId);
      toast.success("采样已停止", { description: "会话汇总已写入本地 SQLite；设备日志导出结果见会话事件区。" });
    } catch (error) {
      toast.error("停止采样失败", { description: error instanceof Error ? error.message : "未知错误" });
    } finally {
      setLoading(false);
    }
  };

  const downloadCsv = () => {
    if (!activeSessionId) return;
    window.open(`${API_BASE}/api/sessions/${activeSessionId}/export`, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="min-h-screen bg-[#101928] text-slate-100">
      <header className="relative h-24 overflow-hidden border-b border-slate-700/70 bg-[#101928]">
        <img src="/manus-storage/telemetry-hero_91d70b81.jpg" alt="" className="absolute inset-0 h-full w-full object-cover object-center opacity-45" />
        <div className="absolute inset-0 bg-gradient-to-r from-[#101928] via-[#101928]/92 to-[#101928]/45" />
        <div className="relative mx-auto flex h-full max-w-[1600px] items-center justify-between px-5 lg:px-8">
          <div className="flex items-center gap-3.5">
            <div className="grid h-11 w-11 place-items-center rounded-md border border-cyan-200/25 bg-cyan-200/8 p-2 shadow-[0_0_30px_rgba(57,214,211,0.14)]">
              <img src="/manus-storage/telemetry-logo_5446425a.png" alt="Telemetry Console" className="h-full w-full object-contain" />
            </div>
            <div>
              <p className="font-telemetry text-[10px] uppercase tracking-[0.22em] text-cyan-200/80">Android Vehicle Lab</p>
              <h1 className="text-lg font-semibold tracking-[0.08em] text-white sm:text-xl">车机性能遥测控制台</h1>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <StatusPill health={health} />
            <span className="hidden font-telemetry text-[10px] uppercase tracking-wider text-slate-400 md:block">v0.1 · local only</span>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1600px] gap-5 p-4 lg:grid-cols-[314px_minmax(0,1fr)] lg:p-6">
        <aside className="telemetry-panel h-fit overflow-hidden rounded-md border border-slate-700/70 bg-slate-900/80 lg:sticky lg:top-5">
          <div className="border-b border-slate-700/70 px-4 py-3.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2"><SlidersHorizontal size={15} className="text-cyan-200" /><h2 className="text-sm font-semibold">会话骨架</h2></div>
              <span className="font-telemetry text-[10px] text-slate-500">01 / CONFIG</span>
            </div>
          </div>
          <div className="space-y-5 p-4">
            <section>
              <div className="mb-2 flex items-center justify-between"><label className="text-xs font-medium text-slate-300">目标车机</label><button onClick={() => void refreshDevices()} className="text-slate-500 transition hover:text-cyan-200" aria-label="刷新 ADB 设备"><RefreshCw size={14} /></button></div>
              <div className="relative">
                <Usb size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <select value={selectedSerial} onChange={(event) => setSelectedSerial(event.target.value)} disabled={running} className="h-10 w-full appearance-none rounded-sm border border-slate-600/80 bg-slate-950/55 pl-9 pr-7 text-xs text-slate-200 outline-none transition focus:border-cyan-300/70 disabled:cursor-not-allowed disabled:opacity-60">
                  <option value="">选择已连接设备</option>
                  {devices.map((device) => <option key={device.serial} value={device.serial} disabled={device.state !== "device"}>{device.model || device.serial} · {device.state}</option>)}
                </select>
              </div>
              <p className="mt-2 font-telemetry text-[10px] text-slate-500">{selectedDevice ? `${selectedDevice.serial} · Android ${selectedDevice.android_version || "—"}` : "USB / TCP ADB · 需完成设备授权"}</p>
            </section>

            <section>
              <p className="mb-2 text-xs font-medium text-slate-300">采样窗口</p>
              <div className="grid grid-cols-4 gap-1.5">
                {[5, 15, 30, 60].map((minutes) => <button key={minutes} disabled={running} onClick={() => setDurationMinutes(minutes)} className={`rounded-sm border py-2 font-telemetry text-[11px] transition ${durationMinutes === minutes ? "border-cyan-300/45 bg-cyan-300/12 text-cyan-100" : "border-slate-700 bg-slate-950/30 text-slate-500 hover:border-slate-500"}`}>{minutes}m</button>)}
              </div>
              <div className="mt-2 flex items-center justify-between rounded-sm border border-slate-700/60 bg-slate-950/30 px-2.5 py-2"><span className="text-[11px] text-slate-400">采样间隔</span><select value={intervalMs} onChange={(event) => setIntervalMs(Number(event.target.value))} disabled={running} className="bg-transparent font-telemetry text-[11px] text-cyan-100 outline-none"><option value={500}>0.5s</option><option value={1000}>1.0s</option><option value={2000}>2.0s</option><option value={5000}>5.0s</option></select></div>
            </section>

            <section>
              <div className="mb-2 flex items-center justify-between"><p className="text-xs font-medium text-slate-300">采样模块</p><span className="font-telemetry text-[10px] text-cyan-200">{enabledMetrics.length}/3 ON</span></div>
              <div className="space-y-2">
                {(Object.keys(METRIC_META) as MetricKey[]).map((metric) => <MetricToggle key={metric} metric={metric} checked={metrics[metric]} onCheckedChange={(checked) => !running && setMetrics((current) => ({ ...current, [metric]: checked }))} />)}
              </div>
            </section>

            {metrics.fps && <section>
              <div className="mb-2 flex items-center gap-2"><Layers3 size={14} className="text-amber-200" /><p className="text-xs font-medium text-slate-300">帧率数据源 · SurfaceFlinger layer</p></div>
              <select value={surfaceLayer} disabled={running || !selectedSerial} onChange={(event) => setSurfaceLayer(event.target.value)} className="h-9 w-full rounded-sm border border-slate-700 bg-slate-950/35 px-2 text-[11px] text-slate-300 outline-none focus:border-amber-300/60">
                <option value="">自动选择活跃 layer</option>
                {layers.map((layer) => <option key={layer} value={layer}>{layer}</option>)}
              </select>
              {frameCapability ? (
                <div className="mt-2 rounded-sm border border-slate-700 bg-slate-950/35 px-2.5 py-2">
                  <div className="flex items-center justify-between">
                    <p className="font-telemetry text-[10px] uppercase tracking-wider text-slate-500">Android {frameCapability.android_release || (frameCapability.sdk_version ?? "—")} · FRAME SOURCE</p>
                    <span className="font-telemetry text-[10px] text-cyan-200">→ {frameSourceLabel(frameCapability.recommended_source)}</span>
                  </div>
                  <p className="mt-1.5 text-[10px] leading-4 text-slate-400">
                    FrameTimeline {frameCapability.sources.frametimeline ? "✓" : "✗"} · framestats {frameCapability.sources.framestats ? "✓" : "✗"} · SF latency ✓
                  </p>
                  {frameCapability.notes.length > 0 && <p className="mt-1 text-[10px] leading-4 text-amber-100/70">{frameCapability.notes[0]}</p>}
                </div>
              ) : <p className="mt-1.5 text-[10px] leading-4 text-slate-500">选择设备后自动探测可用的逐帧数据源；采集时按版本自动降级。</p>}
            </section>}

            <section>
              <div className="mb-2 flex items-center gap-2"><Download size={14} className="text-emerald-200" /><p className="text-xs font-medium text-slate-300">设备日志导出</p></div>
              <div className="space-y-1.5">
                <input value={logPaths.anr} disabled={running} onChange={(event) => setLogPaths((current) => ({ ...current, anr: event.target.value }))} placeholder="ANR 路径，如 /data/anr" className="h-8 w-full rounded-sm border border-slate-700 bg-slate-950/35 px-2 text-[11px] text-slate-300 outline-none focus:border-emerald-300/60 disabled:cursor-not-allowed disabled:opacity-60" />
                <input value={logPaths.crash} disabled={running} onChange={(event) => setLogPaths((current) => ({ ...current, crash: event.target.value }))} placeholder="Crash 路径，如 /data/crash" className="h-8 w-full rounded-sm border border-slate-700 bg-slate-950/35 px-2 text-[11px] text-slate-300 outline-none focus:border-emerald-300/60 disabled:cursor-not-allowed disabled:opacity-60" />
                <input value={logPaths.tombstone} disabled={running} onChange={(event) => setLogPaths((current) => ({ ...current, tombstone: event.target.value }))} placeholder="Tombstone 路径，如 /data/tombstones" className="h-8 w-full rounded-sm border border-slate-700 bg-slate-950/35 px-2 text-[11px] text-slate-300 outline-none focus:border-emerald-300/60 disabled:cursor-not-allowed disabled:opacity-60" />
                <input value={logPaths.exportRoot} disabled={running} onChange={(event) => setLogPaths((current) => ({ ...current, exportRoot: event.target.value }))} placeholder="导出根目录（留空 = 项目根/DevicesLogs）" className="h-8 w-full rounded-sm border border-slate-700 bg-slate-950/35 px-2 text-[11px] text-slate-300 outline-none focus:border-emerald-300/60 disabled:cursor-not-allowed disabled:opacity-60" />
              </div>
              <p className="mt-1.5 text-[10px] leading-4 text-slate-500">停止测试时自动导出到 <code className="font-telemetry">DevicesLogs/&lt;结束时间&gt;/ANR·Crash·Tombstone/</code> 并清理车机日志（保留目录）。留空的类型不导出，仅提示。</p>
            </section>

            <InteractionDiagnostic serial={selectedSerial} />
            <div className="border-t border-slate-700/70 pt-4">
              {running ? (
                <Button onClick={() => void stopSession()} disabled={loading} className="h-10 w-full bg-red-400 text-red-950 hover:bg-red-300"><CircleStop size={15} /> {loading ? "正在停止…" : "停止并汇总"}</Button>
              ) : (
                <Button onClick={() => void startSession()} disabled={loading || !selectedSerial || enabledMetrics.length === 0} className="h-10 w-full bg-cyan-300 text-slate-950 hover:bg-cyan-200"><Play size={15} fill="currentColor" /> {loading ? "正在建立会话…" : "开始连续采样"}</Button>
              )}
              <p className="mt-2 text-center font-telemetry text-[10px] text-slate-500">HARD LIMIT · 60 MINUTES</p>
            </div>
          </div>
        </aside>

        <div className="min-w-0 space-y-5">
          <section className="telemetry-panel relative overflow-hidden rounded-md border border-slate-700/70 bg-slate-900/80">
            <img src="/manus-storage/telemetry-texture_c54753f2.jpg" alt="" className="pointer-events-none absolute inset-0 h-full w-full object-cover opacity-[0.08]" />
            <div className="relative flex flex-col gap-4 p-4 xl:flex-row xl:items-center xl:justify-between">
              <div className="flex items-center gap-4">
                <div className={`grid h-12 w-12 place-items-center rounded-md border ${running ? "border-cyan-200/30 bg-cyan-200/10 text-cyan-200" : "border-slate-700 bg-slate-950/35 text-slate-500"}`}>
                  {running ? <Activity className="telemetry-live" size={22} /> : <TimerReset size={20} />}
                </div>
                <div>
                  <p className="font-telemetry text-[10px] uppercase tracking-[0.18em] text-slate-500">Session status</p>
                  <h2 className="mt-0.5 text-lg font-semibold text-slate-100">{running ? "正在连续采样" : session ? `会话已${session.state === "completed" ? "完成" : session.state === "stopped" ? "停止" : session.state}` : "等待建立会话"}</h2>
                  <p className="mt-1 text-xs text-slate-500">{running ? `目标 ${session?.serial} · ${session?.summary?.sample_count || 0} 个有效采样周期` : "数据仅保存于本机 data/ 会话目录，曲线窗口最多保留最近 180 点。"}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 xl:justify-end">
                <div className="rounded-sm border border-slate-700 bg-slate-950/35 px-3 py-2"><p className="font-telemetry text-[9px] uppercase tracking-wider text-slate-500">Window</p><p className="font-telemetry mt-0.5 text-xs text-slate-200">{session ? `${Math.ceil(session.duration_seconds / 60)}m @ ${session.interval_ms / 1000}s` : `${durationMinutes}m @ ${intervalMs / 1000}s`}</p></div>
                <div className="rounded-sm border border-slate-700 bg-slate-950/35 px-3 py-2"><p className="font-telemetry text-[9px] uppercase tracking-wider text-slate-500">Storage</p><p className="font-telemetry mt-0.5 text-xs text-slate-200">SQLite · WAL</p></div>
                {activeSessionId && <Button variant="outline" onClick={downloadCsv} className="h-auto border-slate-600 bg-slate-950/20 text-slate-300 hover:bg-slate-800 hover:text-cyan-100"><Download size={14} /> 导出 CSV</Button>}
              </div>
            </div>
          </section>

          {apiError && <div className="flex items-start gap-2 rounded-sm border border-amber-300/25 bg-amber-300/8 px-3 py-2.5 text-xs text-amber-100"><AlertTriangle size={15} className="mt-0.5 shrink-0" /><div><strong>本地服务提示：</strong>{apiError}<p className="mt-1 text-amber-100/65">运行 <code className="font-telemetry">./run-local.sh</code> 后，刷新设备列表以重新连接。</p></div><button onClick={() => setApiError("")} className="ml-auto text-amber-100/60 hover:text-amber-100" aria-label="关闭提示"><X size={14} /></button></div>}

          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {[
              { label: "CPU 平均 / 峰值", metric: metricSummary(session, "cpu"), icon: Cpu, tone: "text-cyan-200", output: (item: SummaryMetric) => `${formatValue(item.average, "%")} / ${formatValue(item.peak, "%")}` },
              { label: "PSS 平均 / 峰值", metric: metricSummary(session, "memory_pss"), icon: HardDrive, tone: "text-lime-200", output: (item: SummaryMetric) => formatMemorySummary(item, totalRamKb) },
              { label: "RSS 平均 / 峰值", metric: metricSummary(session, "memory_rss"), icon: HardDrive, tone: "text-emerald-200", output: (item: SummaryMetric) => formatValue(item.average === null ? null : item.average / 1024, " MiB") + " / " + formatValue(item.peak === null ? null : item.peak / 1024, " MiB") },
              { label: "应用渲染 FPS 平均 / 最低", metric: metricSummary(session, "app_render_fps"), icon: Activity, tone: "text-sky-200", output: (item: SummaryMetric) => formatValue(item.average, " fps") + " / " + formatValue(item.peak, " fps") },
              { label: "内容呈现 FPS 平均 / 最低", metric: metricSummary(session, "fps"), icon: Gauge, tone: "text-amber-200", output: (item: SummaryMetric) => formatValue(item.average, " fps") + " / " + formatValue(item.peak, " fps") },
              { label: "渲染 Jank 平均 / 峰值", metric: metricSummary(session, "app_jank_pct"), icon: AlertTriangle, tone: "text-rose-200", output: (item: SummaryMetric) => formatValue(item.average, "%") + " / " + formatValue(item.peak, "%") },
              { label: "逐帧 Jank 平均 / 峰值", metric: metricSummary(session, "frame_jank_pct"), icon: AlertTriangle, tone: "text-rose-200", output: (item: SummaryMetric) => formatValue(item.average, "%") + " / " + formatValue(item.peak, "%") },
              { label: "逐帧 P95 帧耗时 平均 / 峰值", metric: metricSummary(session, "frame_p95"), icon: Activity, tone: "text-violet-200", output: (item: SummaryMetric) => formatValue(item.average, " ms") + " / " + formatValue(item.peak, " ms") },
            ].map(({ label, metric, icon: Icon, tone, output }) => <article key={label} className="telemetry-panel rounded-md border border-slate-700/70 bg-slate-900/80 p-3.5"><div className="flex items-center justify-between"><p className="text-[11px] text-slate-400">{label}</p><Icon size={14} className={tone} /></div><p className="font-telemetry mt-2.5 text-sm font-medium text-slate-100">{metric ? output(metric) : "— / —"}</p><p className="mt-1 font-telemetry text-[10px] text-slate-500">{metric?.valid_count ? `${metric.valid_count} VALID SAMPLES` : "NOT RECORDED"}</p></article>)}
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between px-1"><div><p className="font-telemetry text-[10px] uppercase tracking-[0.18em] text-cyan-200">Telemetry bands</p><h2 className="mt-1 text-sm font-semibold">实时性能轨迹</h2></div><p className="font-telemetry text-[10px] text-slate-500">WINDOW · {points.length}/180</p></div>
            {enabledMetrics.length > 0 ? enabledMetrics.map((metric) => <MetricChart key={metric} metric={metric} points={points} height={chartHeight} />) : <div className="grid h-48 place-items-center rounded-md border border-dashed border-slate-700 bg-slate-900/45 text-center"><div><SlidersHorizontal className="mx-auto mb-2 text-slate-600" size={20} /><p className="text-sm text-slate-400">请启用至少一个采样模块</p></div></div>}
          </section>

          <section className="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_330px]">
            <article className="telemetry-panel overflow-hidden rounded-md border border-slate-700/70 bg-slate-900/80">
              <div className="flex items-center justify-between border-b border-slate-700/70 px-4 py-3"><div><p className="font-telemetry text-[10px] uppercase tracking-[0.16em] text-cyan-200">Process pressure</p><h2 className="mt-0.5 text-sm font-semibold">应用 / 进程聚合排行</h2></div><select value={processMetric} onChange={(event) => setProcessMetric(event.target.value as ProcessMetric)} className="rounded-sm border border-slate-700 bg-slate-950/30 px-2 py-1 text-[11px] text-slate-300 outline-none"><option value="cpu">CPU</option><option value="pss">PSS</option><option value="rss">RSS</option></select></div>
              <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left"><thead className="bg-slate-950/35 font-telemetry text-[10px] uppercase tracking-wider text-slate-500"><tr><th className="px-4 py-2.5 font-medium">Process</th><th className="px-3 py-2.5 font-medium">PID</th><th className="px-3 py-2.5 font-medium">Average</th><th className="px-3 py-2.5 font-medium">Peak</th><th className="px-4 py-2.5 text-right font-medium">Samples</th></tr></thead><tbody>{processes.length ? processes.map((row, index) => <tr key={`${row.process_name}-${row.pid ?? index}`} className="border-t border-slate-800/85 text-xs text-slate-300"><td className="max-w-[260px] truncate px-4 py-3 font-medium text-slate-200">{row.process_name}</td><td className="font-telemetry px-3 py-3 text-slate-500">{row.pid ?? "—"}</td><td className="font-telemetry px-3 py-3 text-cyan-100">{formatValue(row.average, processMetric === "cpu" ? "%" : " KiB")}</td><td className="font-telemetry px-3 py-3 text-amber-100">{formatValue(row.peak, processMetric === "cpu" ? "%" : " KiB")}</td><td className="font-telemetry px-4 py-3 text-right text-slate-500">{row.samples}</td></tr>) : <tr><td colSpan={5} className="px-4 py-9 text-center text-xs text-slate-500">开始采样后，按所选指标显示进程的平均值与峰值。</td></tr>}</tbody></table></div>
            </article>
            <article className="telemetry-panel overflow-hidden rounded-md border border-slate-700/70 bg-slate-900/80">
              <div className="border-b border-slate-700/70 px-4 py-3"><p className="font-telemetry text-[10px] uppercase tracking-[0.16em] text-amber-200">Session events</p><h2 className="mt-0.5 text-sm font-semibold">会话事件</h2></div>
              <div className="max-h-[310px] divide-y divide-slate-800/85 overflow-y-auto">{events.length ? events.map((event, index) => <div key={`${event.ts_ms}-${index}`} className="flex gap-2.5 px-4 py-3"><span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${event.severity === "error" ? "bg-red-300" : "bg-amber-300"}`} /><div className="min-w-0"><p className="font-telemetry text-[10px] text-slate-500">{formatTime(event.ts_ms)} · {event.code}</p><p className="mt-1 break-words text-[11px] leading-4 text-slate-300">{event.message}</p></div></div>) : <div className="px-4 py-9 text-center"><Check className="mx-auto mb-2 text-slate-600" size={18} /><p className="text-xs text-slate-500">暂无事件记录</p></div>}</div>
            </article>
          </section>
          <footer className="flex flex-col justify-between gap-2 border-t border-slate-700/55 px-1 pt-4 pb-2 text-[10px] text-slate-500 sm:flex-row"><p>ADB 指令在本地后端执行；任一模块失败仅降级该模块并写入会话事件。</p><p className="font-telemetry">LOCALHOST · SQLITE WAL · NO CLOUD UPLOAD</p></footer>
        </div>
      </main>
    </div>
  );
}
