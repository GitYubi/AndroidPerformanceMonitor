import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { BACKEND_URL } from "@/lib/backend";
import { isCanStep, type Selector, type Step, type StepOptions } from "@/lib/uiSteps";
import type { CanCatalog } from "@/lib/canCatalog";
import { UiCanStepFields } from "@/components/UiCanStepFields";

const inputClass = "h-8 px-2 shadow-none";
const labelClass = "grid min-w-0 gap-1 text-xs text-muted-foreground";
const selectClass =
  "h-8 min-w-0 rounded-md border border-input bg-background px-2 text-sm text-foreground";

type Props = {
  step: Step;
  index: number;
  onPickElement?: () => void;
  pickingElement?: boolean;
  canCatalog: CanCatalog | null;
  canChannels: number[];
  screenshots: { id: string; label: string }[];
  onChange: (patch: Partial<Step> | ((current: Step) => Partial<Step>)) => void;
};

export function UiStepFields({
  step,
  index,
  onPickElement,
  pickingElement,
  canCatalog,
  canChannels,
  screenshots,
  onChange,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const upload = useRef<AbortController>(null);
  useEffect(() => () => upload.current?.abort(), []);
  const options = step.options ?? {};
  const patchOptions = (patch: StepOptions) =>
    onChange(current => ({ options: { ...current.options, ...patch } }));
  function numberField(
    key: keyof StepOptions,
    label: string,
    max: number,
    min = 0,
    increment = 1
  ) {
    const value = options[key];
    return (
      <label key={key} className={labelClass}>
        {label}
        <Input
          className={inputClass}
          type="number"
          min={min}
          max={max}
          step={increment}
          value={typeof value === "number" ? value : ""}
          onChange={event =>
            patchOptions({
              [key]:
                event.target.value === ""
                  ? undefined
                  : Number(event.target.value),
            })
          }
        />
      </label>
    );
  }
  async function uploadBaseline(file: File) {
    const controller = new AbortController();
    upload.current = controller;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${BACKEND_URL}/api/ui/baselines`, {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === "string" ? data.detail : "基准图上传失败"
        );
      if (controller.signal.aborted) return;
      patchOptions({ baselineId: data.id });
      toast.success(`已上传基准图 ${data.width} × ${data.height}`);
    } catch (error) {
      if (!controller.signal.aborted)
        toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      if (!controller.signal.aborted) setUploading(false);
    }
  }

  function selectorFields() {
    const fields: [keyof Selector, string, string][] = [
      ["resourceId", "资源 ID", "com.example:id/title"],
      ["label", "元素标签", "精确匹配文字或描述"],
      ["className", "类名", "android.widget.TextView"],
      ["package", "包名", "com.example"],
    ];
    return fields.map(([key, label, placeholder]) => (
      <label className={labelClass} key={key}>
        {label}
        <Input
          className={inputClass}
          placeholder={placeholder}
          value={step.selector?.[key] ?? ""}
          onChange={event =>
            onChange({
              selector: { ...step.selector, [key]: event.target.value },
            })
          }
        />
      </label>
    ));
  }

  const pickButton = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      disabled={!onPickElement}
      onClick={onPickElement}
    >
      {pickingElement ? "在右侧选取元素" : "从投屏选元素"}
    </Button>
  );

  if (isCanStep(step.type)) {
    return <UiCanStepFields step={step} catalog={canCatalog} channels={canChannels} onChange={patchOptions} />;
  }
  if (step.type === "delay") {
    return (
      <label className={`${labelClass} w-40`}>
        等待时长（秒）
        <Input
          className={inputClass}
          type="number"
          min={0.001}
          max={300}
          step={0.001}
          value={options.durationMs === undefined ? "" : options.durationMs / 1000}
          onChange={event => patchOptions({
            durationMs: event.target.value === ""
              ? undefined
              : Math.round(event.target.valueAsNumber * 1000),
          })}
        />
      </label>
    );
  }
  if (step.type === "adb_tap" || step.type === "adb_swipe") {
    const tap = step.type === "adb_tap";
    return (
      <div className="grid grid-cols-2 gap-2 @min-[550px]:grid-cols-5">
        {tap ? (
          <>
            {numberField("x", "X（像素）", 32767)}
            {numberField("y", "Y（像素）", 32767)}
          </>
        ) : (
          <>
            {numberField("startX", "起点 X", 32767)}
            {numberField("startY", "起点 Y", 32767)}
            {numberField("endX", "终点 X", 32767)}
            {numberField("endY", "终点 Y", 32767)}
            {numberField("durationMs", "时长（ms）", 10000, 1)}
          </>
        )}
      </div>
    );
  }
  if (
    step.type === "element_assert" ||
    (step.type === "tap" && step.selector)
  ) {
    return (
      <div className="space-y-2">
        {pickButton}
        <div className="grid grid-cols-2 gap-2 @min-[550px]:grid-cols-5">
          {selectorFields()}
          {step.type === "element_assert" && (
            <label className={labelClass}>
              预期状态
              <select
                className={selectClass}
                value={options.expected ?? "exists"}
                onChange={event =>
                  patchOptions({ expected: event.target.value })
                }
              >
                {[
                  ["exists", "存在"],
                  ["absent", "不存在"],
                  ["enabled", "可用"],
                  ["disabled", "禁用"],
                  ["checked", "已勾选"],
                  ["unchecked", "未勾选"],
                ].map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>
    );
  }
  if (step.type === "image_assert") {
    const mode = options.mode ?? "screen";
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2 @min-[550px]:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <label className={labelClass}>
            截图范围
            <select
              className={selectClass}
              value={mode}
              onChange={event => {
                const selected = event.target.value as NonNullable<
                  StepOptions["mode"]
                >;
                onChange(current => ({
                  options: {
                    ...current.options,
                    mode: selected,
                    roi: selected === "roi" ? {} : undefined,
                  },
                  selector: selected === "element" ? {} : null,
                }));
              }}
            >
              <option value="screen">全局断言</option>
              <option value="element">元素区域断言</option>
              <option value="roi">ROI 截图断言</option>
            </select>
          </label>
          <label className={labelClass}>
            {uploading
              ? "正在上传基准图"
              : options.baselineId
                ? "基准图已上传，可替换"
                : "基准图（PNG）"}
            <Input
              className={inputClass}
              type="file"
              accept="image/png,.png"
              disabled={uploading}
              onChange={event => {
                const file = event.target.files?.[0];
                if (file) void uploadBaseline(file);
                event.target.value = "";
              }}
            />
          </label>
          {numberField("minSimilarity", "最低相似度（%）", 100, 0, 0.1)}
          {numberField("pixelTolerance", "像素容差（0–255）", 255)}
        </div>
        {mode === "element" && (
          <>
            {pickButton}
            <div className="grid grid-cols-2 gap-2 @min-[550px]:grid-cols-4">
              {selectorFields()}
            </div>
          </>
        )}
        {mode === "roi" && (
          <div className="grid grid-cols-2 gap-2 @min-[440px]:grid-cols-4">
            {(
              [
                ["left", "左上角 X"],
                ["top", "左上角 Y"],
                ["right", "右下角 X"],
                ["bottom", "右下角 Y"],
              ] as const
            ).map(([key, label]) => (
              <label key={key} className={labelClass}>
                {label}（像素）
                <Input
                  className={inputClass}
                  type="number"
                  min={0}
                  max={32767}
                  step={1}
                  value={options.roi?.[key] ?? ""}
                  onChange={event => {
                    const text = event.target.value;
                    onChange(current => ({
                      options: {
                        ...current.options,
                        roi: {
                          ...current.options?.roi,
                          [key]: text === "" ? undefined : Number(text),
                        },
                      },
                    }));
                  }}
                />
              </label>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          基准 PNG 应与所选截图范围尺寸一致。
          {mode === "roi" &&
            "坐标以原始设备截图左上角为原点，右下角为裁剪边界。"}
        </p>
      </div>
    );
  }
  if (step.type === "screenshot") {
    return (
      <div className="grid gap-2 @min-[420px]:grid-cols-2">
        <label className={labelClass}>
          截图名称
          <Input
            className={inputClass}
            maxLength={100}
            value={options.captureName ?? ""}
            placeholder="例如 图片1"
            onChange={event => patchOptions({ captureName: event.target.value })}
          />
        </label>
        <label className={labelClass}>
          截图描述
          <Textarea
            className="h-8 min-h-8 resize-y field-sizing-fixed px-2 py-1.5 leading-4 shadow-none"
            aria-label={`步骤 ${index + 1} 描述`}
            value={step.prompt}
            onChange={event => onChange({ prompt: event.target.value })}
            placeholder="例如登录后的主页面"
          />
        </label>
      </div>
    );
  }
  if (step.type === "screenshot_assert") {
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2 @min-[640px]:grid-cols-[minmax(0,1.4fr)_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)]">
          {([
            ["firstStepId", "第一张截图", options.secondStepId],
            ["secondStepId", "第二张截图", options.firstStepId],
          ] as const).map(([key, label, other]) => {
            const selected = options[key];
            const available = screenshots.some(item => item.id === selected);
            return (
              <label className={labelClass} key={key}>
                {label}
                <select
                  className={selectClass}
                  value={available ? selected : ""}
                  onChange={event => patchOptions({ [key]: event.target.value })}
                >
                  <option value="" disabled>
                    {selected ? "截图不可用，请重新选择" : "选择截图"}
                  </option>
                  {screenshots.map(item => (
                    <option
                      key={item.id}
                      value={item.id}
                      disabled={item.id === other && item.id !== selected}
                    >
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            );
          })}
          <label className={labelClass}>
            预期结果
            <select
              className={selectClass}
              value={options.relation ?? "same"}
              onChange={event =>
                patchOptions({ relation: event.target.value as StepOptions["relation"] })
              }
            >
              <option value="same">相同</option>
              <option value="different">不同</option>
            </select>
          </label>
          {numberField("minSimilarity", "相同阈值（%）", 100, 0, 0.1)}
          {numberField("pixelTolerance", "像素容差（0–255）", 255)}
        </div>
        {screenshots.length < 2 && (
          <p className="text-xs text-muted-foreground">
            请先在此步骤之前添加并启用两条 ADB 截图步骤。
          </p>
        )}
      </div>
    );
  }
  if (step.type === "settings" || step.type === "back") return null;

  const placeholders: Partial<Record<Step["type"], string>> = {
    tap: "描述要点击的控件，例如右上角设置图标",
    input: "描述目标输入框",
    wait: "描述要等待的界面条件",
    assert: "描述应成立的条件，例如 Wi-Fi 显示已连接",
    explore: "填写当前页面名称",
    scan: "填写待扫描页面的名称",
  };
  const scope = step.exploration;
  return (
    <>
      <div
        className={
          step.type === "input"
            ? "grid items-start gap-1.5 @min-[420px]:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]"
            : undefined
        }
      >
        <Textarea
          className="h-9 min-h-9 resize-y field-sizing-fixed px-2 py-1.5 leading-4 shadow-none"
          aria-label={`步骤 ${index + 1} 描述`}
          value={step.prompt}
          onChange={event => onChange({ prompt: event.target.value })}
          placeholder={placeholders[step.type] ?? "描述操作目标"}
        />
        {step.type === "input" && (
          <Input
            className={inputClass}
            aria-label={`步骤 ${index + 1} 输入内容`}
            placeholder={"输入值，例如 ${PASSWORD}"}
            value={step.value}
            onChange={event => onChange({ value: event.target.value })}
          />
        )}
      </div>
      {(step.type === "explore" || step.type === "scan") && (
        <div className="grid gap-1.5 @min-[440px]:grid-cols-[minmax(0,1fr)_auto]">
          <label className={labelClass}>
            {step.type === "explore"
              ? "允许探索的开关（逗号分隔）"
              : "允许进入的页面（逗号分隔）"}
            <Input
              className={inputClass}
              value={
                (step.type === "explore"
                  ? scope?.allowedControls
                  : scope?.allowedNavigation
                )?.join(",") ?? ""
              }
              onChange={event => {
                const items = event.target.value
                  .split(",")
                  .map(value => value.trim());
                const key =
                  step.type === "explore"
                    ? "allowedControls"
                    : "allowedNavigation";
                onChange({
                  exploration: {
                    allowedControls: [],
                    maxActions: 6,
                    ...scope,
                    [key]: items,
                  },
                });
              }}
            />
          </label>
          <label className={labelClass}>
            最大点击次数（含恢复）
            <Input
              className={`${inputClass} w-24`}
              type="number"
              min={2}
              max={100}
              value={scope?.maxActions ?? 6}
              onChange={event =>
                onChange({
                  exploration: {
                    allowedControls: [],
                    ...scope,
                    maxActions: Number(event.target.value),
                  },
                })
              }
            />
          </label>
        </div>
      )}
    </>
  );
}
