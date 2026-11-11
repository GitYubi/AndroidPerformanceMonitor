import { useCallback, useEffect, useState, type SetStateAction } from "react";
import { BACKEND_URL } from "@/lib/backend";
import type {
  CanCatalog as Catalog,
  CanMessage as Message,
  CanSignal as Signal,
  CanValue as Value,
} from "@/lib/canCatalog";
import { getCanSection } from "@/lib/canNavigation";
import { useSearchParams } from "wouter";
import { toast } from "sonner";
import CanSignalTrend, {
  type SignalSelection,
} from "@/components/CanSignalTrend";

type Draft = {
  values: Record<string, string>;
  period: number;
  count: number;
  fd: boolean;
  brs: boolean;
};
type Profile = Record<string, Record<string, Value>>;
type Task = {
  channel: number;
  name: string;
  active: boolean;
  signals: Record<string, unknown>;
  error?: string;
};
type Status = {
  open: boolean;
  channels?: number[];
  recording?: boolean;
  hardware_periodic?: boolean;
  wake_active?: boolean;
  tasks: Task[];
  error?: string;
};
type Frame = {
  sequence: number;
  timestamp: number;
  channel: number;
  arbitration_id: number;
  data: number[];
  is_rx: boolean;
};
type Directory = {
  path: string;
  parent: string;
  entries: { name: string; path: string; directory: boolean }[];
};
const field = "h-7 rounded-md border border-border bg-background px-2 text-xs";
function displaySignal(value: unknown) {
  if (value == null) return "—";
  if (typeof value === "object") {
    if ("name" in value) return String(value.name);
    if ("raw" in value) return String(value.raw);
    return "—";
  }
  return String(value);
}
const button =
  "inline-flex h-7 shrink-0 items-center justify-center rounded-md border border-border px-2.5 text-xs transition-colors bg-card text-card-foreground hover:border-primary/50 hover:bg-accent hover:text-accent-foreground disabled:opacity-40";

export default function CanBus() {
  const [status, setStatus] = useState<Status>({ open: false, tasks: [] });
  const [search, setSearch] = useSearchParams();
  const section = getCanSection(search);
  const [trendSelection, setTrendSelection] = useState<SignalSelection>({
    message: "",
    signal: "",
  });
  const [backend, setBackend] = useState("hardware");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [directory, setDirectory] = useState<Directory | null>(null);
  const [folderInput, setFolderInput] = useState("");
  const [channel, setChannel] = useState(0);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => setName(""), [section]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [wakeDrafts, setWakeDrafts] = useState<
    Record<string, Record<string, string>>
  >({});
  const [profile, setProfile] = useState<Profile>({});

  const [stopAfter, setStopAfter] = useState(true);

  const [frames, setFrames] = useState<Frame[]>([]);
  const [error, setError] = useState("");
  const [record, setRecord] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useCallback(async (path: string, body?: unknown) => {
    const r = await fetch(
      `${BACKEND_URL}/api/can${path}`,
      body === undefined
        ? undefined
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
    );
    const data = await r.json();
    if (!r.ok)
      throw Error(
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail)
      );
    return data;
  }, []);
  const refresh = useCallback(
    async () => setStatus(await request("/status")),
    [request]
  );
  useEffect(() => {
    let live = true;
    const update = () =>
      refresh().catch(e => {
        if (live) setError(String(e));
      });
    void update();
    const t = setInterval(() => void update(), 1500);
    void request("/dbc")
      .then(d => {
        if (live) {
          setCatalog(d);
        }
      })
      .catch(e => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [refresh, request]);
  useEffect(() => {
    if (!catalog?.key) return;
    void request("/wake/profile")
      .then(acceptProfile)
      .catch(e => setError(String(e)));
  }, [catalog?.key, request]);
  useEffect(() => {
    if (!status.open || section !== "receive") return;
    const update = () =>
      request("/frames")
        .then(d => setFrames(d.frames.slice(-500)))
        .catch(e => setError(String(e)));
    void update();
    const t = setInterval(() => void update(), 1000);
    return () => clearInterval(t);
  }, [status.open, section, request]);
  const action = async (path: string, body: unknown, showSuccess = true) => {
    setBusy(true);
    setError("");
    try {
      const result = await request(path, body);
      await refresh();
      if (showSuccess) {
        if (result.completed === false) {
          toast.info("触发已被后续更新或停止接管");
        } else {
          toast.success("操作完成");
        }
      }
      return result;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  const browse = async (path = "") => {
    try {
      const d = await request(`/files?path=${encodeURIComponent(path)}`);
      setDirectory(d);
      setFolderInput(d.path);
    } catch (e) {
      setError(String(e));
    }
  };
  const load = async (path: string) => {
    const d = await action("/dbc/load", { path }, false);
    if (d) {
      setCatalog(d);
      setDirectory(null);
      if (d.key !== catalog?.key) {
        setName("");
        setDrafts({});
        acceptProfile({});
      }
    }
  };
  const waking = section === "wake";
  const selectedNames = waking ? Object.keys(profile) : Object.keys(drafts);
  const message = selectedNames.includes(name)
    ? catalog?.messages.find(m => m.name === name)
    : undefined;
  function defaultDraft(m?: Message): Draft {
    return {
      values: {},
      period: m?.cycle_time || 100,
      count: 3,
      fd: m?.can_fd ?? true,
      brs: false,
    };
  }
  const current = drafts[name] || defaultDraft(message);
  const values = waking ? wakeDrafts[name] || {} : current.values;
  function updateDraft(n: string, patch: Partial<Draft>) {
    setDrafts(p => ({
      ...p,
      [n]: {
        ...(p[n] || defaultDraft(catalog?.messages.find(m => m.name === n))),
        ...patch,
      },
    }));
  }
  function setValues(next: SetStateAction<Record<string, string>>) {
    const v = typeof next === "function" ? next(values) : next;
    if (waking) setWakeDrafts(p => ({ ...p, [name]: v }));
    else updateDraft(name, { values: v });
  }
  function acceptProfile(p: Profile) {
    setProfile(p);
    setWakeDrafts(
      Object.fromEntries(
        Object.entries(p).map(([n, v]) => [
          n,
          Object.fromEntries(
            Object.entries(v).map(([k, x]) => [
              k,
              typeof x === "number" ? String(x) : `raw:${x.raw}`,
            ])
          ),
        ])
      )
    );
  }
  function selectMessage(m?: Message) {
    if (!m) return;
    setName(m.name);
    if (waking) {
      setProfile(p => (p[m.name] ? p : { ...p, [m.name]: {} }));
      setWakeDrafts(p => ({ ...p, [m.name]: p[m.name] || {} }));
    } else
      setDrafts(p => (p[m.name] ? p : { ...p, [m.name]: defaultDraft(m) }));
  }
  function removeMessage(n: string) {
    if (waking) {
      setProfile(p => {
        const c = { ...p };
        delete c[n];
        return c;
      });
      setWakeDrafts(p => {
        const c = { ...p };
        delete c[n];
        return c;
      });
    } else
      setDrafts(p => {
        const c = { ...p };
        delete c[n];
        return c;
      });
    if (name === n) setName("");
  }
  function parsedValues(input = values): Record<string, Value> {
    const result: Record<string, Value> = {};
    for (const [key, text] of Object.entries(input)) {
      if (text === "") continue;
      if (text.startsWith("raw:")) result[key] = { raw: Number(text.slice(4)) };
      else {
        const n = Number(text);
        if (!Number.isFinite(n)) throw Error(`${key} 需要有效数值`);
        result[key] = n;
      }
    }
    return result;
  }
  const sendDraft = async (n: string, path: string) => {
    if (path === "/stop") {
      await action(path, { channel, name: n });
      return;
    }
    try {
      const d =
        drafts[n] || defaultDraft(catalog?.messages.find(m => m.name === n));
      await action(path, {
        channel,
        name: n,
        signals: parsedValues(d.values),
        can_fd: d.fd,
        fd_brs: d.brs,
        cycle_time: d.period,
        send_count: d.count,
        stop_after: stopAfter,
        count: d.count > 0 ? d.count : 1,
      });
    } catch (e) {
      setError(String(e));
    }
  };
  const importWake = async (file: File) => {
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(`${BACKEND_URL}/api/can/wake/import`, {
        method: "POST",
        body: form,
      });
      const data = await r.json();
      if (!r.ok) throw Error(data.detail);
      acceptProfile(data);
      toast.success("唤醒配置已导入并保存");
    } catch (e) {
      setError(String(e));
    }
  };
  const saveWake = async () => {
    try {
      const p: Profile = {};
      for (const n of Object.keys(profile)) {
        const v = parsedValues(wakeDrafts[n] || {});
        if (!Object.keys(v).length) throw Error(`${n} 请至少填写一个信号值`);
        p[n] = v;
      }
      const result = await action("/wake/profile", { profile: p });
      if (result) acceptProfile(result);
    } catch (e) {
      setError(String(e));
    }
  };
  const filtered =
    catalog?.messages.filter(m =>
      `${m.name} 0x${m.id.toString(16)}`
        .toLowerCase()
        .includes(query.toLowerCase())
    ) || [];
  const editor = (
    <section
      aria-label="信号配置"
      className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
    >
      <div className="flex h-10 shrink-0 items-center justify-between gap-3 border-b border-border bg-muted/50 px-3 text-sm">
        <strong className="truncate">{message?.name || "信号配置"}</strong>
        <span className="shrink-0 text-xs text-muted-foreground">
          {message
            ? `${message.signals.length} 个信号 · ${message.length} 字节`
            : "点击上方已选报文"}
        </span>
      </div>
      <div
        key={message?.name}
        className="min-h-0 flex-1 overflow-auto overscroll-contain"
      >
        {!message ? (
          <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
            从左侧添加报文，再点击已选报文编辑信号
          </div>
        ) : (
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="sticky top-0 z-10 bg-muted">
              <tr>
                {["信号", "设置值", "当前发送值", "范围 / 说明", "趋势"].map(
                  x => (
                    <th
                      className="border-b border-border px-3 py-2 font-medium text-muted-foreground"
                      key={x}
                    >
                      {x}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {message.signals.map(sig => {
                const task = status.tasks.find(
                  t => t.name === name && t.channel === channel && t.active
                );
                return (
                  <tr
                    key={sig.name}
                    className="border-b border-border/60 hover:bg-muted/60"
                  >
                    <td
                      className="max-w-52 truncate px-3 py-2 font-mono"
                      title={sig.name}
                    >
                      {sig.name}
                    </td>
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1.5">
                        {sig.choices.length > 0 && (
                          <select
                            aria-label={`${sig.name} 枚举`}
                            className={`${field} w-44`}
                            value={
                              values[sig.name]?.startsWith("raw:")
                                ? values[sig.name]
                                : ""
                            }
                            onChange={e =>
                              setValues(v => ({
                                ...v,
                                [sig.name]: e.target.value,
                              }))
                            }
                          >
                            <option value="">沿用当前值</option>
                            {sig.choices.map(c => (
                              <option key={c.raw} value={`raw:${c.raw}`}>
                                {c.raw} · {c.label}
                              </option>
                            ))}
                          </select>
                        )}
                        <input
                          aria-label={`${sig.name} 物理值`}
                          className={`${field} w-24`}
                          placeholder={
                            sig.choices.length ? "物理值" : "沿用当前值"
                          }
                          value={
                            values[sig.name]?.startsWith("raw:")
                              ? ""
                              : values[sig.name] || ""
                          }
                          onChange={e =>
                            setValues(v => ({
                              ...v,
                              [sig.name]: e.target.value,
                            }))
                          }
                        />
                      </div>
                    </td>
                    <td className="px-3 py-2 font-mono text-primary">
                      {displaySignal(task?.signals[sig.name])}
                    </td>
                    <td
                      className="max-w-60 truncate px-3 py-2 text-muted-foreground"
                      title={[
                        sig.comment,
                        sig.multiplexer &&
                          `复用 ${sig.multiplexer}=${sig.multiplexer_ids?.join(",")}`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    >
                      {sig.minimum ?? "—"} ～ {sig.maximum ?? "—"} {sig.unit}{" "}
                      <span className="ml-2">{sig.comment}</span>
                    </td>
                    <td className="whitespace-nowrap px-2 py-1">
                      <button
                        className={button}
                        aria-label={`绘制 ${sig.name} 趋势`}
                        onClick={() => {
                          setTrendSelection({
                            message: message.name,
                            signal: sig.name,
                          });
                          setSearch({ section: "trend" });
                        }}
                      >
                        绘图
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="shrink-0 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        留空沿用已有值 · 物理值按 DBC 范围和步长校验
        {waking && " · 编辑后点击保存唤醒配置"}
      </div>
    </section>
  );
  return (
    <main className="min-h-dvh bg-background text-foreground dark:bg-transparent">
      <div className="mx-auto max-w-[1760px] space-y-3 px-3 py-4 sm:px-5">
        <header className="flex items-center gap-3">
          <h1 className="text-xl font-semibold">总线控制</h1>
          <span className="text-xs text-muted-foreground">CAN / CAN FD</span>
        </header>
        <section className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-card text-card-foreground px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-3">
            <strong>
              {status.open
                ? `已连接 · 通道 ${status.channels?.join(", ")}`
                : "设备未连接"}
            </strong>
            <span className="text-sm text-muted-foreground">
              {status.open
                ? status.hardware_periodic
                  ? "硬件定时"
                  : "软件发送"
                : "选择 DBC 后连接设备"}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              className={button}
              disabled={status.open || busy}
              onClick={() => void browse()}
            >
              选择 DBC 文件
            </button>
            <span
              className="max-w-[min(43vw,430px)] truncate text-xs text-muted-foreground"
              title={catalog?.path}
            >
              {catalog?.path || "尚未选择 DBC"}
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              aria-label="设备后端"
              className={field}
              value={backend}
              disabled={status.open}
              onChange={e => setBackend(e.target.value)}
            >
              <option value="mac-usb">Mac USB</option>
              <option value="hardware">硬件自动选择</option>
            </select>
            <select
              aria-label="连接通道"
              className={field}
              value={channel}
              disabled={status.open}
              onChange={e => setChannel(Number(e.target.value))}
            >
              <option value={0}>通道 0</option>
              <option value={1}>通道 1</option>
            </select>
            <button
              className={button}
              disabled={(busy && !status.open) || !catalog?.key}
              onClick={() =>
                void action(
                  status.open ? "/close" : "/open",
                  status.open
                    ? {}
                    : { backend, dbc: catalog?.path, channels: [channel] }
                )
              }
            >
              {status.open ? "断开设备" : "打开设备"}
            </button>
          </div>
        </section>
        {(error || status.error) && (
          <p
            role="alert"
            className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-xs text-destructive"
          >
            {error || status.error}
          </p>
        )}
        {(section === "send" || section === "wake") && (
          <div className="grid h-[650px] min-h-0 grid-cols-1 grid-rows-[150px_minmax(0,1fr)] gap-3 md:h-[clamp(390px,calc(100dvh-155px),640px)] md:grid-cols-[clamp(180px,24%,260px)_minmax(0,1fr)] md:grid-rows-[minmax(0,1fr)]">
            <section
              aria-label="全部 DBC 报文"
              className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
            >
              <div className="flex h-10 shrink-0 items-center justify-between border-b border-border bg-muted/50 px-3 text-sm">
                <strong>全部报文</strong>
                <span className="text-xs text-muted-foreground">
                  {filtered.length} / {catalog?.message_count || 0}
                </span>
              </div>
              <div className="shrink-0 border-b border-border p-2">
                <input
                  aria-label="搜索报文"
                  className={`${field} w-full`}
                  placeholder="搜索报文名 / ID"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                />
              </div>
              <div className="min-h-0 flex-1 overflow-auto overscroll-contain">
                {!catalog?.key && (
                  <p className="p-4 text-xs text-muted-foreground">
                    请先选择 DBC 文件
                  </p>
                )}
                {filtered.map(m => (
                  <button
                    key={m.name}
                    title={`${m.name} · 点击添加到配置`}
                    className={`flex w-full items-center gap-2 border-b border-border/40 px-3 py-2 text-left text-xs ${name === m.name ? "bg-accent text-accent-foreground" : "hover:bg-muted/60"}`}
                    onClick={() => selectMessage(m)}
                  >
                    <span className="w-12 shrink-0 font-mono text-muted-foreground">
                      0x{m.id.toString(16).toUpperCase().padStart(3, "0")}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{m.name}</span>
                    <span
                      className={
                        selectedNames.includes(m.name)
                          ? "text-primary"
                          : "text-muted-foreground"
                      }
                    >
                      {selectedNames.includes(m.name) ? "✓" : "＋"}
                    </span>
                  </button>
                ))}
              </div>
              <div className="shrink-0 border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
                点击报文添加到右侧配置
              </div>
            </section>
            <div className="grid min-h-0 min-w-0 grid-rows-[minmax(155px,0.85fr)_minmax(180px,1.15fr)] gap-3">
              <section
                aria-label="已选报文配置"
                className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
              >
                <div className="flex min-h-10 shrink-0 flex-wrap items-center justify-between gap-x-2 gap-y-1 border-b border-border bg-muted/50 px-3 py-1">
                  <strong className="text-sm">
                    {waking ? "唤醒报文配置" : "已选报文配置"}{" "}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      {selectedNames.length}
                    </span>
                  </strong>
                  {waking ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <label className={`${button} cursor-pointer`}>
                        导入配置
                        <input
                          aria-label="导入唤醒配置"
                          type="file"
                          accept=".ini,.json"
                          className="hidden"
                          disabled={status.wake_active}
                          onChange={e => {
                            const f = e.target.files?.[0];
                            if (f) void importWake(f);
                            e.target.value = "";
                          }}
                        />
                      </label>
                      <button
                        className={button}
                        disabled={busy || status.wake_active}
                        onClick={() => void saveWake()}
                      >
                        保存配置
                      </button>
                      <button
                        className={button}
                        disabled={busy || !status.open}
                        onClick={() =>
                          void action(
                            status.wake_active ? "/wake/stop" : "/wake/start",
                            { channel }
                          )
                        }
                      >
                        {status.wake_active ? "取消唤醒" : "唤醒车机"}
                      </button>
                    </div>
                  ) : (
                    <label className="text-xs text-muted-foreground">
                      <input
                        type="checkbox"
                        checked={stopAfter}
                        onChange={e => setStopAfter(e.target.checked)}
                      />{" "}
                      时间触发后停发
                    </label>
                  )}
                </div>
                <div className="min-h-0 flex-1 overflow-auto overscroll-contain">
                  {!selectedNames.length ? (
                    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                      从左侧选择需要配置的报文
                    </div>
                  ) : (
                    <table className="w-full min-w-[650px] text-left text-xs">
                      <thead className="sticky top-0 z-10 bg-muted">
                        <tr>
                          {(waking
                            ? ["报文名", "ID", "已设信号", "状态", "操作"]
                            : [
                                "报文名",
                                "ID",
                                "格式",
                                "周期 ms",
                                "次数",
                                "操作",
                              ]
                          ).map(x => (
                            <th
                              className="border-b border-border px-2 py-2 font-medium text-muted-foreground"
                              key={x}
                            >
                              {x}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {selectedNames.map(n => {
                          const m = catalog?.messages.find(x => x.name === n);
                          if (!m) return null;
                          const d = drafts[n] || defaultDraft(m);
                          const task = status.tasks.find(
                            t =>
                              t.name === n && t.channel === channel && t.active
                          );
                          return (
                            <tr
                              key={n}
                              className={`cursor-pointer border-b border-border/60 ${name === n ? "bg-accent text-accent-foreground" : "hover:bg-muted/60"}`}
                              onClick={() => setName(n)}
                            >
                              <td
                                className="max-w-56 truncate px-2 py-2 font-medium"
                                title={n}
                              >
                                <button
                                  className="text-left"
                                  onClick={() => setName(n)}
                                >
                                  {n}
                                </button>
                              </td>
                              <td className="px-2 font-mono text-muted-foreground">
                                0x{m.id.toString(16).toUpperCase()}
                              </td>
                              {waking ? (
                                <>
                                  <td className="px-2">
                                    {
                                      Object.values(wakeDrafts[n] || {}).filter(
                                        v => v !== ""
                                      ).length
                                    }{" "}
                                    个
                                  </td>
                                  <td className="px-2">
                                    {task ? "发送中" : "已配置"}
                                  </td>
                                </>
                              ) : (
                                <>
                                  <td className="px-2">
                                    <select
                                      aria-label={`${n} 帧格式`}
                                      className={`${field} w-24`}
                                      value={
                                        d.fd ? (d.brs ? "brs" : "fd") : "can"
                                      }
                                      onChange={e =>
                                        updateDraft(n, {
                                          fd: e.target.value !== "can",
                                          brs: e.target.value === "brs",
                                        })
                                      }
                                    >
                                      <option value="can">CAN</option>
                                      <option value="fd">CAN FD</option>
                                      <option value="brs">FD+BRS</option>
                                    </select>
                                  </td>
                                  <td className="px-2">
                                    <input
                                      aria-label={`${n} 周期`}
                                      type="number"
                                      min={1}
                                      className={`${field} w-20`}
                                      value={d.period}
                                      onChange={e =>
                                        updateDraft(n, {
                                          period: Number(e.target.value),
                                        })
                                      }
                                    />
                                  </td>
                                  <td className="px-2">
                                    <input
                                      aria-label={`${n} 发送次数`}
                                      title="-1 为持续发送"
                                      type="number"
                                      className={`${field} w-16`}
                                      value={d.count}
                                      onChange={e =>
                                        updateDraft(n, {
                                          count: Number(e.target.value),
                                        })
                                      }
                                    />
                                  </td>
                                </>
                              )}
                              <td className="whitespace-nowrap px-2 py-1.5">
                                <div className="flex gap-1">
                                  {!waking && (
                                    <>
                                      <button
                                        className={button}
                                        disabled={!status.open}
                                        onClick={e => {
                                          e.stopPropagation();
                                          void sendDraft(n, "/send");
                                        }}
                                      >
                                        {task ? "更新" : "发送"}
                                      </button>
                                      <button
                                        className={button}
                                        disabled={!status.open}
                                        onClick={e => {
                                          e.stopPropagation();
                                          void sendDraft(n, "/pulse");
                                        }}
                                      >
                                        脉冲
                                      </button>
                                      <button
                                        className={button}
                                        disabled={!status.open}
                                        onClick={e => {
                                          e.stopPropagation();
                                          void sendDraft(n, "/trigger");
                                        }}
                                      >
                                        触发
                                      </button>
                                      {task && (
                                        <button
                                          className={button}
                                          onClick={e => {
                                            e.stopPropagation();
                                            void sendDraft(n, "/stop");
                                          }}
                                        >
                                          停止
                                        </button>
                                      )}
                                    </>
                                  )}
                                  <button
                                    aria-label={`移除 ${n}`}
                                    className="rounded px-2 text-muted-foreground hover:text-destructive disabled:opacity-40"
                                    disabled={
                                      !!task || (waking && status.wake_active)
                                    }
                                    title={
                                      task ? "先停止发送再移除" : "移除配置"
                                    }
                                    onClick={e => {
                                      e.stopPropagation();
                                      removeMessage(n);
                                    }}
                                  >
                                    ×
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
                <div className="shrink-0 truncate border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
                  {waking
                    ? "按 DBC 单独保存 · 点击报文在下方编辑 · 手动发送可接管唤醒任务"
                    : "每条报文独立保留信号、周期及次数 · -1 持续 · 脉冲为 N 帧触发值 + N 帧复位值"}
                </div>
              </section>
              {editor}
            </div>
          </div>
        )}
        {section === "trend" && (
          <CanSignalTrend
            messages={catalog?.messages ?? []}
            catalogKey={catalog?.key ?? ""}
            selection={trendSelection}
            onSelectionChange={setTrendSelection}
            channels={status.channels ?? []}
            defaultChannel={channel}
          />
        )}
        {section === "tasks" && (
          <section className="max-h-[520px] space-y-2 overflow-auto">
            {!status.tasks.length && <p>暂无发送任务</p>}
            {status.tasks.map(t => (
              <div
                key={`${t.channel}:${t.name}`}
                className="flex flex-wrap items-center gap-4 rounded-lg border border-border bg-card p-3 text-card-foreground"
              >
                <strong>{t.name}</strong>
                <span>
                  通道 {t.channel} · {t.active ? "发送中" : "已停止"}
                </span>
                <span className="text-xs text-muted-foreground">
                  {Object.keys(t.signals).length} 个信号
                </span>
                {t.error && <span className="text-destructive">{t.error}</span>}
                <button
                  className={`${button} ml-auto`}
                  onClick={() =>
                    void action("/stop", { channel: t.channel, name: t.name })
                  }
                >
                  停止
                </button>
              </div>
            ))}
          </section>
        )}
        {section === "receive" && (
          <section className="max-h-[520px] overflow-auto rounded-lg border border-border bg-card text-card-foreground">
            <table className="w-full text-left text-sm">
              <thead className="sticky top-0 bg-muted">
                <tr>
                  {["序号", "时间", "通道", "方向", "ID", "数据"].map(x => (
                    <th className="p-3" key={x}>
                      {x}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {frames.map(f => (
                  <tr
                    key={f.sequence}
                    className="border-t border-border font-mono"
                  >
                    <td className="p-2">{f.sequence}</td>
                    <td>{new Date(f.timestamp * 1000).toLocaleTimeString()}</td>
                    <td>{f.channel}</td>
                    <td>{f.is_rx ? "RX" : "TX"}</td>
                    <td>{f.arbitration_id.toString(16).toUpperCase()}</td>
                    <td>
                      {f.data
                        .map(v => v.toString(16).padStart(2, "0"))
                        .join(" ")
                        .toUpperCase()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
        {section === "record" && (
          <section className="space-y-4 rounded-lg border border-border bg-card p-5 text-card-foreground">
            <p>{status.recording ? "正在录制 BLF" : "未录制"}</p>
            <input
              className={field}
              placeholder="文件名（可留空）"
              value={record}
              onChange={e => setRecord(e.target.value)}
            />
            <div>
              <button
                className={button}
                disabled={busy || !status.open}
                onClick={() =>
                  void action(
                    status.recording ? "/record/stop" : "/record/start",
                    { name: record }
                  )
                }
              >
                {status.recording ? "停止录制" : "开始录制"}
              </button>
            </div>
            <p className="text-sm text-muted-foreground">
              录制从点击开始时起保存；不包含故障前历史帧。
            </p>
          </section>
        )}
      </div>
      {directory && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6">
          <section
            role="dialog"
            aria-modal="true"
            aria-label="选择 DBC 文件"
            className="flex max-h-[85vh] w-full max-w-3xl flex-col gap-4 rounded-xl border border-border bg-popover p-5 text-popover-foreground"
          >
            <div className="flex justify-between">
              <h2 className="text-xl font-semibold">选择 DBC 文件</h2>
              <button className={button} onClick={() => setDirectory(null)}>
                关闭
              </button>
            </div>
            <p className="text-sm text-muted-foreground">
              默认从项目文件夹开始。进入文件夹后点击 .dbc 文件即可解析。
            </p>
            <form
              className="flex gap-2"
              onSubmit={e => {
                e.preventDefault();
                void browse(folderInput);
              }}
            >
              <input
                aria-label="文件夹路径"
                className={`${field} min-w-0 flex-1`}
                value={folderInput}
                onChange={e => setFolderInput(e.target.value)}
              />
              <button className={button}>前往</button>
            </form>
            <div className="overflow-auto">
              <button
                className="block w-full p-2 text-left hover:bg-muted"
                onClick={() => void browse(directory.parent)}
              >
                📁 上一级
              </button>
              {directory.entries.map(e => (
                <button
                  key={e.path}
                  className="block w-full p-2 text-left hover:bg-muted"
                  onClick={() =>
                    e.directory ? void browse(e.path) : void load(e.path)
                  }
                >
                  {e.directory ? "📁" : "📄"} {e.name}
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
