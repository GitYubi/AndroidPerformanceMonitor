import ScrcpyScreen, { type ScrcpyHandle } from "./ScrcpyScreen";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
const API = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8090";
type Node = {
  index: number;
  parent: number | null;
  children: number[];
  label: string;
  text: string;
  description: string;
  resourceId: string;
  xpath: string;
  className: string;
  package: string;
  bounds: number[] | null;
  clickable: boolean;
  enabled: boolean;
  checkable: boolean;
  checked: boolean;
};
type Observation = {
  id: string;
  serial: string;
  image: string;
  width: number;
  height: number;
  nodes: Node[];
  capturedAt: number;
  signature: string;
  identity?: string;
  geometry: string;
  rotation: string;
};
type App = { id: string; name: string; package: string | null };
type Page = {
  entry?: {
    elementId?: string;
    sourcePageId: string;
    observationId: string;
    nodeIndex: number;
    locator: Element["locator"];
  };
  appId?: string;
  parentPageId?: string | null;
  navigationStatus?: string;
  identity?: string;
  package?: string | null;
  id: string;
  name: string;
  signature: string;
  parentEdgeId: string | null;
};
type Element = {
  validation?: string;
  moveUndo?: unknown;
  id: string;
  name: string;
  pageId: string;
  observationId: string;
  nodeIndex: number;
  checkable: boolean;
  locator: { strategy: string; xpath: string; fragile?: boolean };
};
type Edge = {
  id: string;
  from: string;
  to: string;
  name: string;
  status: string;
};
type Graph = {
  apps: App[];
  pages: Page[];
  elements: Element[];
  edges: Edge[];
  revision: number;
  deleted: unknown[];
};
type Pick = { obs: Observation; node: Node };
type Menu = {
  x: number;
  y: number;
  kind: "screen" | "page" | "element";
  id?: string;
  pick?: Pick;
};
const empty: Graph = {
  apps: [],
  pages: [],
  elements: [],
  edges: [],
  revision: 0,
  deleted: [],
};
async function api(path: string, body: unknown) {
  const r = await fetch(`${API}/api/ui/map/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok)
    throw Error(
      typeof data.detail === "string" ? data.detail : "请求参数或设备响应无效"
    );
  return data;
}
export default function PageMapWorkbench() {
  const [devices, setDevices] = useState<{ serial: string; state: string }[]>(
      []
    ),
    [serial, setSerial] = useState("");
  const [viewParentId, setViewParentId] = useState("");
  const [pendingEntryId, setPendingEntryId] = useState("");
  const [appId, setAppId] = useState("");
  const [pageId, setPageId] = useState("");
  const [dialog, setDialog] = useState<"app" | "page" | "move" | null>(null);
  const [dialogName, setDialogName] = useState("");
  const [parentId, setParentId] = useState("");
  const [moveId, setMoveId] = useState("");
  const [movePageId, setMovePageId] = useState("");
  const [recaptureId, setRecaptureId] = useState("");
  const [allowMissing, setAllowMissing] = useState(false);
  const [draftToken, setDraftToken] = useState("");
  const scrcpy = useRef<ScrcpyHandle>(null);
  const [obs, setObs] = useState<Observation | null>(null),
    [frame, setFrame] = useState<{
      image: string;
      width: number;
      height: number;
    } | null>(null);
  const [graph, setGraph] = useState<Graph>(empty),
    [pick, setPick] = useState<Pick | null>(null),
    [candidates, setCandidates] = useState<Node[]>([]);
  const [busy, setBusy] = useState(false),
    [live, setLive] = useState(false),
    [mode, setMode] = useState("inspect");
  const [status, setStatus] = useState(
      "连接设备后读取当前页面，不会启动任何应用。"
    ),
    [error, setError] = useState(""),
    [name, setName] = useState("");
  const [menu, setMenu] = useState<Menu | null>(null),
    [undo, setUndo] = useState("");
  const inspectionPaused = useRef(false);
  const working = useRef(false),
    serialRef = useRef(serial),
    frameBusy = useRef(false);
  serialRef.current = serial;
  const imageRef = useRef<HTMLImageElement>(null),
    cropRef = useRef<HTMLCanvasElement>(null);
  const refreshDevices = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/devices`);
      if (!r.ok) throw Error("设备列表读取失败");
      setDevices(await r.json());
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);
  useEffect(() => {
    setViewParentId("");
    setPendingEntryId("");
    setAppId("");
    setPageId("");
    setRecaptureId("");
    setDraftToken("");
    setMode("inspect");
    setObs(null);
    setFrame(null);
    setPick(null);
    setCandidates([]);
    setMenu(null);
    setGraph(empty);
    setUndo("");
    setLive(false);
    setError("");
    if (serial)
      api("graph", { serial })
        .then(g => {
          if (serialRef.current === serial) setGraph(g);
        })
        .catch(e => setError(String(e)));
  }, [serial]);
  async function job(fn: () => Promise<void>) {
    if (working.current) return;
    if (frameBusy.current) {
      setStatus("画面正在刷新，请稍后重试。");
      return;
    }
    working.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
      setLive(false);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  function accept(o: Observation) {
    if (o.serial !== serialRef.current) return;
    setCandidates([]);
    setPick(null);
    setObs(o);
    setFrame(o);
  }
  async function read() {
    const o: Observation = await api("observe", { serial });
    accept(o);
    return o;
  }
  useEffect(() => {
    if (!live || !serial || mode !== "inspect") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!working.current && !frameBusy.current) {
        frameBusy.current = true;
        try {
          const f = await api("frame", { serial });
          if (!cancelled && !inspectionPaused.current) setFrame(f);
        } catch (e) {
          if (!cancelled) {
            setError(String(e));
            setLive(false);
          }
        } finally {
          frameBusy.current = false;
        }
      }
      if (!cancelled) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [live, serial, mode]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  useEffect(() => {
    if (!pick || !cropRef.current || !pick.node.bounds) return;
    const canvas = cropRef.current;
    let alive = true;
    const image = new Image();
    image.onload = () => {
      if (!alive) return;
      const [x1, y1, x2, y2] = pick.node.bounds!;
      const x = Math.max(0, x1),
        y = Math.max(0, y1),
        w = Math.max(1, Math.min(image.width, x2) - x),
        h = Math.max(1, Math.min(image.height, y2) - y);
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d")?.drawImage(image, x, y, w, h, 0, 0, w, h);
    };
    image.src = pick.obs.image;
    return () => {
      alive = false;
    };
  }, [pick]);
  function choose(o: Observation, n: Node) {
    setViewParentId("");
    setPick({ obs: o, node: n });
    setName(
      (recaptureId && graph.elements.find(e => e.id === recaptureId)?.name) ||
        n.label ||
        n.resourceId ||
        n.className
    );
  }
  function menuAt(x: number, y: number, m: Omit<Menu, "x" | "y">) {
    setMenu({
      ...m,
      x: Math.max(8, Math.min(x, window.innerWidth - 270)),
      y: Math.max(8, Math.min(y, window.innerHeight - 360)),
    });
  }
  async function screenClick(
    e: React.MouseEvent<HTMLImageElement>,
    right = false
  ) {
    e.preventDefault();
    if (!serial || busy || !obs || mode !== "inspect") return;
    const rect = e.currentTarget.getBoundingClientRect(),
      rx = (e.clientX - rect.left) / rect.width,
      ry = (e.clientY - rect.top) / rect.height,
      mx = e.clientX,
      my = e.clientY;
    setMenu(null);

    const select = async () => {
      const o = obs;
      inspectionPaused.current = true;
      setLive(false);
      setFrame(o);
      const x = rx * o.width,
        y = ry * o.height;
      const list = o.nodes
        .filter(n => {
          const b = n.bounds;
          return (
            b &&
            b[2] > b[0] &&
            b[3] > b[1] &&
            x >= b[0] &&
            x < b[2] &&
            y >= b[1] &&
            y < b[3]
          );
        })
        .sort(
          (a, b) =>
            (a.bounds![2] - a.bounds![0]) * (a.bounds![3] - a.bounds![1]) -
            (b.bounds![2] - b.bounds![0]) * (b.bounds![3] - b.bounds![1])
        );
      setCandidates(list);
      if (!list.length) {
        setPick(null);
        setStatus("该区域没有可见结构节点；不能保存为稳定元素。");
        return;
      }
      const n = list[0];
      choose(o, n);
      if (right) {
        menuAt(mx, my, { kind: "screen", pick: { obs: o, node: n } });
        return;
      }
      setStatus("已选中元素，可切换节点层级或保存到所选父节点。");
    };
    await select();
  }
  async function switchMode(next: string) {
    if (busy || mode === next) return;
    setLive(false);
    setMenu(null);
    setDialog(null);
    if (frameBusy.current) {
      setStatus("正在结束截图刷新，请稍后切换模式。");
      return;
    }
    if (next === "operate") {
      setPick(null);
      setCandidates([]);
      setDraftToken("");
      setMode(next);
      return;
    }
    await job(async () => {
      await scrcpy.current?.stop();
      setMode("inspect");
      await read();
      setStatus(
        "已采集标定快照。请选择所属页面或保存父元素 / 绑定打开结果，再保存元素。"
      );
    });
  }
  async function saveStructure() {
    if (!obs) return;
    await job(async () => {
      const result =
        dialog === "app"
          ? await api("save-app", {
              serial,
              observationId: obs.id,
              name: dialogName,
            })
          : await api("save-page", {
              serial,
              observationId: obs.id,
              appId,
              name: dialogName,
              parentPageId: parentId || null,
              draftToken: draftToken || null,
              allowMissing,
              entryElementId: pendingEntryId || null,
            });
      setGraph(result.graph);
      if (result.appId) {
        setAppId(result.appId);
        setPageId("");
      }
      if (result.pageId) {
        setPageId(result.pageId);
        setRecaptureId("");
        setPendingEntryId("");
      }
      setDialog(null);
      setStatus(
        dialog === "app"
          ? "应用已保存，可以保存根元素。"
          : "父元素已保存，进入后的元素可以挂在它下面。"
      );
    });
  }
  async function moveElement(undoMove = false) {
    await job(async () => {
      setGraph(
        await api("move-element", {
          serial,
          elementId: moveId,
          pageId: movePageId || null,
          undo: undoMove,
        })
      );
      setDialog(null);
      setMenu(null);
      setStatus(
        undoMove
          ? "已撤销移动。"
          : "元素已移动。新归属下验证后才能使用；跨应用需要重新标定。"
      );
    });
  }
  async function save(p: Pick) {
    if (mode !== "inspect" || !appId) {
      setError("请先保存应用，再选择应用根节点或父元素。");
      return;
    }
    await job(async () => {
      const savedGraph: Graph = await api("save", {
        serial,
        observationId: p.obs.id,
        nodeIndex: p.node.index,
        pageId: pageId || null,
        appId,
        elementId: recaptureId || null,
        name,
      });
      setGraph(savedGraph);
      if (!pageId) {
        const saved = savedGraph.elements.find(
          e => e.observationId === p.obs.id && e.nodeIndex === p.node.index
        );
        if (saved) setPageId(saved.pageId);
      }
      setRecaptureId("");
      setStatus("元素已保存到所选父节点。");
      setMenu(null);
    });
  }
  async function execute(id: string, intent: string) {
    setMenu(null);
    if (mode !== "inspect") return;
    await job(async () => {
      let result;
      try {
        result = await api("execute", {
          serial,
          targetId: id,
          intent,
          requestId: crypto.randomUUID(),
        });
      } catch (error) {
        // Launch/navigation may have changed the device even when the target was not reached.
        try {
          await read();
        } catch {
          /* Keep the original action error. */
        }
        throw error;
      }
      accept(result.observation);
      setGraph(result.graph);
      setStatus(result.message);
    });
  }
  async function edit(id: string, action: string, newName = "") {
    if (mode !== "inspect") return;
    setMenu(null);
    await job(async () => {
      setGraph(await api("edit", { serial, id, action, name: newName }));
      if (action === "delete") setUndo(id);
      if (action === "restore") setUndo("");
      setStatus(
        action === "rename"
          ? "名称已保存"
          : action === "delete"
            ? "已删除保存项；未操作设备，可撤销"
            : "已恢复保存项"
      );
    });
  }
  async function selectSaved(el: Element) {
    await job(async () => {
      const o = await api("saved-observation", {
        serial,
        observationId: el.observationId,
        nodeIndex: el.nodeIndex,
      });
      choose(o, o.nodes[el.nodeIndex]);
      setCandidates([]);
      setName(el.name);
      setStatus("查看保存时的检查快照；设备画面和设备状态不变。");
    });
  }
  async function selectParent(p: Page) {
    setAppId(p.appId || "");
    setPageId(p.id);
    setRecaptureId("");
    await job(async () => {
      const result = await api("parent-element", { serial, pageId: p.id });
      if (!result.entry) {
        setPick(null);
        setViewParentId(p.id);
        setStatus(result.message);
        return;
      }
      choose(result.observation, result.node);
      setViewParentId(p.id);
      setCandidates([]);
      setName(p.name);
      setStatus(
        `父元素：${p.name}；入口位于「${result.sourceName}」。下方是入口控件属性，子元素属于进入后的画面。`
      );
    });
  }
  const selectedElement =
    menu?.kind === "element"
      ? graph.elements.find(e => e.id === menu.id)
      : undefined;
  function pageLabel(page: Page) {
    const names = [page.name];
    const seen = new Set([page.id]);
    let parent = graph.pages.find(p => p.id === page.parentPageId);
    while (parent && !seen.has(parent.id)) {
      names.unshift(parent.name);
      seen.add(parent.id);
      parent = graph.pages.find(p => p.id === parent!.parentPageId);
    }
    return names.join(" / ");
  }
  function renderPage(p: Page, seen: Set<string>, depth = 0): React.ReactNode {
    if (seen.has(p.id)) return null;
    const next = new Set(seen).add(p.id);
    return (
      <div key={p.id} style={{ paddingLeft: depth ? 12 : 0 }}>
        {(p.entry || p.parentPageId || p.navigationStatus === "missing") && (
          <button
            className="my-1 w-full rounded px-2 py-2 text-left hover:bg-muted"
            onClick={() => void selectParent(p)}
            onContextMenu={e => {
              e.preventDefault();
              if (mode !== "inspect") return;
              setName(p.name);
              menuAt(e.clientX, e.clientY, { kind: "page", id: p.id });
            }}
          >
            ▾ {p.name}
            {pageId === p.id ? " ✓" : ""}
            {p.navigationStatus === "missing" && (
              <span className="ml-2 text-xs text-amber-400">入口待补充</span>
            )}
          </button>
        )}
        {graph.elements
          .filter(
            e =>
              e.pageId === p.id &&
              !graph.pages.some(parent => parent.entry?.elementId === e.id)
          )
          .map(el => (
            <button
              key={el.id}
              className="block w-full rounded py-2 pl-5 text-left text-sm hover:bg-muted"
              onClick={() => selectSaved(el)}
              onContextMenu={e => {
                e.preventDefault();
                if (mode !== "inspect") return;
                setName(el.name);
                menuAt(e.clientX, e.clientY, { kind: "element", id: el.id });
              }}
            >
              {el.checkable ? "开关" : "元素"} · {el.name}
              {el.validation === "pending" && (
                <span className="ml-2 text-amber-400">待验证</span>
              )}
              {el.validation === "recapture" && (
                <span className="ml-2 text-amber-400">待重新标定</span>
              )}
            </button>
          ))}
        {graph.pages
          .filter(child => child.parentPageId === p.id)
          .map(child => renderPage(child, next, depth + 1))}
      </div>
    );
  }
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold">页面与元素工作台</h2>
        <span className="text-xs text-muted-foreground">
          真实 ADB · 默认屏幕 · 不使用系统功能键
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <select
          aria-label="工作台设备"
          className="rounded border bg-background p-2"
          value={serial}
          disabled={busy || mode === "operate"}
          onChange={e => setSerial(e.target.value)}
        >
          <option value="">选择设备</option>
          {devices.map(d => (
            <option
              key={d.serial}
              value={d.serial}
              disabled={d.state !== "device"}
            >
              {d.serial} · {d.state}
            </option>
          ))}
        </select>
        <Button variant="outline" disabled={busy} onClick={refreshDevices}>
          刷新设备
        </Button>
        <Button
          disabled={!serial || busy || mode !== "inspect"}
          onClick={() =>
            job(async () => {
              await read();
              setStatus("已读取当前页画面与控件，未操作设备。");
            })
          }
        >
          {busy ? "处理中…" : "读取画面与控件"}
        </Button>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={live}
            disabled={!obs || busy || mode !== "inspect"}
            onChange={e => {
              inspectionPaused.current = false;
              setLive(e.target.checked);
            }}
          />
          实时显示（截图刷新）
        </label>
        <label className="flex min-w-0 max-w-full items-center gap-2 text-sm">
          <span className="shrink-0">节点层级</span>
          <select
            className="w-64 min-w-0 max-w-full rounded border bg-background p-2 disabled:opacity-50"
            disabled={
              busy || !pick || obs?.id !== pick.obs.id || !candidates.length
            }
            value={
              pick && candidates.length && obs?.id === pick.obs.id
                ? pick.node.index
                : ""
            }
            onChange={e => {
              if (!pick) return;
              inspectionPaused.current = true;
              setLive(false);
              setFrame(pick.obs);
              setMenu(null);
              choose(pick.obs, pick.obs.nodes[Number(e.target.value)]);
            }}
          >
            {!pick || !candidates.length || obs?.id !== pick.obs.id ? (
              <option value="">请先在画面中选择元素</option>
            ) : (
              candidates.map(n => (
                <option key={n.index} value={n.index}>
                  {n.label || n.className} ·{" "}
                  {n.clickable ? "可点击" : "不可点击"} · {n.index}
                </option>
              ))
            )}
          </select>
        </label>
      </div>
      <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="max-h-[800px] overflow-auto rounded border p-3">
          <h3 className="font-medium">应用与元素树</h3>
          <p className="my-2 text-xs text-muted-foreground">
            单击查看 · 右键管理 · 按设备保存
          </p>
          {graph.apps.map(app => (
            <div key={app.id} className="mb-3">
              <button
                className="w-full rounded p-2 text-left font-medium hover:bg-muted"
                onClick={() => {
                  setAppId(app.id);
                  setPageId("");
                  setRecaptureId("");
                }}
              >
                ▾ {app.name}
                {appId === app.id ? " ✓" : ""}
              </button>
              <p className="px-2 text-xs break-all text-muted-foreground">
                {app.package || "包名待确认"}
              </p>
              {graph.pages
                .filter(
                  p =>
                    p.appId === app.id &&
                    (!p.parentPageId ||
                      !graph.pages.some(parent => parent.id === p.parentPageId))
                )
                .map(p => renderPage(p, new Set(), 1))}
            </div>
          ))}
          {!graph.pages.length && (
            <p className="py-5 text-sm text-muted-foreground">
              先添加当前应用并保存元素；能打开画面的元素可以绑定为父元素，再保存子元素。
            </p>
          )}
        </aside>
        <div className="min-w-0 space-y-3">
          <h3 className="font-medium">设备画面</h3>
          <div className="flex flex-wrap gap-4 text-sm">
            <label>
              <input
                type="radio"
                name="map-mode"
                checked={mode === "inspect"}
                disabled={busy}
                onChange={() => void switchMode("inspect")}
              />{" "}
              选择元素
            </label>
            <label>
              <input
                type="radio"
                name="map-mode"
                checked={mode === "operate"}
                disabled={busy || !serial}
                onChange={() => void switchMode("operate")}
              />{" "}
              操作设备
            </label>
            <span className="text-muted-foreground">操作模式不写入保存树</span>
          </div>
          <p className="text-xs text-muted-foreground">
            {mode === "operate"
              ? "scrcpy 实时画面与触摸控制；完成切换后进入选择元素模式保存。"
              : "在画面中选择元素，右键保存到下方指定页面。"}
          </p>
          {mode === "inspect" && (
            <div className="flex flex-wrap gap-2 rounded border p-3 text-sm">
              <Button
                variant="outline"
                disabled={!obs || busy}
                onClick={() => {
                  setDialogName("");
                  setDialog("app");
                }}
              >
                添加当前应用
              </Button>
              <select
                aria-label="所属应用"
                className="min-w-0 max-w-full rounded border bg-background p-2"
                value={appId}
                onChange={e => {
                  setAppId(e.target.value);
                  setPageId("");
                  setRecaptureId("");
                }}
              >
                <option value="">选择应用</option>
                {graph.apps.map(a => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <Button
                variant="outline"
                disabled={
                  !obs || !appId || busy || (!pendingEntryId && !draftToken)
                }
                title="先将已保存元素设为父元素，操作进入后绑定打开结果"
                onClick={() => {
                  setDialogName(
                    pendingEntryId
                      ? graph.elements.find(e => e.id === pendingEntryId)
                          ?.name || ""
                      : ""
                  );
                  const selected = graph.pages.find(p => p.id === pageId);
                  setParentId(
                    pendingEntryId
                      ? graph.elements.find(e => e.id === pendingEntryId)
                          ?.pageId || ""
                      : selected?.signature === obs?.signature ||
                          (obs?.identity && selected?.identity === obs.identity)
                        ? selected?.parentPageId || ""
                        : pageId
                  );
                  setAllowMissing(false);
                  setDialog("page");
                }}
              >
                保存父元素 / 绑定打开结果
              </Button>
              <select
                aria-label="父元素"
                className="min-w-0 max-w-full rounded border bg-background p-2"
                value={pageId}
                onChange={e => {
                  setPageId(e.target.value);
                  setRecaptureId("");
                }}
              >
                <option value="">应用根节点</option>
                {graph.pages
                  .filter(p => p.appId === appId)
                  .map(p => (
                    <option key={p.id} value={p.id}>
                      {p.parentPageId || p.entry
                        ? pageLabel(p)
                        : `应用根节点 · ${p.name}`}
                    </option>
                  ))}
              </select>
              {pendingEntryId && (
                <div className="flex items-center gap-2 text-amber-400">
                  待绑定父元素：
                  {graph.elements.find(e => e.id === pendingEntryId)?.name}
                  <Button
                    variant="outline"
                    onClick={() => setPendingEntryId("")}
                  >
                    取消绑定
                  </Button>
                </div>
              )}
              {recaptureId && (
                <span className="text-amber-400">
                  重新标定：
                  {graph.elements.find(e => e.id === recaptureId)?.name}
                  ，请在画面选择新元素后保存
                </span>
              )}
            </div>
          )}
          {mode === "operate" ? (
            <ScrcpyScreen
              ref={scrcpy}
              api={API}
              serial={serial}
              observationId={obs?.id}
              onDraft={setDraftToken}
            />
          ) : frame ? (
            <div className="relative w-fit max-w-full overflow-hidden rounded border bg-black">
              <img
                ref={imageRef}
                src={frame.image}
                alt="当前设备画面，可选择元素或操作"
                draggable={false}
                className="block h-auto max-h-[65vh] max-w-full w-auto cursor-crosshair"
                style={{ touchAction: "none" }}
                onClick={e => screenClick(e)}
                onContextMenu={e => screenClick(e, true)}
              />
              {pick &&
                obs?.id === pick.obs.id &&
                frame.image === pick.obs.image &&
                pick.node.bounds && (
                  <div
                    className="pointer-events-none absolute border-2 border-cyan-400"
                    style={{
                      left: `${(pick.node.bounds[0] / frame.width) * 100}%`,
                      top: `${(pick.node.bounds[1] / frame.height) * 100}%`,
                      width: `${((pick.node.bounds[2] - pick.node.bounds[0]) / frame.width) * 100}%`,
                      height: `${((pick.node.bounds[3] - pick.node.bounds[1]) / frame.height) * 100}%`,
                    }}
                  />
                )}
            </div>
          ) : (
            <div className="flex min-h-80 items-center justify-center rounded border text-muted-foreground">
              选择设备并读取当前画面
            </div>
          )}
        </div>
      </div>
      {error && (
        <p
          role="alert"
          className="rounded border border-red-500 p-3 text-sm text-red-400"
        >
          {error}
        </p>
      )}
      <div
        role="status"
        className="flex flex-wrap items-center gap-3 rounded bg-muted p-3 text-sm"
      >
        {status}
        {undo && (
          <Button variant="outline" onClick={() => edit(undo, "restore")}>
            撤销删除
          </Button>
        )}
      </div>
      {mode === "inspect" && pick && (
        <section className="space-y-3 rounded border p-4">
          <h3 className="font-medium">
            {viewParentId ? "父元素入口详情" : "选中元素详情"} · 检查快照{" "}
            {new Date(pick.obs.capturedAt * 1000).toLocaleTimeString()}
          </h3>
          <div className="grid gap-5 md:grid-cols-[minmax(160px,1fr)_minmax(0,3fr)]">
            <div>
              <canvas
                ref={cropRef}
                aria-label="元素真实截图"
                className="max-h-44 max-w-full border object-contain"
              />
            </div>
            <div className="min-w-0 space-y-3 text-sm">
              <label>
                保存名称{" "}
                <input
                  className="ml-2 max-w-full rounded border bg-background p-2"
                  value={name}
                  readOnly={!!viewParentId}
                  onChange={e => setName(e.target.value)}
                />
              </label>
              <div className="overflow-hidden rounded border">
                <table
                  className="w-full table-fixed border-collapse text-sm"
                  aria-label="元素属性"
                >
                  <tbody>
                    {[
                      ["ResourceId", pick.node.resourceId || "未提供"],
                      ["XPath", pick.node.xpath],
                      [
                        "Bounds",
                        pick.node.bounds
                          ? `${JSON.stringify(pick.node.bounds)} px`
                          : "未提供",
                      ],
                      ["Text", pick.node.text || "未提供"],
                      ["Content-desc", pick.node.description || "未提供"],
                      ["Class", pick.node.className || "未提供"],
                      ["Package", pick.node.package || "未提供"],
                      ["可点击", pick.node.clickable ? "是" : "否"],
                      [
                        "开关状态",
                        pick.node.checkable
                          ? pick.node.checked
                            ? "开启"
                            : "关闭"
                          : "不适用",
                      ],
                    ].map(([label, value]) => (
                      <tr key={label} className="border-b last:border-b-0">
                        <th
                          scope="row"
                          className="w-28 border-r bg-muted/40 px-3 py-2 text-left align-top font-medium text-muted-foreground sm:w-32"
                        >
                          {label}
                        </th>
                        <td className="whitespace-pre-wrap break-all px-3 py-2 align-top font-mono text-xs leading-6">
                          {value}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!viewParentId && (
                <Button
                  disabled={busy || !appId || mode !== "inspect"}
                  onClick={() => save(pick)}
                >
                  保存 / 更新元素
                </Button>
              )}
            </div>
          </div>
        </section>
      )}
      {dialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-label={
              dialog === "app"
                ? "保存应用"
                : dialog === "page"
                  ? "绑定父元素"
                  : "移动元素"
            }
            className="w-full max-w-lg space-y-4 rounded border bg-background p-5"
          >
            <h3 className="font-semibold">
              {dialog === "app"
                ? "添加当前应用"
                : dialog === "page"
                  ? "保存父元素 / 绑定打开结果"
                  : "移动元素"}
            </h3>
            {dialog !== "move" && (
              <label className="block">
                名称（可选）
                <input
                  autoFocus
                  className="mt-2 w-full rounded border bg-background p-2"
                  value={dialogName}
                  onChange={e => setDialogName(e.target.value)}
                  placeholder={
                    dialog === "app" ? "默认使用包名" : "例如：显示入口"
                  }
                />
              </label>
            )}
            {dialog === "app" && (
              <p className="break-all text-sm text-muted-foreground">
                包名将从当前标定快照自动读取。
              </p>
            )}
            {dialog === "page" && (
              <>
                <p>所属应用：{graph.apps.find(a => a.id === appId)?.name}</p>
                <label className="block">
                  上层父元素
                  <select
                    className="mt-2 w-full rounded border bg-background p-2"
                    value={parentId}
                    onChange={e => setParentId(e.target.value)}
                  >
                    <option value="">应用根节点（无控件入口）</option>
                    {pendingEntryId &&
                      graph.elements.find(e => e.id === pendingEntryId) && (
                        <option
                          value={
                            graph.elements.find(e => e.id === pendingEntryId)!
                              .pageId
                          }
                        >
                          选定入口的来源节点
                        </option>
                      )}
                    {graph.pages
                      .filter(p => p.appId === appId)
                      .map(p => (
                        <option key={p.id} value={p.id}>
                          {p.parentPageId || p.entry
                            ? pageLabel(p)
                            : `应用根节点 · ${p.name}`}
                        </option>
                      ))}
                  </select>
                </label>
                <p className="text-sm text-muted-foreground">
                  {parentId
                    ? "将保存入口控件的截图和全部属性，并绑定当前打开结果。请使用所选入口完成一次进入操作。"
                    : "跳转时启动应用并验证实际落地页；启动不一定回到此页。"}
                </p>
                {parentId && (
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={allowMissing}
                      onChange={e => setAllowMissing(e.target.checked)}
                    />
                    入口不足时保留待补充节点
                  </label>
                )}
              </>
            )}
            {dialog === "move" && (
              <>
                <label className="block">
                  目标页面
                  <select
                    autoFocus
                    className="mt-2 w-full rounded border bg-background p-2"
                    value={movePageId}
                    onChange={e => setMovePageId(e.target.value)}
                  >
                    <option value="">请选择目标页面</option>
                    {graph.apps.map(a => (
                      <optgroup key={a.id} label={a.name}>
                        {graph.pages
                          .filter(p => p.appId === a.id)
                          .map(p => (
                            <option key={p.id} value={p.id}>
                              {p.parentPageId || p.entry
                                ? pageLabel(p)
                                : `应用根节点 · ${p.name}`}
                            </option>
                          ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <p className="text-sm text-muted-foreground">
                  只更新归属，不操作设备。跨应用移动后需要重新标定。
                </p>
              </>
            )}
            {error && (
              <p role="alert" className="text-sm text-red-400">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => setDialog(null)}
              >
                取消
              </Button>
              <Button
                disabled={busy || (dialog === "move" && !movePageId)}
                onClick={() =>
                  dialog === "move" ? moveElement() : saveStructure()
                }
              >
                保存
              </Button>
            </div>
          </div>
        </div>
      )}
      {menu && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setMenu(null)}
            onContextMenu={e => {
              e.preventDefault();
              setMenu(null);
            }}
          />
          <div
            role="menu"
            className="fixed z-50 flex max-h-[calc(100vh-16px)] w-64 flex-col gap-2 overflow-y-auto rounded border bg-popover p-3 text-popover-foreground"
            style={{ left: menu.x, top: menu.y }}
          >
            {menu.kind === "screen" ? (
              <>
                <span className="text-sm">
                  {menu.pick!.node.label || menu.pick!.node.className}
                </span>
                <Button
                  disabled={busy || !appId || mode !== "inspect"}
                  onClick={() => save(menu.pick!)}
                >
                  保存到所选父节点
                </Button>
              </>
            ) : (
              <>
                <Button
                  disabled={busy || mode !== "inspect"}
                  onClick={() => execute(menu.id!, "navigate")}
                >
                  {menu.kind === "page"
                    ? "打开父元素并进入"
                    : "进入元素所在画面"}
                </Button>
                {menu.kind === "page" && (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => {
                      const p = graph.pages.find(p => p.id === menu.id)!;
                      setMenu(null);
                      void selectParent(p);
                    }}
                  >
                    查看入口元素属性
                  </Button>
                )}
                {selectedElement && (
                  <>
                    <Button
                      disabled={busy}
                      onClick={() => execute(menu.id!, "click")}
                    >
                      到页后点击一次
                    </Button>
                    {selectedElement.checkable && (
                      <>
                        <Button
                          disabled={busy}
                          onClick={() => execute(menu.id!, "on")}
                        >
                          到页后开启
                        </Button>
                        <Button
                          disabled={busy}
                          onClick={() => execute(menu.id!, "off")}
                        >
                          到页后关闭
                        </Button>
                      </>
                    )}
                  </>
                )}
                {menu.kind === "element" && (
                  <>
                    <Button
                      variant="outline"
                      disabled={busy || mode !== "inspect"}
                      onClick={() => {
                        const el = selectedElement!;
                        const parent = graph.pages.find(
                          p => p.id === el.pageId
                        )!;
                        setPendingEntryId(el.id);
                        setPageId(parent.id);
                        setAppId(parent.appId || "");
                        setMenu(null);
                        setStatus(
                          `已选择父元素「${el.name}」。请在其来源画面读取快照，进入操作模式点击它，再切回选择元素并点击“保存父元素 / 绑定打开结果”。`
                        );
                      }}
                    >
                      设为父元素…
                    </Button>
                    <Button
                      variant="outline"
                      disabled={busy || mode !== "inspect"}
                      onClick={() => {
                        setMoveId(menu.id!);
                        setMovePageId("");
                        setDialog("move");
                        setMenu(null);
                      }}
                    >
                      移动到…
                    </Button>
                    {selectedElement?.moveUndo != null && (
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          const id = menu.id!;
                          setMenu(null);
                          void job(async () => {
                            setGraph(
                              await api("move-element", {
                                serial,
                                elementId: id,
                                undo: true,
                              })
                            );
                            setStatus("已撤销移动。");
                          });
                        }}
                      >
                        撤销移动
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      disabled={busy || mode !== "inspect"}
                      onClick={() => {
                        const el = selectedElement!;
                        const p = graph.pages.find(p => p.id === el.pageId)!;
                        setAppId(p.appId || "");
                        setPageId(p.id);
                        setRecaptureId(el.id);
                        setName(el.name);
                        setMenu(null);
                        setStatus("请在目标页面读取画面、选择正确元素后保存。");
                      }}
                    >
                      重新标定此元素
                    </Button>
                  </>
                )}
                <input
                  aria-label="重命名保存项"
                  className="min-w-0 rounded border bg-background p-2"
                  value={name}
                  onChange={e => setName(e.target.value)}
                />
                <Button
                  disabled={busy}
                  onClick={() => edit(menu.id!, "rename", name)}
                >
                  重命名
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => edit(menu.id!, "delete")}
                >
                  {menu.kind === "page"
                    ? "删除父元素及其直属元素"
                    : "删除保存项"}
                </Button>
              </>
            )}
            <Button variant="ghost" onClick={() => setMenu(null)}>
              取消
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
