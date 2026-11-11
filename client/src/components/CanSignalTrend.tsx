import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BACKEND_URL } from "@/lib/backend";

const SAMPLE_LIMIT = 600;
const control =
  "h-8 min-w-0 rounded-md border border-border bg-background px-2 text-xs";
const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hour12: false,
});
const formatTime = (seconds: number) => timeFormat.format(seconds * 1000);

type Message = {
  name: string;
  id: number;
  signals: { name: string; unit: string | null }[];
};
type Point = { timestamp: number; value: number };
type Series = { session: string; latest: number; points: Point[] };
export type SignalSelection = { message: string; signal: string };

function SignalPlot({
  message,
  signal,
  unit,
  channel,
  connected,
}: SignalSelection & {
  unit: string | null;
  channel: number;
  connected: boolean;
}) {
  const [points, setPoints] = useState<Point[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!connected) return;
    const controller = new AbortController();
    let timer: number;
    let after = 0;
    let session = "";

    async function poll() {
      const query = new URLSearchParams({
        message,
        signal,
        channel: String(channel),
        after: String(after),
        session,
      });
      try {
        const response = await fetch(
          `${BACKEND_URL}/api/can/signal-series?${query}`,
          { signal: controller.signal }
        );
        const data = await response.json();
        if (!response.ok)
          throw new Error(
            response.status === 404
              ? "请重启后端以加载信号趋势功能"
              : data.detail || "读取信号失败"
          );
        if (controller.signal.aborted) return;
        const result = data as Series;
        const reset = session !== result.session;
        session = result.session;
        after = result.latest;
        if (reset || result.points.length) {
          setPoints(previous =>
            [...(reset ? [] : previous), ...result.points].slice(-SAMPLE_LIMIT)
          );
        }
        setError("");
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 500);
      }
    }

    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [channel, connected, message, signal]);

  const latest = points.at(-1);
  const data = [...points].sort((a, b) => a.timestamp - b.timestamp);
  const valueLabel = unit ? `${signal} (${unit})` : signal;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5 text-xs">
        <span className="font-medium">{valueLabel}</span>
        <span className="font-mono text-muted-foreground">
          {latest
            ? `最新值 ${latest.value}${unit ? ` ${unit}` : ""} · ${points.length} 点`
            : "暂无采样"}
        </span>
      </div>
      {error && connected && (
        <p role="alert" className="px-3 py-2 text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="h-[clamp(220px,calc(100dvh-340px),460px)] min-w-0 p-3">
        {data.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={data}
              margin={{ top: 12, right: 24, bottom: 4, left: 4 }}
            >
              <CartesianGrid
                stroke="var(--chart-grid)"
                strokeDasharray="3 5"
                vertical={false}
              />
              <XAxis
                type="number"
                dataKey="timestamp"
                domain={["dataMin", "dataMax"]}
                minTickGap={36}
                tickFormatter={formatTime}
                tick={{ fill: "var(--chart-tick)", fontSize: 10 }}
              />
              <YAxis
                domain={["auto", "auto"]}
                width={62}
                tick={{ fill: "var(--chart-tick)", fontSize: 10 }}
              />
              <Tooltip
                labelFormatter={formatTime}
                contentStyle={{
                  background: "var(--popover)",
                  color: "var(--popover-foreground)",
                  border: "1px solid var(--border)",
                  fontSize: 12,
                }}
              />
              <Line
                type="linear"
                dataKey="value"
                name={valueLabel}
                stroke="var(--chart-fps)"
                strokeWidth={2}
                dot={data.length === 1}
                activeDot={{ r: 4 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div
            className="grid h-full place-items-center text-sm text-muted-foreground"
            role="status"
          >
            {connected ? "等待所选报文的接收帧" : "请先打开所选 CAN 通道"}
          </div>
        )}
      </div>
      <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
        {connected
          ? "实时接收 · 最近 600 个采样点"
          : "通道已断开 · 已采样数据保留在图中"}
      </p>
    </>
  );
}

export default function CanSignalTrend({
  messages,
  catalogKey,
  selection,
  onSelectionChange,
  channels,
  defaultChannel,
}: {
  messages: Message[];
  catalogKey: string;
  selection: SignalSelection;
  onSelectionChange: (selection: SignalSelection) => void;
  channels: number[];
  defaultChannel: number;
}) {
  const [query, setQuery] = useState("");
  const [selectedChannel, setSelectedChannel] = useState(defaultChannel);
  const channel =
    channels.length && !channels.includes(selectedChannel)
      ? channels[0]
      : selectedChannel;
  const message = messages.find(item => item.name === selection.message);
  const signal = message?.signals.find(item => item.name === selection.signal);
  const matches = messages.filter(item =>
    `${item.name} 0x${item.id.toString(16)}`
      .toLowerCase()
      .includes(query.trim().toLowerCase())
  );
  if (message && !matches.includes(message)) matches.unshift(message);

  return (
    <section
      aria-label="信号趋势"
      className="overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
    >
      <div className="flex flex-wrap items-end gap-3 border-b border-border bg-muted/50 p-3">
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs text-muted-foreground">
          搜索报文
          <input
            className={control}
            placeholder="报文名 / ID"
            value={query}
            onChange={event => setQuery(event.target.value)}
          />
        </label>
        <label className="flex min-w-48 flex-[2] flex-col gap-1 text-xs text-muted-foreground">
          报文
          <select
            className={control}
            value={message?.name ?? ""}
            onChange={event =>
              onSelectionChange({ message: event.target.value, signal: "" })
            }
          >
            <option value="">选择报文</option>
            {matches.map(item => (
              <option key={item.name} value={item.name}>
                0x{item.id.toString(16).toUpperCase()} · {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1 text-xs text-muted-foreground">
          信号
          <select
            className={control}
            value={signal?.name ?? ""}
            disabled={!message}
            onChange={event =>
              onSelectionChange({
                message: selection.message,
                signal: event.target.value,
              })
            }
          >
            <option value="">选择信号</option>
            {message?.signals.map(item => (
              <option key={item.name} value={item.name}>
                {item.name}
                {item.unit ? ` (${item.unit})` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          通道
          <select
            className={control}
            value={channel}
            onChange={event => setSelectedChannel(Number(event.target.value))}
          >
            {(channels.length ? channels : [channel]).map(item => (
              <option key={item} value={item}>
                通道 {item}
              </option>
            ))}
          </select>
        </label>
      </div>
      {message && signal ? (
        <SignalPlot
          key={`${catalogKey}:${message.name}:${signal.name}:${channel}`}
          message={message.name}
          signal={signal.name}
          unit={signal.unit}
          channel={channel}
          connected={channels.includes(channel)}
        />
      ) : (
        <p className="grid h-56 place-items-center text-sm text-muted-foreground">
          {messages.length ? "选择报文和信号后显示折线" : "请先选择 DBC 文件"}
        </p>
      )}
    </section>
  );
}
