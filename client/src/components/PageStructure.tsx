import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
const API = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8090";
async function request(path: string, body?: unknown) {
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
    throw new Error(typeof data.detail === "string" ? data.detail : "请求失败");
  return data;
}
type Node = {
  index: number;
  parent: number | null;
  children: number[];
  label: string;
  className: string;
  resourceId: string;
  package: string;
  clickable: boolean;
  checkable: boolean;
  checked: boolean;
  enabled: boolean;
  bounds: number[] | null;
};
type Tree = { nodes: Node[]; fingerprint: string };
type Scan = { id: string; kind: string; state: string; createdAt: number };
type Event = {
  seq: number;
  type: string;
  fingerprint?: string;
  from?: string;
  to?: string;
  depth?: number;
  artifact?: string;
  label?: string;
  state: string;
  message?: string;
  nodeCount?: number;
  pages?: number;
  actions?: number;
};
const running = (s?: Scan | null) =>
  !!s && ["queued", "running"].includes(s.state);
const stateName: Record<string, string> = {
  queued: "排队中",
  running: "扫描中",
  passed: "扫描完成",
  needs_review: "扫描结束，有未覆盖区域",
  error: "扫描异常",
  cancelled: "已取消",
  interrupted: "已中断",
};
function Control({ node, tree }: { node: Node; tree: Tree }) {
  return (
    <details className="ml-3 border-l pl-3" open={node.parent === null}>
      <summary className="cursor-pointer py-1 text-sm break-all">
        {node.label || node.className.split(".").at(-1)}{" "}
        <span className="text-muted-foreground">
          {node.clickable ? " · 可点击" : ""}
          {node.checkable ? ` · ${node.checked ? "开启" : "关闭"}` : ""}
          {!node.enabled ? " · 禁用" : ""}
        </span>
      </summary>
      <div className="pb-2 text-xs text-muted-foreground break-all">
        {node.className}
        <br />
        {node.resourceId || "无 resource-id"}
        <br />
        {node.package} · {JSON.stringify(node.bounds)}
      </div>
      {node.children.map(i => (
        <Control key={i} node={tree.nodes[i]} tree={tree} />
      ))}
    </details>
  );
}
export default function PageStructure() {
  const [devices, setDevices] = useState<{ serial: string; state: string }[]>(
      []
    ),
    [serial, setSerial] = useState("");
  const [packages, setPackages] = useState("com.android.settings"),
    [labels, setLabels] = useState("");
  const [depth, setDepth] = useState(2),
    [pages, setPages] = useState(10),
    [actions, setActions] = useState(20),
    [openSettings, setOpenSettings] = useState(true);
  const [history, setHistory] = useState<Scan[]>([]),
    [scan, setScan] = useState<Scan | null>(null),
    [events, setEvents] = useState<Event[]>([]),
    [tree, setTree] = useState<Tree | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      const [ds, rs] = await Promise.all([
        request("/devices"),
        request("/ui/runs"),
      ]);
      setDevices(ds);
      setHistory(rs.filter((r: Scan) => r.kind === "scan"));
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  useEffect(() => {
    if (!scan) return;
    let alive = true;
    const poll = async () => {
      try {
        const [run, ev] = await Promise.all([
          request(`/ui/runs/${scan.id}`),
          request(`/ui/runs/${scan.id}/events`),
        ]);
        if (alive) {
          setScan(run);
          setEvents(ev);
          setHistory(previous =>
            previous.map(s => (s.id === run.id ? run : s))
          );
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    void poll();
    const timer = running(scan) ? setInterval(poll, 2000) : undefined;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [scan?.id, scan?.state]);
  useEffect(() => {
    setTree(null);
  }, [scan?.id]);
  async function start() {
    setBusy(true);
    setError("");
    try {
      const s = await request("/ui/scans", {
        serial,
        openSettings,
        scope: {
          allowedPackages: packages
            .split(",")
            .map(s => s.trim())
            .filter(Boolean),
          allowedNavigation: labels
            .split(",")
            .map(s => s.trim())
            .filter(Boolean),
          autoNavigation: !labels.trim(),
          maxDepth: depth,
          maxPages: pages,
          maxActions: actions,
        },
      });
      setEvents([]);
      setScan(s);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const pageEvents = events.filter(e => e.type === "graph-page");
  const coverage = events.filter(
    e =>
      e.type === "coverage" ||
      e.type === "coverage-summary" ||
      (e.type === "graph-return" && e.state === "needs_review") ||
      (e.type === "graph-edge" && e.state === "skipped")
  );
  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold">测试前 · 页面结构扫描</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          通过 USB / ADB 读取当前窗口控件树，按菜单结构逐页发现并返回。无需模型
          API；不切换开关。自绘控件、滚动区域和预算外页面会留下覆盖缺口。
        </p>
      </div>
      <div className="grid gap-4 rounded-xl border p-5 md:grid-cols-3">
        <label className="text-sm">
          设备
          <select
            aria-label="扫描设备"
            className="mt-1 w-full rounded border p-2"
            value={serial}
            onChange={e => setSerial(e.target.value)}
          >
            <option value="">选择设备</option>
            {devices.map(d => (
              <option key={d.serial} disabled={d.state !== "device"}>
                {d.serial}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          允许包名（逗号分隔）
          <Input value={packages} onChange={e => setPackages(e.target.value)} />
        </label>
        <label className="text-sm">
          限定菜单名称（留空自动识别菜单行）
          <Input
            value={labels}
            onChange={e => setLabels(e.target.value)}
            placeholder="显示, 声音"
          />
        </label>
        <label className="text-sm">
          最大深度
          <Input
            type="number"
            min={0}
            max={5}
            value={depth}
            onChange={e => setDepth(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          最多页面
          <Input
            type="number"
            min={1}
            max={50}
            value={pages}
            onChange={e => setPages(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          动作预算（含返回）
          <Input
            type="number"
            min={2}
            max={100}
            value={actions}
            onChange={e => setActions(Number(e.target.value))}
          />
        </label>
        <label className="text-sm flex items-center gap-2">
          <input
            type="checkbox"
            checked={openSettings}
            onChange={e => setOpenSettings(e.target.checked)}
          />
          先打开系统设置（关闭则扫描当前页）
        </label>
        <div className="flex gap-2">
          <Button disabled={!serial || busy || running(scan)} onClick={start}>
            开始扫描
          </Button>
          <Button variant="outline" onClick={refresh}>
            刷新设备和记录
          </Button>
          {running(scan) && (
            <Button
              variant="outline"
              onClick={async () => {
                try {
                  setScan(await request(`/ui/runs/${scan!.id}/cancel`, {}));
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              停止
            </Button>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <label className="block text-sm">
        扫描记录
        <select
          aria-label="扫描记录"
          className="ml-3 rounded border p-2"
          value={scan?.id || ""}
          onChange={e => {
            const s = history.find(s => s.id === e.target.value);
            setEvents([]);
            setScan(s || null);
          }}
        >
          <option value="">选择扫描记录</option>
          {history.map(s => (
            <option key={s.id} value={s.id}>
              {new Date(s.createdAt).toLocaleString()} ·{" "}
              {stateName[s.state] || s.state}
            </option>
          ))}
        </select>
      </label>
      {scan && (
        <div className="text-sm">
          {stateName[scan.state] || scan.state} · 已发现 {pageEvents.length} 页{" "}
          <a
            className="ml-3 underline"
            href={`${API}/api/ui/runs/${scan.id}/report`}
          >
            下载结构索引与覆盖记录
          </a>
        </div>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="rounded-xl border p-4">
          <h3 className="mb-3 font-medium">页面导航树</h3>
          {!pageEvents.length && (
            <p className="text-sm text-muted-foreground">
              扫描后显示页面；点击页面查看控件树。
            </p>
          )}
          {pageEvents.map(p => {
            const edge = events.find(
              e => e.type === "graph-edge" && e.to === p.fingerprint
            );
            return (
              <div
                key={p.seq}
                style={{ paddingLeft: (p.depth || 0) * 20 }}
                className="mb-2"
              >
                <button
                  className="rounded border px-3 py-2 text-left text-sm hover:bg-muted"
                  onClick={async () => {
                    try {
                      if (p.artifact)
                        setTree(
                          await request(
                            `/ui/runs/${scan!.id}/artifacts/${p.artifact}`
                          )
                        );
                    } catch (e) {
                      setError(String(e));
                    }
                  }}
                >
                  {p.depth ? "└ " : ""}
                  {edge?.label || "起始页"} · {p.nodeCount} 个控件
                  <br />
                  <span className="text-xs text-muted-foreground">
                    {p.fingerprint}
                  </span>
                </button>
              </div>
            );
          })}
          {events.filter(e => e.type === "graph-edge" && e.state === "entered")
            .length > 0 && (
            <details className="mt-4 text-xs">
              <summary>页面跳转关系（含重复页面）</summary>
              {events
                .filter(e => e.type === "graph-edge")
                .map(e => (
                  <div key={e.seq} className="mt-2 break-all">
                    {e.label}: {e.from} → {e.to || "未访问"} · {e.state}
                  </div>
                ))}
            </details>
          )}
        </div>
        <div className="min-w-0 rounded-xl border p-4">
          <h3 className="mb-3 font-medium">当前选择页面的控件树</h3>
          {tree ? (
            <>
              <a
                className="text-xs underline"
                href={`${API}/api/ui/runs/${scan!.id}/artifacts/tree-${tree.fingerprint}.json`}
                target="_blank"
                rel="noreferrer"
              >
                打开完整控件 JSON
              </a>
              <div className="mt-3 max-h-[650px] overflow-auto">
                {tree.nodes
                  .filter(n => n.parent === null)
                  .map(n => (
                    <Control key={n.index} node={n} tree={tree} />
                  ))}
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              在左侧选择一个页面。
            </p>
          )}
        </div>
      </div>
      {!!coverage.length && (
        <div className="rounded-xl border p-4">
          <h3 className="font-medium">覆盖情况</h3>
          {coverage.map(e => (
            <p key={e.seq} className="mt-2 text-sm">
              {e.label ? `${e.label}：` : ""}
              {e.message}
              {e.pages !== undefined
                ? ` · ${e.pages} 页 / ${e.actions} 次动作`
                : ""}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
