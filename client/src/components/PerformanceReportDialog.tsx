import { useState, type ReactElement } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useTheme } from "@/contexts/ThemeContext";

const reportOptions = [
  { id: "cpu", label: "CPU 占用及进程排行", source: "cpu" },
  { id: "memory", label: "系统内存占用率", source: "memory" },
  { id: "process_memory", label: "进程内存（PSS、USS）", source: "memory" },
  { id: "fps", label: "呈现与渲染帧率（FPS）", source: "fps" },
  { id: "frames", label: "卡顿率及帧耗时", source: "fps" },
  { id: "events", label: "会话事件", source: null },
] as const;

type Metric = (typeof reportOptions)[number]["id"];
type Source = NonNullable<(typeof reportOptions)[number]["source"]>;

export function PerformanceReportDialog({
  url,
  enabledMetrics,
  children,
}: {
  url: string;
  enabledMetrics?: Partial<Record<Source, boolean>>;
  children: ReactElement;
}) {
  const { theme } = useTheme();
  const [selected, setSelected] = useState<Metric[]>([]);
  const available = reportOptions.filter(
    option => option.source === null || enabledMetrics?.[option.source] !== false
  );
  const included = available.filter(option => selected.includes(option.id));

  function reportUrl(download: boolean) {
    const params = new URLSearchParams({ theme });
    for (const { id } of included) params.append("metrics", id);
    if (download) params.set("download", "1");
    return `${url}?${params}`;
  }

  return (
    <Dialog onOpenChange={open => open && setSelected(available.map(option => option.id))}>
      <DialogTrigger asChild>{children}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>性能报告导出选项</DialogTitle>
          <DialogDescription>
            勾选要纳入报告的内容。打开预览后，可将同一份报告保存为 PNG。
          </DialogDescription>
        </DialogHeader>
        <fieldset className="grid gap-2">
          <legend className="sr-only">报告内容</legend>
          {available.map(({ id, label }) => (
            <label key={id} className="flex items-center gap-3 rounded-md border px-3 py-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={selected.includes(id)}
                onChange={event => {
                  const checked = event.target.checked;
                  setSelected(previous => checked ? [...previous, id] : previous.filter(item => item !== id));
                }}
              />
              {label}
            </label>
          ))}
        </fieldset>
        <DialogFooter>
          {included.length ? (
            <>
              <Button asChild variant="outline">
                <a href={reportUrl(true)}>下载 HTML</a>
              </Button>
              <Button asChild>
                <a href={reportUrl(false)} target="_blank" rel="noopener noreferrer">生成预览</a>
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" disabled>下载 HTML</Button>
              <Button disabled>生成预览</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
