import { useEffect, useState, type FormEvent } from "react";
import { Link } from "wouter";
import { FolderKanban, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { BACKEND_URL } from "@/lib/backend";
import { uiImageUrl, type UiImage, type UiProject } from "@/lib/uiImages";

type Overview = {
  caseCount: number;
  runCount: number;
  states: Record<string, number>;
  recentRuns: { id: string; name: string; state: string; createdAt: number }[];
  imageCount: number;
  images: UiImage[];
  elementCount: number;
  elements: { selector: Record<string, string>; caseName: string }[];
};
const stateLabels: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  error: "错误",
  needs_review: "待复核",
  queued: "排队中",
  running: "运行中",
  starting: "启动中",
  cancelling: "停止中",
  cancelled: "已取消",
  stopped: "已停止",
};
const selectorLabels: Record<string, string> = {
  resourceId: "Resource ID",
  xpath: "XPath",
  label: "文本或描述",
  className: "类名",
  package: "包名",
};

export function UiProjectManagement({
  projects,
  projectId,
  onSelect,
  busy,
  onCreate,
  onDelete,
}: {
  projects: UiProject[];
  projectId: string;
  onSelect: (id: string) => void;
  busy: boolean;
  onCreate: (name: string) => Promise<boolean>;
  onDelete: (id: string) => Promise<boolean>;
}) {
  const [name, setName] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [checkingDelete, setCheckingDelete] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewProject, setOverviewProject] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const project = projects.find(item => item.id === projectId);
  useEffect(() => {
    let cancelled = false;
    let pending = false;
    setOverview(null);
    setOverviewProject("");
    setError("");
    const read = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch(
          `${BACKEND_URL}/api/ui/projects/${encodeURIComponent(projectId)}/overview`
        );
        if (!response.ok) throw Error("项目概览读取失败");
        const data: Overview = await response.json();
        if (!cancelled) {
          setOverview(data);
          setOverviewProject(projectId);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        pending = false;
      }
    };
    void read();
    const timer = window.setInterval(read, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, reload]);
  async function create(event: FormEvent) {
    event.preventDefault();
    if (name.trim() && (await onCreate(name.trim()))) setName("");
  }
  async function prepareDelete() {
    setCheckingDelete(true);
    try {
      const response = await fetch(
        `${BACKEND_URL}/api/ui/projects/${encodeURIComponent(projectId)}/overview`
      );
      if (!response.ok) throw Error("项目资源读取失败，请重试");
      setOverview(await response.json());
      setOverviewProject(projectId);
      setError("");
      setDeleteOpen(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCheckingDelete(false);
    }
  }
  return (
    <>
      <div className="grid min-h-[32rem] gap-4 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]">
        <aside className="min-w-0 rounded-lg border border-border bg-card">
          <div className="border-b border-border px-4 py-3">
            <h2 className="font-semibold">
              项目{" "}
              <span className="text-sm font-normal text-muted-foreground">
                {projects.length}
              </span>
            </h2>
          </div>
          <form
            className="flex gap-2 border-b border-border p-3"
            onSubmit={create}
          >
            <Input
              className="min-w-0"
              aria-label="新项目名称"
              placeholder="项目名称"
              maxLength={100}
              value={name}
              onChange={event => setName(event.target.value)}
            />
            <Button
              type="submit"
              size="sm"
              className="h-9 shrink-0"
              aria-label="新建项目"
              title="新建项目"
              disabled={busy || !name.trim()}
            >
              <Plus className="size-4" />
              新建
            </Button>
          </form>
          <ul className="max-h-[calc(100vh-300px)] overflow-auto p-2">
            {projects.map(item => (
              <li key={item.id}>
                <button
                  type="button"
                  aria-current={item.id === projectId ? "true" : undefined}
                  onClick={() => onSelect(item.id)}
                  className={`flex w-full min-w-0 items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm ${item.id === projectId ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted"}`}
                >
                  <FolderKanban className="size-4 shrink-0" />
                  <span className="truncate">{item.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section className="min-w-0 rounded-lg border border-border bg-card">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
            <h2 className="break-words text-lg font-semibold">
              {project?.name ?? "项目概览"}
            </h2>
            {project && project.id !== "default" && (
              <Button
                variant="outline"
                size="sm"
                disabled={
                  busy ||
                  checkingDelete ||
                  !overview ||
                  overviewProject !== projectId ||
                  !!error
                }
                onClick={() => void prepareDelete()}
              >
                <Trash2 className="size-4" />
                删除项目
              </Button>
            )}
          </div>
          <div className="max-h-[calc(100vh-240px)] space-y-6 overflow-auto p-5">
            {error && (
              <div
                role="alert"
                className="flex items-center gap-3 text-sm text-destructive"
              >
                {error}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setReload(value => value + 1)}
                >
                  重试
                </Button>
              </div>
            )}
            {!overview && !error && (
              <p className="text-sm text-muted-foreground">正在读取项目概览…</p>
            )}
            {overview && (
              <>
                <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                  {[
                    ["用例", overview.caseCount],
                    ["运行记录", overview.runCount],
                    ["图片", overview.imageCount],
                    ["引用的元素", overview.elementCount],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-md border border-border bg-background px-4 py-3"
                    >
                      <div className="text-xs text-muted-foreground">
                        {label}
                      </div>
                      <div className="mt-1 text-2xl font-semibold tabular-nums">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
                <section>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">运行情况</h3>
                    <Link
                      href="/ui?section=reports"
                      className="text-xs text-primary hover:underline"
                    >
                      查看测试报告
                    </Link>
                  </div>
                  {overview.runCount ? (
                    <>
                      <div className="mb-3 flex flex-wrap gap-2">
                        {Object.entries(overview.states).map(
                          ([state, count]) => (
                            <span
                              key={state}
                              className="rounded-md bg-muted px-2 py-1 text-xs"
                            >
                              {stateLabels[state] ?? state} {count}
                            </span>
                          )
                        )}
                      </div>
                      <ul className="divide-y divide-border rounded-md border border-border bg-background">
                        {overview.recentRuns.map(run => (
                          <li
                            key={run.id}
                            className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm"
                          >
                            <span className="min-w-0 flex-1 truncate">
                              {run.name || "未命名用例"}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {stateLabels[run.state] ?? run.state}
                            </span>
                            <time className="text-xs text-muted-foreground">
                              {run.createdAt
                                ? new Date(run.createdAt).toLocaleString()
                                : ""}
                            </time>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      暂无运行记录
                    </p>
                  )}
                </section>
                <section>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">图片资源</h3>
                    <Link
                      href="/ui?section=images"
                      className="text-xs text-primary hover:underline"
                    >
                      查看图片库
                    </Link>
                  </div>
                  {overview.images.length ? (
                    <ul className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-3">
                      {overview.images.map(image => (
                        <li
                          key={image.id}
                          className="min-w-0 rounded-md border border-border bg-background p-2"
                        >
                          <a
                            href={uiImageUrl(projectId, image.id)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <img
                              src={uiImageUrl(projectId, image.id)}
                              alt={image.name}
                              className="h-24 w-full rounded bg-muted object-contain"
                            />
                            <p
                              className="mt-2 truncate text-sm"
                              title={image.name}
                            >
                              {image.name}
                            </p>
                          </a>
                          <p className="text-xs text-muted-foreground">
                            {image.width} × {image.height} px
                          </p>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      暂无图片资源
                    </p>
                  )}
                </section>
                <section>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold">用例引用的元素</h3>
                    <Link
                      href="/ui?section=cases"
                      className="text-xs text-primary hover:underline"
                    >
                      查看用例库
                    </Link>
                  </div>
                  {overview.elements.length ? (
                    <ul className="grid gap-2 md:grid-cols-2">
                      {overview.elements.map((element, index) => (
                        <li
                          key={index}
                          className="min-w-0 rounded-md border border-border bg-background p-3"
                        >
                          <p
                            className="mb-2 truncate text-sm font-medium"
                            title={element.caseName}
                          >
                            {element.caseName}
                          </p>
                          <dl className="space-y-1 text-xs">
                            {Object.entries(element.selector).map(
                              ([key, value]) => (
                                <div
                                  key={key}
                                  className="grid grid-cols-[88px_minmax(0,1fr)] gap-2"
                                >
                                  <dt className="text-muted-foreground">
                                    {selectorLabels[key] ?? key}
                                  </dt>
                                  <dd className="break-all">{value}</dd>
                                </div>
                              )
                            )}
                          </dl>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      用例暂无元素定位条件
                    </p>
                  )}
                </section>
              </>
            )}
          </div>
        </section>
      </div>
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除项目“{project?.name}”？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除项目“{project?.name}”的 {overview?.caseCount ?? 0} 个用例、
              {overview?.imageCount ?? 0} 张图片和 {overview?.runCount ?? 0}{" "}
              条运行记录及其文件。 删除后无法恢复。其他项目引用的图片会保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <Button
              disabled={busy || !project}
              variant="destructive"
              onClick={() => {
                if (project)
                  void onDelete(project.id).then(deleted => {
                    if (deleted) setDeleteOpen(false);
                  });
              }}
            >
              删除项目
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
