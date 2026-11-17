import { useEffect, useRef, useState, type PointerEvent } from "react";
import { toast } from "sonner";
import { BACKEND_URL } from "@/lib/backend";

type Box = { left: number; top: number; right: number; bottom: number };
type Area = Box & { package: string };
type Target = {
  display_id: number;
  fingerprint: string;
  mode?: "whitelist" | "blacklist";
  packages: string[];
  launch_packages?: string[];
  areas: Area[];
};
type Screen = {
  display_id: number;
  fingerprint: string;
  width: number;
  height: number;
  rotation: number;
  user_id: number | null;
  physical_id: string | null;
  input_supported: boolean;
  activity: string;
  reason: string;
};
type Frame = { screen: Screen; image: string };
type Counts = Partial<
  Record<"planned" | "success" | "skipped" | "failed", number>
>;
export type MultiScreen = {
  targets: Target[];
  actions: Record<string, number>;
};
export type DisplaySummary = {
  seed: number;
  selected_coverage: boolean;
  screens: {
    display_id: number;
    selected: boolean;
    input_supported: boolean;
    injected: number;
    reason: string;
    actions: Record<string, Counts>;
    reasons: Record<string, number>;
  }[];
  failures: {
    display_id: number | null;
    package?: string;
    kind?: string;
    detail?: string;
  }[];
};

export const labels: Record<string, string> = {
  tap: "点击",
  long_press: "长按",
  swipe: "滑动",
  pinch: "双指缩放",
  app_switch: "应用切换",
};
export const newMultiScreen = (): MultiScreen => ({
  targets: [],
  actions: { tap: 45, long_press: 10, swipe: 30, pinch: 15, app_switch: 0 },
});
const field =
  "w-full rounded border border-border bg-background p-2 text-sm text-foreground";
const button =
  "rounded border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-40";
const isPackage = (name: string) =>
  /^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(name);

export function validMultiScreen(config: MultiScreen) {
  return (
    config.targets.length > 0 &&
    Object.values(config.actions).every(
      value => Number.isInteger(value) && value >= 0 && value <= 100
    ) &&
    Object.values(config.actions).reduce((sum, value) => sum + value, 0) ===
      100 &&
    config.targets.every(target => {
      const targets = target.launch_packages ?? [];
      const blacklist = target.mode === "blacklist";
      return (
        (target.packages.length > 0 ||
          (!blacklist && target.areas.length > 0)) &&
        target.packages.every(isPackage) &&
        targets.every(isPackage) &&
        (!blacklist ||
          ![...targets, ...target.areas.map(area => area.package)].some(pkg =>
            target.packages.includes(pkg)
          ))
      );
    }) &&
    (!config.actions.app_switch ||
      config.targets.some(
        target =>
          (target.mode === "blacklist"
            ? target.launch_packages
            : target.packages
          )?.length
      ))
  );
}

async function read<T>(
  path: string,
  serial: string,
  fingerprint?: string
): Promise<T> {
  const params = new URLSearchParams({ serial });
  if (fingerprint) params.set("fingerprint", fingerprint);
  const response = await fetch(
    `${BACKEND_URL}/api/monkey/displays${path}?${params}`
  );
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string" ? value.detail : "屏幕读取失败"
    );
  return value as T;
}

function Packages({
  packages,
  title,
  onChange,
}: {
  packages: string[];
  title: string;
  onChange: (packages: string[]) => void;
}) {
  const [text, setText] = useState(packages.join("\n"));
  return (
    <label className="block text-xs text-muted-foreground">
      {title}
      <textarea
        className={`${field} mt-1`}
        rows={2}
        value={text}
        spellCheck={false}
        onChange={event => {
          setText(event.target.value);
          onChange(
            Array.from(new Set(event.target.value.split(/\s+/).filter(Boolean)))
          );
        }}
      />
    </label>
  );
}

export function MonkeyDisplays({
  serial,
  value,
  defaultBlacklist,
  onChange,
}: {
  serial: string;
  value: MultiScreen;
  defaultBlacklist: string[];
  onChange: (value: MultiScreen) => void;
}) {
  const [screens, setScreens] = useState<Screen[]>([]);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [areaPackage, setAreaPackage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const start = useRef<{ x: number; y: number } | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const target = value.targets.find(
    item => item.display_id === frame?.screen.display_id
  );
  const updateTarget = (id: number, patch: Partial<Target>) =>
    onChange({
      ...value,
      targets: value.targets.map(item =>
        item.display_id === id ? { ...item, ...patch } : item
      ),
    });

  async function operation(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      if (mounted.current) setError((cause as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  function position(event: PointerEvent<HTMLImageElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const { width, height } = frame!.screen;
    return {
      x: Math.max(
        0,
        Math.min(
          width - 1,
          Math.floor(((event.clientX - rect.left) / rect.width) * width)
        )
      ),
      y: Math.max(
        0,
        Math.min(
          height - 1,
          Math.floor(((event.clientY - rect.top) / rect.height) * height)
        )
      ),
    };
  }

  const total = Object.values(value.actions).reduce((sum, n) => sum + n, 0);
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-3 text-card-foreground">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">目标屏幕</h3>
        <button
          type="button"
          className={button}
          disabled={!serial || busy}
          onClick={() =>
            void operation(async () => {
              const found = await read<Screen[]>("", serial);
              if (mounted.current) setScreens(found);
            })
          }
        >
          扫描屏幕
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="grid gap-2">
        {screens.map(screen => {
          const selected = value.targets.find(
            item => item.display_id === screen.display_id
          );
          const stale = selected && selected.fingerprint !== screen.fingerprint;
          return (
            <div
              key={screen.display_id}
              className="space-y-2 rounded border border-border p-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    disabled={!screen.input_supported}
                    checked={!!selected && !stale}
                    onChange={event =>
                      onChange({
                        ...value,
                        targets: [
                          ...value.targets.filter(
                            item => item.display_id !== screen.display_id
                          ),
                          ...(event.target.checked
                            ? [
                                {
                                  display_id: screen.display_id,
                                  fingerprint: screen.fingerprint,
                                  packages: [],
                                  areas: [],
                                },
                              ]
                            : []),
                        ],
                      })
                    }
                  />
                  屏幕 {screen.display_id} · {screen.width}×{screen.height}
                </label>
                <button
                  type="button"
                  className={button}
                  disabled={busy || !screen.physical_id}
                  onClick={() =>
                    void operation(async () => {
                      const result = await read<Frame>(
                        `/${screen.display_id}/frame`,
                        serial,
                        screen.fingerprint
                      );
                      if (!mounted.current) return;
                      setFrame(result);
                      setBox(null);
                      setAreaPackage("");
                    })
                  }
                >
                  浏览屏幕 {screen.display_id}
                </button>
              </div>
              <p className="text-xs text-muted-foreground">
                旋转 {screen.rotation * 90}° · 用户 {screen.user_id ?? "未知"} ·{" "}
                {screen.input_supported ? "可选" : screen.reason}
              </p>
              {stale && (
                <p className="text-xs text-amber-500">
                  屏幕配置已变化，请重新勾选。
                </p>
              )}
              {selected && !stale && (
                <>
                  <label className="block text-xs text-muted-foreground">
                    屏幕 {screen.display_id} 包名单模式
                    <select
                      className={`${field} mt-1`}
                      value={selected.mode ?? "whitelist"}
                      onChange={event =>
                        updateTarget(screen.display_id, {
                          mode: event.target.value as Target["mode"],
                          packages:
                            event.target.value === "blacklist"
                              ? Array.from(
                                  new Set([
                                    ...defaultBlacklist,
                                    "com.baic.icc.app.vehiclesetting",
                                    "com.baic.icc.launcher",
                                  ])
                                )
                              : [],
                          launch_packages: [],
                        })
                      }
                    >
                      <option value="whitelist">白名单 · 仅操作指定应用</option>
                      <option value="blacklist">黑名单 · 跳过指定应用</option>
                    </select>
                  </label>
                  <Packages
                    key={selected.mode}
                    packages={selected.packages}
                    title={
                      selected.mode === "blacklist"
                        ? "排除应用包名（每行一个）"
                        : "允许应用包名（每行一个）"
                    }
                    onChange={packages =>
                      updateTarget(screen.display_id, { packages })
                    }
                  />
                  {selected.mode === "blacklist" && (
                    <Packages
                      packages={selected.launch_packages ?? []}
                      title="应用切换候选（可选，每行一个）"
                      onChange={launch_packages =>
                        updateTarget(screen.display_id, { launch_packages })
                      }
                    />
                  )}
                  {selected.areas.map((area, index) => (
                    <div
                      key={index}
                      className="flex flex-wrap items-center gap-2 text-xs"
                    >
                      <span>
                        系统区 {area.package} · ({area.left}, {area.top}) – (
                        {area.right}, {area.bottom})
                      </span>
                      <button
                        type="button"
                        className="text-destructive"
                        onClick={() =>
                          updateTarget(screen.display_id, {
                            areas: selected.areas.filter((_, i) => i !== index),
                          })
                        }
                      >
                        移除区域
                      </button>
                    </div>
                  ))}
                </>
              )}
            </div>
          );
        })}
      </div>
      {frame && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-medium">
              屏幕 {frame.screen.display_id} 预览
            </h4>
            <button
              type="button"
              className={button}
              onClick={() => {
                setFrame(null);
                setBox(null);
              }}
            >
              关闭预览
            </button>
          </div>
          <div className="relative w-fit overflow-hidden rounded border border-border">
            <img
              src={frame.image}
              alt={`屏幕 ${frame.screen.display_id} 截图`}
              draggable={false}
              className={`block max-w-full ${target ? "touch-none cursor-crosshair" : ""}`}
              onPointerDown={event => {
                if (!target || !event.isPrimary || event.button !== 0) return;
                event.currentTarget.setPointerCapture(event.pointerId);
                start.current = position(event);
                setBox(null);
              }}
              onPointerMove={event => {
                if (!start.current) return;
                const from = start.current,
                  to = position(event);
                setBox({
                  left: Math.min(from.x, to.x),
                  top: Math.min(from.y, to.y),
                  right: Math.max(from.x, to.x) + 1,
                  bottom: Math.max(from.y, to.y) + 1,
                });
              }}
              onPointerUp={() => {
                start.current = null;
              }}
              onPointerCancel={() => {
                start.current = null;
                setBox(null);
              }}
            />
            {box && (
              <span
                aria-hidden
                className="pointer-events-none absolute border-2 border-cyan-400 bg-cyan-400/20"
                style={{
                  left: `${(box.left / frame.screen.width) * 100}%`,
                  top: `${(box.top / frame.screen.height) * 100}%`,
                  width: `${((box.right - box.left) / frame.screen.width) * 100}%`,
                  height: `${((box.bottom - box.top) / frame.screen.height) * 100}%`,
                }}
              />
            )}
          </div>
          {target && target.fingerprint === frame.screen.fingerprint && (
            <div className="flex items-end gap-2">
              <label className="flex-1 text-xs text-muted-foreground">
                框选系统区域对应的包名
                <input
                  className={`${field} mt-1`}
                  value={areaPackage}
                  onChange={event => setAreaPackage(event.target.value)}
                />
              </label>
              <button
                type="button"
                className={button}
                disabled={
                  !box ||
                  !isPackage(areaPackage.trim()) ||
                  (target.mode === "blacklist" &&
                    target.packages.includes(areaPackage.trim()))
                }
                onClick={() => {
                  if (!box) return;
                  updateTarget(target.display_id, {
                    areas: [
                      ...target.areas,
                      { ...box, package: areaPackage.trim() },
                    ],
                  });
                  setBox(null);
                  toast.success("已加入系统区域");
                }}
              >
                添加区域
              </button>
            </div>
          )}
        </div>
      )}
      <h4 className="text-sm font-medium">多屏动作比例 · {total}% / 100%</h4>
      <div className="grid grid-cols-2 gap-2">
        {Object.entries(labels).map(([key, label]) => (
          <label key={key} className="text-xs text-muted-foreground">
            {label} %
            <input
              className={`${field} mt-1`}
              type="number"
              min={0}
              max={100}
              step={1}
              value={value.actions[key]}
              onChange={event =>
                onChange({
                  ...value,
                  actions: {
                    ...value.actions,
                    [key]: Number(event.target.value),
                  },
                })
              }
            />
          </label>
        ))}
      </div>
    </div>
  );
}

export function MonkeyScreenCoverage({
  summary,
  download,
}: {
  summary: DisplaySummary;
  download: string;
}) {
  return (
    <section className="space-y-3 rounded-lg border border-border bg-card p-4 text-card-foreground">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-medium">多屏动作记录</h2>
        <a className={button} href={download}>
          下载按屏记录
        </a>
      </div>
      <p className="text-xs text-muted-foreground">
        种子 {summary.seed} ·{" "}
        {summary.selected_coverage
          ? "所有已选屏有成功注入"
          : "有已选屏尚无成功注入"}
      </p>
      {summary.screens.map(screen => (
        <div
          key={screen.display_id}
          className="space-y-1 border-t border-border pt-2 text-sm"
        >
          <p>
            屏幕 {screen.display_id} ·{" "}
            {screen.selected
              ? `成功注入 ${screen.injected}`
              : screen.reason || "未选择"}
          </p>
          {Object.entries(screen.actions).map(([action, counts]) => (
            <p key={action} className="text-xs text-muted-foreground">
              {labels[action]} · 计划 {counts.planned ?? 0} · 成功{" "}
              {counts.success ?? 0} · 跳过 {counts.skipped ?? 0} · 失败{" "}
              {counts.failed ?? 0}
            </p>
          ))}
          {Object.entries(screen.reasons).map(([reason, count]) => (
            <p key={reason} className="text-xs text-muted-foreground">
              {reason} × {count}
            </p>
          ))}
        </div>
      ))}
      {summary.failures.slice(-5).map((item, index) => (
        <p key={index} className="text-xs text-destructive">
          {item.display_id == null ? "未归属屏幕" : `屏幕 ${item.display_id}`} ·{" "}
          {item.package} {item.kind || item.detail}
        </p>
      ))}
    </section>
  );
}
