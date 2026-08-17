import { useEffect, useState } from "react";
import { Download, Play, Radio } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";

type InteractionRecord = {
  interaction_id: string;
  serial: string;
  duration_seconds: number;
  state: "queued" | "preparing" | "pushing_config" | "recording" | "pulling_trace" | "completed" | "failed";
  started_at_ms?: number | null;
  ended_at_ms?: number | null;
  trace_ready: boolean;
  trace_file?: string | null;
  error?: string | null;
  mode: string;
  notice: string;
};

const API_BASE = (import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8090").replace(/\/$/, "");

async function api<T>(path: string, options?: RequestInit): Promise<T> {
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

export function InteractionDiagnostic({ serial }: { serial: string }) {
  const [duration, setDuration] = useState(15);
  const [record, setRecord] = useState<InteractionRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const recording = ["queued", "preparing", "pushing_config", "recording", "pulling_trace"].includes(record?.state || "");

  useEffect(() => {
    if (!recording || !record) return;
    const timer = window.setInterval(() => {
      void api<InteractionRecord>(`/api/interactions/${record.interaction_id}`)
        .then(setRecord)
        .catch((error: Error) => toast.error("刷新交互诊断状态失败", { description: error.message }));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [record, recording]);

  const start = async () => {
    if (!serial) {
      toast.error("请先选择已连接车机");
      return;
    }
    setLoading(true);
    try {
      const started = await api<InteractionRecord>("/api/interactions", {
        method: "POST",
        body: JSON.stringify({ serial, duration_seconds: duration }),
      });
      setRecord(started);
      toast.success("交互诊断已开始", { description: `请在车机上于 ${duration} 秒内执行真实点击或滑动。` });
    } catch (error) {
      toast.error("无法启动交互诊断", { description: error instanceof Error ? error.message : "未知错误" });
    } finally {
      setLoading(false);
    }
  };

  const download = () => {
    if (!record?.trace_ready) return;
    window.open(`${API_BASE}/api/interactions/${record.interaction_id}/trace`, "_blank", "noopener,noreferrer");
  };

  return (
    <section className="rounded-sm border border-amber-300/20 bg-amber-300/[0.045] p-3">
      <div className="flex items-start gap-2.5">
        <span className={`mt-0.5 grid h-7 w-7 place-items-center rounded-sm ${recording ? "bg-amber-300/15 text-amber-200" : "bg-slate-800 text-slate-400"}`}><Radio size={15} className={recording ? "telemetry-live" : ""} /></span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-slate-200">真实操作诊断</p>
          <p className="mt-0.5 text-[10px] leading-4 text-slate-500">Perfetto · INPUT / FRAME LIFECYCLE · 不注入事件</p>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between rounded-sm border border-slate-700/60 bg-slate-950/30 px-2.5 py-2">
        <span className="text-[11px] text-slate-400">诊断窗口</span>
        <select value={duration} disabled={recording} onChange={(event) => setDuration(Number(event.target.value))} className="bg-transparent font-telemetry text-[11px] text-amber-100 outline-none disabled:opacity-60">
          <option value={10}>10s</option><option value={15}>15s</option><option value={30}>30s</option><option value={60}>60s</option>
        </select>
      </div>
      <Button onClick={() => void start()} disabled={!serial || loading || recording} variant="outline" className="mt-2.5 h-9 w-full border-amber-300/35 bg-amber-300/10 text-amber-100 hover:bg-amber-300/20 hover:text-amber-50">
        <Play size={13} fill="currentColor" /> {loading ? "正在启动…" : recording ? "正在录制真实操作…" : "启动交互诊断"}
      </Button>
      {record && <div className="mt-2.5 border-t border-amber-300/10 pt-2.5 text-[10px] leading-4 text-slate-400">
        <p><span className="font-telemetry text-amber-100">{record.state.toUpperCase()}</span> · {record.notice}</p>
        {record.state === "completed" && <Button variant="ghost" onClick={download} className="mt-1.5 h-7 px-0 text-[10px] text-cyan-200 hover:bg-transparent hover:text-cyan-100"><Download size={12} /> 下载 Perfetto trace</Button>}
        {record.state === "failed" && <p className="mt-1 text-rose-300">{record.error || "诊断失败"}</p>}
      </div>}
    </section>
  );
}
