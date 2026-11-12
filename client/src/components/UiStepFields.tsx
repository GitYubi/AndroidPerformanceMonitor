import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { BACKEND_URL } from "@/lib/backend";
import {
  changeStepType,
  conditionTypes,
  isCanStep,
  isCapture,
  stepTypes,
  type Selector,
  type Step,
  type StepOptions,
  type StepType,
} from "@/lib/uiSteps";
import type { CanCatalog } from "@/lib/canCatalog";
import type { UiImage } from "@/lib/uiImages";
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
  images: UiImage[];
  projectId: string;
  screenshots: { id: string; label: string }[];
  variables: string[];
  imageVariables: string[];
  onChange: (patch: Partial<Step> | ((current: Step) => Partial<Step>)) => void;
};

export function UiStepFields({
  step,
  index,
  onPickElement,
  pickingElement,
  canCatalog,
  canChannels,
  images,
  projectId,
  screenshots,
  variables,
  imageVariables,
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
      patchOptions(
        step.type === "assign"
          ? { baselineId: data.id, sourceStepId: undefined }
          : { baselineId: data.id }
      );
      toast.success(`已上传图片 ${data.width} × ${data.height}`);
    } catch (error) {
      if (!controller.signal.aborted)
        toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      if (!controller.signal.aborted) setUploading(false);
    }
  }

  function pngField(label: string) {
    return (
      <label className={labelClass}>
        {uploading
          ? "正在上传图片"
          : options.baselineId
            ? "图片已上传，可替换"
            : label}
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
    );
  }

  function selectorFields() {
    const fields: [keyof Selector, string, string][] = [
      ["resourceId", "资源 ID", "com.example:id/title"],
      ["label", "元素标签", "精确匹配文字或描述"],
      ["className", "类名", "android.widget.TextView"],
      ["package", "包名", "com.example"],
      ["xpath", "XPath", "/hierarchy/node[1]"],
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

  function roiFields() {
    return (
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
                const value = event.target.value;
                onChange(current => ({
                  options: {
                    ...current.options,
                    roi: {
                      ...current.options?.roi,
                      [key]: value === "" ? undefined : Number(value),
                    },
                  },
                }));
              }}
            />
          </label>
        ))}
      </div>
    );
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

  if (step.type === "if" && step.condition) {
    const condition = step.condition;
    return (
      <div className="space-y-2">
        <label className={`${labelClass} max-w-64`}>
          判断条件
          <select
            className={selectClass}
            value={condition.type}
            onChange={event => {
              const type = event.target.value as StepType;
              const patch = changeStepType(condition, type);
              onChange({
                condition: {
                  ...condition,
                  ...patch,
                  prompt: type === "assert" ? "" : stepTypes[type],
                },
              });
            }}
          >
            {conditionTypes.map(type => (
              <option key={type} value={type}>
                {stepTypes[type]}
              </option>
            ))}
          </select>
        </label>
        <UiStepFields
          key={condition.type}
          step={condition}
          index={index}
          canCatalog={canCatalog}
          canChannels={canChannels}
          images={images}
          projectId={projectId}
          screenshots={screenshots}
          variables={variables}
          imageVariables={imageVariables}
          onPickElement={onPickElement}
          pickingElement={pickingElement}
          onChange={patch =>
            onChange(current => {
              const existing = current.condition;
              if (!existing || existing.type !== condition.type) return {};
              return {
                condition: {
                  ...existing,
                  ...(typeof patch === "function" ? patch(existing) : patch),
                },
              };
            })
          }
        />
      </div>
    );
  }
  if (step.type === "assign") {
    const type = options.valueType ?? "boolean";
    return (
      <div className="grid grid-cols-2 gap-2 @min-[510px]:grid-cols-4">
        <label className={labelClass}>
          变量名称
          <Input
            className={inputClass}
            maxLength={64}
            value={options.variable ?? ""}
            placeholder="例如 result"
            onChange={event => patchOptions({ variable: event.target.value })}
          />
        </label>
        <label className={labelClass}>
          值类型
          <select
            className={selectClass}
            value={type}
            onChange={event => {
              const valueType = event.target.value as StepOptions["valueType"];
              upload.current?.abort();
              setUploading(false);
              onChange({
                options: {
                  variable: options.variable,
                  valueType,
                  ...(valueType === "boolean" ? { booleanValue: false } : {}),
                },
              });
            }}
          >
            <option value="boolean">布尔值</option>
            <option value="number">数值</option>
            <option value="image">图片</option>
          </select>
        </label>
        {type === "boolean" && (
          <label className={labelClass}>
            布尔值
            <select
              className={selectClass}
              value={String(options.booleanValue ?? false)}
              onChange={event =>
                patchOptions({ booleanValue: event.target.value === "true" })
              }
            >
              <option value="true">True（真）</option>
              <option value="false">False（假）</option>
            </select>
          </label>
        )}
        {type === "number" && (
          <label className={labelClass}>
            数值
            <Input
              className={inputClass}
              type="number"
              step="any"
              value={options.numberValue ?? ""}
              onChange={event =>
                patchOptions({
                  numberValue:
                    event.target.value === ""
                      ? undefined
                      : event.target.valueAsNumber,
                })
              }
            />
          </label>
        )}
        {type === "image" && (
          <>
            <label className={labelClass}>
              从截图取图
              <select
                className={selectClass}
                value={options.sourceStepId ?? ""}
                disabled={uploading}
                onChange={event =>
                  patchOptions({
                    sourceStepId: event.target.value,
                    baselineId: undefined,
                  })
                }
              >
                <option value="" disabled>
                  {screenshots.length ? "选择截图步骤" : "暂无可用截图"}
                </option>
                {screenshots.map(item => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            {pngField("或上传 PNG")}
          </>
        )}
      </div>
    );
  }
  if (step.type === "for") {
    return (
      <div className="grid grid-cols-2 gap-2">
        {numberField("count", "循环次数", 100, 1)}
        <label className={labelClass}>
          索引变量（可选，从 1 开始）
          <Input
            className={inputClass}
            maxLength={64}
            placeholder="例如 index"
            value={options.indexVariable ?? ""}
            onChange={event =>
              patchOptions({ indexVariable: event.target.value || null })
            }
          />
        </label>
      </div>
    );
  }
  if (step.type === "variable_assert") {
    return (
      <div className="grid grid-cols-2 gap-2 @min-[450px]:grid-cols-3">
        <label className={labelClass}>
          变量
          <select
            className={selectClass}
            value={options.variable ?? ""}
            onChange={event => patchOptions({ variable: event.target.value })}
          >
            <option value="" disabled>
              选择变量
            </option>
            {options.variable && !variables.includes(options.variable) && (
              <option value={options.variable} disabled>
                {options.variable}（不可用）
              </option>
            )}
            {variables.map(name => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className={labelClass}>
          比较方式
          <select
            className={selectClass}
            value={options.operator ?? "eq"}
            onChange={event =>
              patchOptions({
                operator: event.target.value as StepOptions["operator"],
              })
            }
          >
            {[
              ["eq", "等于"],
              ["ne", "不等于"],
              ["gt", "大于（数值）"],
              ["gte", "大于等于（数值）"],
              ["lt", "小于（数值）"],
              ["lte", "小于等于（数值）"],
            ].map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className={labelClass}>
          比较值
          <Input
            className={inputClass}
            value={options.expectedValue ?? ""}
            placeholder={"固定值或 ${变量名}"}
            onChange={event =>
              patchOptions({ expectedValue: event.target.value })
            }
          />
        </label>
      </div>
    );
  }
  if (isCanStep(step.type)) {
    return (
      <UiCanStepFields
        step={step}
        catalog={canCatalog}
        channels={canChannels}
        onChange={patchOptions}
      />
    );
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
          value={
            options.durationMs === undefined ? "" : options.durationMs / 1000
          }
          onChange={event =>
            patchOptions({
              durationMs:
                event.target.value === ""
                  ? undefined
                  : Math.round(event.target.valueAsNumber * 1000),
            })
          }
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
    const selected = images.find(
      item =>
        item.id === options.baselineId &&
        (item.source === "legacy"
          ? !options.baselineProjectId
          : options.baselineProjectId === projectId)
    );
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
                    roi:
                      selected === "roi"
                        ? (images.find(image => image.id === options.baselineId)
                            ?.region ?? {})
                        : undefined,
                  },
                  selector: selected === "element" ? {} : null,
                }));
              }}
            >
              <option value="screen">全屏搜索</option>
              <option value="element">元素区域断言</option>
              <option value="roi">ROI 截图断言</option>
            </select>
          </label>
          <label className={labelClass}>
            基准图（项目图片库）
            <select
              className={selectClass}
              value={selected?.id ?? (options.baselineId ? "__bound__" : "")}
              onChange={event => {
                const item = images.find(
                  image => image.id === event.target.value
                );
                patchOptions({
                  baselineId: item?.id ?? null,
                  baselineProjectId:
                    item && item.source !== "legacy" ? projectId : null,
                  ...(mode === "roi" ? { roi: item?.region ?? {} } : {}),
                });
              }}
            >
              <option value="">
                {images.length ? "选择基准图" : "当前项目暂无基准图"}
              </option>
              {options.baselineId && !selected && (
                <option value="__bound__" disabled>
                  沿用已绑定的基准图
                </option>
              )}
              {images.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.width} × {item.height}
                </option>
              ))}
            </select>
          </label>
          {numberField("minSimilarity", "最低相似度（%）", 100, 0, 0.1)}
          {numberField("pixelTolerance", "像素容差（0–255）", 255)}
          <label className={labelClass}>
            对比颜色
            <select
              className={selectClass}
              value={options.colorMode ?? "grayscale"}
              onChange={event =>
                patchOptions({
                  colorMode: event.target.value as "grayscale" | "color",
                })
              }
            >
              <option value="grayscale">灰度图</option>
              <option value="color">彩色图</option>
            </select>
          </label>
        </div>
        {mode === "element" && (
          <>
            {pickButton}
            <div className="grid grid-cols-2 gap-2 @min-[550px]:grid-cols-4">
              {selectorFields()}
            </div>
          </>
        )}
        {mode === "roi" && roiFields()}
        <p className="text-xs text-muted-foreground">
          {mode === "screen" &&
            "在整张截图内搜索基准图，达到相似度阈值即通过。"}
          {mode === "element" && "元素截图需要与基准图尺寸一致。"}
          {mode === "roi" &&
            "使用基准图保存时的坐标，可手动调整。截图范围须与基准图尺寸一致。"}
        </p>
      </div>
    );
  }
  if (isCapture(step.type)) {
    const element = step.type === "element_screenshot";
    return (
      <div className="space-y-2">
        <div className="grid gap-2 @min-[420px]:grid-cols-3">
          <label className={labelClass}>
            截图名称
            <Input
              className={inputClass}
              maxLength={100}
              value={options.captureName ?? ""}
              placeholder="例如 图片1"
              onChange={event =>
                patchOptions({ captureName: event.target.value })
              }
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
          {element ? (
            <div className="flex items-end">{pickButton}</div>
          ) : (
            <label className={labelClass}>
              截图范围
              <select
                className={selectClass}
                value={options.mode ?? "screen"}
                onChange={event => {
                  const mode = event.target.value === "roi" ? "roi" : "screen";
                  patchOptions({ mode, roi: mode === "roi" ? {} : undefined });
                }}
              >
                <option value="screen">全屏截图</option>
                <option value="roi">ROI 截图</option>
              </select>
            </label>
          )}
        </div>
        {element && (
          <div className="grid grid-cols-2 gap-2 @min-[550px]:grid-cols-4">
            {selectorFields()}
          </div>
        )}
        {!element && options.mode === "roi" && roiFields()}
      </div>
    );
  }
  if (step.type === "screenshot_assert") {
    const first = options.firstVariable
      ? `var:${options.firstVariable}`
      : options.firstStepId;
    const second = options.secondVariable
      ? `var:${options.secondVariable}`
      : options.secondStepId;
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2 @min-[640px]:grid-cols-[minmax(0,1.4fr)_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)]">
          {(
            [
              ["firstStepId", "firstVariable", "第一张图片", first, second],
              ["secondStepId", "secondVariable", "第二张图片", second, first],
            ] as const
          ).map(([key, variableKey, label, selected, other]) => {
            const available =
              screenshots.some(item => item.id === selected) ||
              imageVariables.some(name => `var:${name}` === selected);
            return (
              <label className={labelClass} key={key}>
                {label}
                <select
                  className={selectClass}
                  value={available ? (selected ?? "") : ""}
                  onChange={event => {
                    const value = event.target.value;
                    const variable = value.startsWith("var:");
                    patchOptions({
                      [key]: variable ? undefined : value,
                      [variableKey]: variable ? value.slice(4) : undefined,
                    });
                  }}
                >
                  <option value="" disabled>
                    {selected ? "图片来源不可用，请重新选择" : "选择图片来源"}
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
                  {imageVariables.length > 0 && (
                    <optgroup label="图片变量">
                      {imageVariables.map(name => (
                        <option
                          key={name}
                          value={`var:${name}`}
                          disabled={
                            `var:${name}` === other &&
                            `var:${name}` !== selected
                          }
                        >
                          {name}
                        </option>
                      ))}
                    </optgroup>
                  )}
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
                patchOptions({
                  relation: event.target.value as StepOptions["relation"],
                })
              }
            >
              <option value="same">相同</option>
              <option value="different">不同</option>
            </select>
          </label>
          {numberField("minSimilarity", "相同阈值（%）", 100, 0, 0.1)}
          {numberField("pixelTolerance", "像素容差（0–255）", 255)}
        </div>
        {screenshots.length + imageVariables.length < 2 && (
          <p className="text-xs text-muted-foreground">
            请先添加两张截图或为图片变量赋值。
          </p>
        )}
      </div>
    );
  }
  if (step.type === "settings" || step.type === "back" || step.type === "else")
    return null;

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
