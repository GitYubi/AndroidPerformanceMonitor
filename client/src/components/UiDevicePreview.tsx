import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/button";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import ScrcpyScreen, { type ScrcpyHandle } from "@/components/ScrcpyScreen";
import { BACKEND_URL } from "@/lib/backend";
import {
  elementCenter,
  nodesAtPoint,
  screenPixel,
  type MapNode,
  type Observation,
} from "@/lib/uiObservation";
import type { Selector } from "@/lib/uiSteps";

export type UiDevicePreviewHandle = { stop: () => Promise<void> };
type Frame = Pick<Observation, "image" | "width" | "height">;
type Pixel = { x: number; y: number };
type Props = {
  serial: string;
  disabled: boolean;
  running: boolean;
  targets: { id: string; label: string }[];
  target: string;
  onTargetChange: (id: string) => void;
  onApply: (selector: Selector) => void;
};

async function readDevice<T>(
  path: string,
  serial: string,
  signal: AbortSignal
): Promise<T> {
  const response = await fetch(`${BACKEND_URL}/api/ui/map/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ serial }),
    signal,
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string" ? result.detail : "设备画面读取失败"
    );
  return result;
}

function selectorFor(node: MapNode, observation: Observation): Selector {
  const selector = {
    resourceId: node.resourceId,
    className: node.className,
    package: node.package,
  };
  const sameIdentity = observation.nodes.filter(
    other =>
      other.resourceId === node.resourceId &&
      other.className === node.className &&
      other.package === node.package
  );
  if (node.resourceId && sameIdentity.length === 1) return selector;
  const label = node.text || node.description;
  const stillAmbiguous =
    sameIdentity.filter(
      other => !label || label === other.text || label === other.description
    ).length > 1;
  return {
    ...selector,
    label,
    ...(stillAmbiguous ? { xpath: node.xpath } : {}),
  };
}

function ElementDetail({ label, value }: { label: string; value?: string }) {
  return (
    <div className="grid min-w-0 grid-cols-[5.75rem_minmax(0,1fr)_auto] items-start gap-2 border-t border-border/60 px-2.5 py-1.5 first:border-t-0">
      <dt className="py-1 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all py-1 font-mono">{value || "未提供"}</dd>
      {value && (
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="size-6"
          aria-label={`复制 ${label}`}
          title={`复制 ${label}`}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              toast.success(`已复制 ${label}`);
            } catch {
              toast.error("无法访问剪贴板，可选中文本后手动复制");
            }
          }}
        >
          <Copy className="size-3.5" />
        </Button>
      )}
    </div>
  );
}

const pixelText = (point: Pixel) => `(${point.x}, ${point.y}) px`;

function PixelMarker({
  point,
  frame,
  center = false,
}: {
  point: Pixel;
  frame: Frame;
  center?: boolean;
}) {
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute z-10 size-2.5 -translate-x-1/2 -translate-y-1/2 border-2 shadow-[0_0_0_1px_#0f172a] ${center ? "rounded-sm border-white bg-cyan-500" : "rounded-full border-white bg-amber-500"}`}
      style={{
        left: `${((point.x + 0.5) * 100) / frame.width}%`,
        top: `${((point.y + 0.5) * 100) / frame.height}%`,
      }}
    />
  );
}

export const UiDevicePreview = forwardRef<UiDevicePreviewHandle, Props>(
  function UiDevicePreview(
    { serial, disabled, running, targets, target, onTargetChange, onApply },
    ref
  ) {
    const [live, setLive] = useState(false);
    const [frame, setFrame] = useState<Frame | null>(null);
    const [observation, setObservation] = useState<Observation | null>(null);
    const [candidates, setCandidates] = useState<MapNode[]>([]);
    const [node, setNode] = useState<MapNode | null>(null);
    const [clickedPoint, setClickedPoint] = useState<Pixel | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const scrcpy = useRef<ScrcpyHandle>(null);
    const pending = useRef<Promise<void> | null>(null);
    const request = useRef<AbortController>(null);

    useImperativeHandle(
      ref,
      () => ({
        stop: async () => {
          await pending.current;
          await scrcpy.current?.stop();
          setLive(false);
        },
      }),
      []
    );

    function capture(inspect: boolean) {
      if (!serial || pending.current || disabled || running) return;
      const controller = new AbortController();
      request.current = controller;
      setLoading(true);
      setError("");
      pending.current = (async () => {
        try {
          await scrcpy.current?.stop();
          if (controller.signal.aborted) return;
          setLive(false);
          const inspected = inspect
            ? await readDevice<Observation>(
                "observe",
                serial,
                controller.signal
              )
            : null;
          const next =
            inspected ??
            (await readDevice<Frame>("frame", serial, controller.signal));
          if (controller.signal.aborted) return;
          setFrame(next);
          setObservation(inspected);
          setCandidates([]);
          setNode(null);
          setClickedPoint(null);
        } catch (cause) {
          if (!controller.signal.aborted)
            setError(cause instanceof Error ? cause.message : String(cause));
        } finally {
          pending.current = null;
          if (!controller.signal.aborted) setLoading(false);
        }
      })();
    }

    useEffect(() => () => request.current?.abort(), []);

    function pick(event: React.MouseEvent<HTMLImageElement>) {
      if (!frame || loading) return;
      const point = screenPixel(
        frame,
        event.currentTarget.getBoundingClientRect(),
        event.clientX,
        event.clientY
      );
      if (!point) return;
      setClickedPoint(point);
      if (!observation) return;
      const matches = nodesAtPoint(observation, point.x, point.y);
      setCandidates(matches);
      setNode(matches[0] ?? null);
      setError(matches.length ? "" : "该区域没有可选元素");
    }

    const bounds = node?.bounds;
    const center = bounds ? elementCenter(bounds) : null;
    const canCapture = Boolean(serial) && !disabled && !running && !loading;
    return (
      <section
        aria-label="车机投屏"
        className="min-w-0 space-y-2 rounded-lg border border-border bg-card p-3 text-card-foreground"
      >
        <div className="flex flex-wrap gap-1.5">
          <Button
            type="button"
            variant={live ? "secondary" : "outline"}
            size="sm"
            disabled={!canCapture}
            onClick={() => {
              if (live) {
                capture(false);
                return;
              }
              setError("");
              setLive(true);
            }}
          >
            {live ? "停止投屏" : "实时投屏"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canCapture}
            onClick={() => capture(true)}
          >
            {loading ? "读取中…" : "读取画面与控件"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!canCapture || live}
            onClick={() => capture(false)}
          >
            刷新截图
          </Button>
        </div>
        {live && serial ? (
          <ScrcpyScreen
            ref={scrcpy}
            api={BACKEND_URL}
            serial={serial}
            liveMessage="实时投屏 · 可点击或滑动设备画面"
          />
        ) : frame ? (
          <div className="relative mx-auto w-fit max-w-full overflow-hidden rounded-md border border-border bg-black">
            <img
              src={frame.image}
              alt="当前车机画面"
              draggable={false}
              className="block h-auto max-h-[50dvh] w-auto max-w-full cursor-crosshair"
              onClick={pick}
            />
            {bounds && (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute border-2 border-cyan-400"
                style={{
                  left: `${(bounds[0] * 100) / frame.width}%`,
                  top: `${(bounds[1] * 100) / frame.height}%`,
                  width: `${((bounds[2] - bounds[0]) * 100) / frame.width}%`,
                  height: `${((bounds[3] - bounds[1]) * 100) / frame.height}%`,
                }}
              />
            )}
            {center && <PixelMarker point={center} frame={frame} center />}
            {clickedPoint && <PixelMarker point={clickedPoint} frame={frame} />}
          </div>
        ) : (
          <div className="grid min-h-56 place-items-center rounded-md border border-dashed border-border px-4 text-center text-sm text-muted-foreground">
            {running
              ? "当前设备正在执行用例"
              : serial
                ? "开始实时投屏，或读取当前画面与控件"
                : "请先选择目标设备"}
          </div>
        )}
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
        {!live && clickedPoint && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 text-xs">
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <span
                aria-hidden="true"
                className="size-2 rounded-full bg-amber-500"
              />
              点击坐标{" "}
              <code className="text-foreground">{pixelText(clickedPoint)}</code>
            </span>
            {center && (
              <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                <span
                  aria-hidden="true"
                  className="size-2 rounded-sm bg-cyan-500"
                />
                元素中心{" "}
                <code className="text-foreground">{pixelText(center)}</code>
              </span>
            )}
          </div>
        )}
        {!live && observation && (
          <div className="space-y-2 text-xs">
            {!node && (
              <p className="text-muted-foreground">点击画面中的元素进行选择</p>
            )}
            {node && (
              <>
                <label className="grid gap-1 text-muted-foreground">
                  选中元素
                  <select
                    className="h-8 w-full rounded border border-input bg-background px-2 text-foreground"
                    value={node.index}
                    onChange={event =>
                      setNode(
                        candidates.find(
                          item => item.index === Number(event.target.value)
                        ) ?? null
                      )
                    }
                  >
                    {candidates.map(item => (
                      <option key={item.index} value={item.index}>
                        {[item.label, item.resourceId || item.className]
                          .filter(Boolean)
                          .join(" · ")}
                      </option>
                    ))}
                  </select>
                </label>
                <dl
                  aria-label="元素定位信息"
                  className="rounded-md border border-border bg-muted/25"
                >
                  <ElementDetail label="XPath" value={node.xpath} />
                  <ElementDetail label="Resource ID" value={node.resourceId} />
                  <ElementDetail
                    label="元素边界"
                    value={bounds ? `[${bounds.join(", ")}] px` : undefined}
                  />
                </dl>
                {targets.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <select
                      aria-label="回填到步骤"
                      className="h-8 min-w-0 flex-1 rounded border border-input bg-background px-2"
                      value={target}
                      onChange={event => onTargetChange(event.target.value)}
                    >
                      <option value="">选择要回填的步骤</option>
                      {targets.map(item => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <Button
                      type="button"
                      size="sm"
                      disabled={!target || disabled || loading}
                      onClick={() => onApply(selectorFor(node, observation))}
                    >
                      应用到步骤
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </section>
    );
  }
);
