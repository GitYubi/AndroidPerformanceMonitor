import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { UiProjectOptions } from "@/components/UiImageLibrary";
import { BACKEND_URL } from "@/lib/backend";
import type { UiProject } from "@/lib/uiImages";
import { toast } from "sonner";

type SavedCase = {
  id: string;
  name: string;
  version: number;
  projectId?: string;
  parameters: { name: string; secret: boolean; default: string }[];
};
type Batch = {
  id: string;
  caseName: string;
  caseVersion: number;
  serial: string;
  requested: number;
  completed: number;
  counts: Record<string, number>;
  state: string;
  createdAt: number;
  message?: string;
};
type Round = {
  number: number;
  state: string;
  durationMs: number;
  message: string;
  evidence?: boolean;
  failures: {
    step: string;
    message: string;
    state?: string;
    iterations?: number[];
    screenshot?: number;
  }[];
};
type Detail = { batch: Batch; total: number; items: Round[] };
const base = `${BACKEND_URL}/api/ui/stress`;
const field =
  "h-9 w-full min-w-0 rounded-md border border-input bg-background px-2";
const label = "grid gap-1 text-sm";
const states: Record<string, string> = {
  running: "运行中",
  stopping: "停止中",
  completed: "已完成",
  passed: "通过",
  failed: "失败",
  error: "异常",
  interrupted: "已中断",
  cancelled: "已停止",
  needs_review: "待确认",
};
const active = (batch: Batch) => ["running", "stopping"].includes(batch.state);
const pageSize = 25;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(base + path, options);
  const data = await response.json();
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "请求失败，请检查用例和执行参数"
    );
  return data;
}

export function UiStressPanel({
  projects,
  projectId,
  onProjectChange,
  cases,
  devices,
}: {
  projects: UiProject[];
  projectId: string;
  onProjectChange: (id: string) => void;
  cases: SavedCase[];
  devices: { serial: string; state: string; model?: string }[];
}) {
  const [caseId, setCaseId] = useState("");
  const [serial, setSerial] = useState("");
  const [repetitions, setRepetitions] = useState("500");
  const [stopOnFailure, setStopOnFailure] = useState(false);
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Batch[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [offset, setOffset] = useState(0);
  const [failures, setFailures] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const available = cases.filter(
    item => (item.projectId ?? "default") === projectId
  );
  const test = available.find(item => item.id === caseId);
  const current = detail?.batch.id === selected ? detail : null;
  const count = Number(repetitions);
  const validCount = Number.isInteger(count) && count >= 1 && count <= 2000;

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const options = { signal: controller.signal };
        const query = `?offset=${offset}&limit=${pageSize}&failures=${failures}`;
        const [items, next] = await Promise.all([
          request<Batch[]>(
            `?projectId=${encodeURIComponent(projectId)}`,
            options
          ),
          selected
            ? request<Detail>(`/${selected}${query}`, options)
            : Promise.resolve(null),
        ]);
        if (controller.signal.aborted) return;
        setHistory(items);
        setDetail(next);
      } catch (e) {
        if (!controller.signal.aborted) setError((e as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [projectId, selected, failures, offset]);

  function select(id: string) {
    setSelected(id);
    setOffset(0);
    setFailures(false);
    setDetail(null);
  }
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const batch = current?.batch;

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(17rem,1fr)_minmax(0,2fr)]">
      <section className="space-y-4 rounded-lg border bg-card p-4">
        <h2 className="font-semibold">连续执行已保存用例</h2>
        <form
          className="space-y-3"
          onSubmit={event => {
            event.preventDefault();
            if (!test || !validCount || busy) return;
            void action(async () => {
              const run = await request<Batch>("", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  caseId,
                  serial,
                  repetitions: count,
                  stopOnFailure,
                  parameters,
                }),
              });
              setParameters({});
              setHistory(previous => [run, ...previous]);
              select(run.id);
              toast.success("压测已开始，可以切换页面");
            });
          }}
        >
          <label className={label}>
            项目
            <select
              className={field}
              value={projectId}
              disabled={busy}
              onChange={event => {
                onProjectChange(event.target.value);
                setCaseId("");
                setParameters({});
                setHistory([]);
                select("");
              }}
            >
              <UiProjectOptions projects={projects} />
            </select>
          </label>
          <label className={label}>
            用例
            <select
              className={field}
              value={test?.id ?? ""}
              disabled={busy}
              onChange={event => {
                setCaseId(event.target.value);
                setParameters({});
              }}
            >
              <option value="">
                {available.length ? "选择用例" : "当前项目暂无已保存用例"}
              </option>
              {available.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name} · v{item.version}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            目标设备
            <select
              className={field}
              value={serial}
              disabled={busy}
              onChange={event => setSerial(event.target.value)}
            >
              <option value="">无设备（仅本机用例）</option>
              {devices.map(device => (
                <option
                  key={device.serial}
                  value={device.serial}
                  disabled={device.state !== "device"}
                >
                  {device.model || device.serial} · {device.serial}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            执行次数（1–2000）
            <Input
              type="number"
              min={1}
              max={2000}
              step={1}
              required
              disabled={busy}
              value={repetitions}
              onChange={event => setRepetitions(event.target.value)}
            />
          </label>
          {test?.parameters.map(parameter => (
            <label key={parameter.name} className={label}>
              {parameter.name}
              <Input
                type={parameter.secret ? "password" : "text"}
                required={parameter.secret}
                disabled={busy}
                maxLength={8000}
                autoComplete="off"
                value={parameters[parameter.name] ?? parameter.default}
                onChange={event =>
                  setParameters(previous => ({
                    ...previous,
                    [parameter.name]: event.target.value,
                  }))
                }
              />
            </label>
          ))}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={stopOnFailure}
              disabled={busy}
              onChange={event => setStopOnFailure(event.target.checked)}
            />
            断言失败后停止
          </label>
          <p className="text-xs text-muted-foreground">
            逐轮执行，沿用车机当前状态。执行异常会停止后续轮次。仅失败或异常轮次保留可获取的截图。
          </p>
          <Button type="submit" disabled={!test || !validCount || busy}>
            开始压测
          </Button>
        </form>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <div className="space-y-1.5 border-t pt-3">
          <h3 className="text-sm font-medium">项目压测记录</h3>
          {history.length === 0 && (
            <p className="text-sm text-muted-foreground">暂无记录</p>
          )}
          <div className="max-h-64 space-y-1 overflow-auto">
            {history.map(item => (
              <button
                key={item.id}
                type="button"
                aria-pressed={item.id === selected}
                onClick={() => select(item.id)}
                className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-muted aria-pressed:bg-accent"
              >
                <span className="block font-medium">
                  {item.caseName} · {states[item.state] ?? item.state}
                </span>
                <span className="text-xs text-muted-foreground">
                  {new Date(item.createdAt).toLocaleString()} · {item.completed}{" "}
                  / {item.requested}
                </span>
              </button>
            ))}
          </div>
        </div>
      </section>
      <section className="space-y-4 rounded-lg border bg-card p-4">
        {!batch ? (
          <p className="p-5 text-sm text-muted-foreground">
            选择任务查看进度和每轮结果
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="font-semibold">
                  {batch.caseName} · v{batch.caseVersion}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {batch.serial || "本机执行"} ·{" "}
                  {states[batch.state] ?? batch.state}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {active(batch) ? (
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={busy || batch.state === "stopping"}
                    onClick={() =>
                      void action(async () => {
                        const next = await request<Batch>(`/${batch.id}/stop`, {
                          method: "POST",
                        });
                        setDetail(previous =>
                          previous?.batch.id === next.id
                            ? { ...previous, batch: next }
                            : previous
                        );
                      })
                    }
                  >
                    停止压测
                  </Button>
                ) : (
                  <>
                    <Button asChild variant="outline" size="sm">
                      <a href={`${base}/${batch.id}/report`}>下载汇总 JSON</a>
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        if (!window.confirm("删除此压测和全部轮次记录？"))
                          return;
                        void action(async () => {
                          const result = await request<{
                            cleanupErrors: string[];
                          }>(`/${batch.id}/delete`, { method: "POST" });
                          setHistory(previous =>
                            previous.filter(item => item.id !== batch.id)
                          );
                          select("");
                          if (result.cleanupErrors.length)
                            toast.error("记录已删除，部分截图文件未能清理");
                        });
                      }}
                    >
                      删除记录
                    </Button>
                  </>
                )}
              </div>
            </div>
            <div>
              <p className="mb-1 text-sm">
                {batch.completed} / {batch.requested} 轮
              </p>
              <progress
                aria-label="压测进度"
                className="h-2 w-full accent-primary"
                value={batch.completed}
                max={batch.requested}
              />
            </div>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              {Object.entries(batch.counts).map(([state, amount]) => (
                <div key={state} className="flex gap-1">
                  <dt className="text-muted-foreground">
                    {states[state] ?? state}
                  </dt>
                  <dd className="font-medium">{amount}</dd>
                </div>
              ))}
            </dl>
            {batch.message && (
              <p className="text-sm text-muted-foreground">{batch.message}</p>
            )}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={failures}
                onChange={event => {
                  setFailures(event.target.checked);
                  setOffset(0);
                }}
              />
              仅看失败与异常
            </label>
            <div className="overflow-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b text-muted-foreground">
                  <tr>
                    <th className="p-2">轮次</th>
                    <th className="p-2">结果</th>
                    <th className="p-2">耗时</th>
                    <th className="p-2">失败说明</th>
                  </tr>
                </thead>
                <tbody>
                  {current?.items.map(row => (
                    <tr key={row.number} className="border-b align-top">
                      <td className="p-2">{row.number}</td>
                      <td className="p-2">{states[row.state] ?? row.state}</td>
                      <td className="whitespace-nowrap p-2">
                        {(row.durationMs / 1000).toFixed(2)} s
                      </td>
                      <td className="min-w-32 break-words p-2">
                        {row.message && <div>{row.message}</div>}
                        {row.failures.map((failure, i) => (
                          <div key={i}>
                            {failure.step}
                            {failure.iterations?.length
                              ? `（循环 ${failure.iterations.join(" / ")}）`
                              : ""}
                            {failure.state === "error" ? " · 异常" : ""}
                            {failure.message && `：${failure.message}`}
                            {failure.screenshot && (
                              <a
                                className="ml-2 text-primary underline"
                                target="_blank"
                                rel="noreferrer"
                                href={`${base}/${batch.id}/rounds/${row.number}/screenshot?step=${failure.screenshot}`}
                              >
                                查看步骤截图
                              </a>
                            )}
                          </div>
                        ))}
                        {row.evidence && (
                          <a
                            className="text-primary underline"
                            target="_blank"
                            rel="noreferrer"
                            href={`${base}/${batch.id}/rounds/${row.number}/screenshot`}
                          >
                            查看旧版轮次截图
                          </a>
                        )}
                        {!row.message &&
                          !row.failures.length &&
                          !row.evidence &&
                          "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {current?.items.length === 0 && (
                <p className="p-2 text-sm text-muted-foreground">
                  暂无符合条件的轮次
                </p>
              )}
            </div>
            <div className="flex items-center justify-between gap-2 text-sm">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(offset - pageSize)}
              >
                上一页
              </Button>
              <span>共 {current?.total ?? 0} 轮</span>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + pageSize >= (current?.total ?? 0)}
                onClick={() => setOffset(offset + pageSize)}
              >
                下一页
              </Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
