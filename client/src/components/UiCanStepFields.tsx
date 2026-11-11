import { useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { CanCatalog } from "@/lib/canCatalog";
import type { Step, StepOptions } from "@/lib/uiSteps";

const labelClass = "grid min-w-0 gap-1 text-xs text-muted-foreground";
const selectClass =
  "h-8 min-w-0 rounded-md border border-input bg-background px-2 text-sm text-foreground";

function CanSearchSelect({
  label,
  text,
  placeholder,
  items,
  onSelect,
}: {
  label: string;
  text: string;
  placeholder: string;
  items: { name: string; detail?: string; keywords?: string[] }[];
  onSelect: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  function changeOpen(value: boolean) {
    setOpen(value);
    setSearch("");
  }
  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          aria-label={label}
          className="h-8 min-w-0 justify-between gap-2 px-2 text-sm font-normal shadow-none"
        >
          <span className="truncate">{text}</span>
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[max(16rem,var(--radix-popover-trigger-width))] max-w-[var(--radix-popover-content-available-width)] p-0"
      >
        <Command
          label={label}
          filter={(name, query, keywords = []) =>
            Number(
              [name, ...keywords].some(value =>
                value.toLowerCase().includes(query.trim().toLowerCase())
              )
            )
          }
        >
          <CommandInput
            aria-label={placeholder}
            placeholder={placeholder}
            value={search}
            onValueChange={setSearch}
          />
          <CommandList className="max-h-56">
            <CommandEmpty>没有匹配项</CommandEmpty>
            {items.map(item => (
              <CommandItem
                key={item.name}
                value={item.name}
                keywords={item.keywords}
                onSelect={() => {
                  onSelect(item.name);
                  changeOpen(false);
                }}
              >
                <span className="min-w-0 break-all">{item.name}</span>
                {item.detail && (
                  <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">
                    {item.detail}
                  </span>
                )}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

type Props = {
  step: Step;
  catalog: CanCatalog | null;
  channels: number[];
  onChange: (patch: StepOptions) => void;
};

export function UiCanStepFields({ step, catalog, channels, onChange }: Props) {
  const options = step.options ?? {};
  const signals = options.signals ?? {};
  const message =
    catalog?.key === options.dbcKey
      ? catalog?.messages.find(item => item.name === options.message)
      : undefined;
  const canStop = step.type === "can_stop";
  const canLoop = step.type === "can_send_loop";
  const channel = options.channel ?? channels[0] ?? 0;
  const available =
    message?.signals.filter(signal => !Object.hasOwn(signals, signal.name)) ??
    [];
  return (
    <div className="space-y-2">
      {!catalog?.key ? (
        <p className="text-xs text-muted-foreground">
          请先在总线控制的「DBC 发送」页面打开 DBC。
        </p>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 @min-[520px]:flex">
          <div className={`${labelClass} flex-1`}>
            <span>报文</span>
            <CanSearchSelect
              label="选择报文"
              text={
                message?.name ??
                (options.message
                  ? `重新选择 ${options.message}`
                  : "选择 DBC 报文")
              }
              placeholder="搜索报文名或 ID"
              items={catalog.messages.map(item => {
                const id = `0x${item.id.toString(16).toUpperCase()}`;
                return {
                  name: item.name,
                  detail: id,
                  keywords: [id, String(item.id)],
                };
              })}
              onSelect={name => {
                if (name === message?.name) return;
                onChange({
                  dbcKey: catalog.key,
                  message: name,
                  channel,
                  ...(canStop ? {} : { signals: {} }),
                  ...(canLoop ? { cycleMs: null } : {}),
                });
              }}
            />
          </div>
          <label className={labelClass}>
            通道
            <select
              className={selectClass}
              value={channel}
              onChange={event =>
                onChange({ channel: Number(event.target.value) })
              }
            >
              {[0, 1].map(item => (
                <option key={item} value={item}>
                  通道 {item}
                  {channels.includes(item) ? "" : "（未打开）"}
                </option>
              ))}
            </select>
          </label>
          {canLoop && (
            <label className={`${labelClass} w-28`}>
              周期（ms）
              <Input
                className="h-8 px-2 shadow-none"
                type="number"
                min={1}
                step={1}
                value={options.cycleMs ?? ""}
                placeholder={String(message?.cycle_time ?? 100)}
                onChange={event =>
                  onChange({
                    cycleMs:
                      event.target.value === ""
                        ? null
                        : event.target.valueAsNumber,
                  })
                }
              />
            </label>
          )}
        </div>
      )}
      {message && !canStop && (
        <div className="space-y-1.5">
          {message.signals
            .filter(signal => Object.hasOwn(signals, signal.name))
            .map(signal => {
              const value = signals[signal.name];
              return (
                <div
                  key={signal.name}
                  className="flex flex-wrap items-center gap-2"
                >
                  <span
                    className="min-w-24 flex-1 break-all font-mono text-xs"
                    title={signal.comment ?? undefined}
                  >
                    {signal.name}
                  </span>
                  {signal.choices.length > 0 && (
                    <select
                      aria-label={`${signal.name} 枚举值`}
                      className={`${selectClass} max-w-52`}
                      value={
                        value && typeof value === "object" ? value.raw : ""
                      }
                      onChange={event =>
                        onChange({
                          signals: {
                            ...signals,
                            [signal.name]:
                              event.target.value === ""
                                ? null
                                : { raw: Number(event.target.value) },
                          },
                        })
                      }
                    >
                      <option value="">自定义物理值</option>
                      {signal.choices.map(choice => (
                        <option key={choice.raw} value={choice.raw}>
                          {choice.raw} · {choice.label}
                        </option>
                      ))}
                    </select>
                  )}
                  <Input
                    aria-label={`${signal.name} 物理值`}
                    className="h-8 w-28 px-2 shadow-none"
                    type="number"
                    step="any"
                    min={signal.minimum ?? undefined}
                    max={signal.maximum ?? undefined}
                    value={typeof value === "number" ? value : ""}
                    placeholder={signal.unit || "物理值"}
                    disabled={value != null && typeof value === "object"}
                    onChange={event =>
                      onChange({
                        signals: {
                          ...signals,
                          [signal.name]:
                            event.target.value === ""
                              ? null
                              : event.target.valueAsNumber,
                        },
                      })
                    }
                  />
                  <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    aria-label={`移除信号 ${signal.name}`}
                    onClick={() => {
                      const next = { ...signals };
                      delete next[signal.name];
                      onChange({ signals: next });
                    }}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              );
            })}
          {available.length > 0 && (
            <CanSearchSelect
              label="添加信号"
              text="添加信号"
              placeholder="搜索信号名"
              items={available}
              onSelect={name =>
                onChange({ signals: { ...signals, [name]: null } })
              }
            />
          )}
        </div>
      )}
      {canLoop && message && (
        <p className="text-xs text-muted-foreground">
          循环发送会在停止步骤或用例结束时停止。
        </p>
      )}
    </div>
  );
}
