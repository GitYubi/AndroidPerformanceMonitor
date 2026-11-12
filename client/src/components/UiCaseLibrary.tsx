import { useEffect, useMemo, useState } from "react";
import { Download, FileText, Folder, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BACKEND_URL } from "@/lib/backend";
import {
  defaultProject,
  uiImageUrl,
  uiImagesPath,
  type UiImage,
  type UiProject,
} from "@/lib/uiImages";
import { stepLayout, stepTypes, type Step } from "@/lib/uiSteps";

export type LibraryCase = {
  id: string;
  name: string;
  version: number;
  projectId?: string;
  group: string;
  tags: string[];
  steps: Step[];
};

type Props<T extends LibraryCase> = {
  cases: T[];
  projects: UiProject[];
  selected: string[];
  busy: boolean;
  onSelect: (ids: string[]) => void;
  onEdit: (test: T) => void;
  onDelete: (test: T) => void;
  onExport: () => void;
};

const labels: Record<string, string> = {
  x: "X",
  y: "Y",
  startX: "起点 X",
  startY: "起点 Y",
  endX: "终点 X",
  endY: "终点 Y",
  durationMs: "时长（毫秒）",
  count: "次数",
  indexVariable: "索引变量",
  variable: "变量",
  operator: "比较方式",
  expectedValue: "比较值",
  expected: "预期状态",
  captureName: "截图名称",
  mode: "截图范围",
  colorMode: "对比颜色",
  minSimilarity: "最低相似度（%）",
  pixelTolerance: "像素容差",
  channel: "通道",
  message: "报文",
  signals: "信号",
  cycleMs: "周期（毫秒）",
  relation: "预期关系",
  valueType: "变量类型",
  booleanValue: "布尔值",
  numberValue: "数值",
  sourceStepId: "来源步骤",
  firstStepId: "第一张截图",
  secondStepId: "第二张截图",
  firstVariable: "第一张图片变量",
  secondVariable: "第二张图片变量",
  roi: "截图坐标",
};
const values: Record<string, string> = {
  eq: "等于",
  ne: "不等于",
  gt: "大于",
  gte: "大于等于",
  lt: "小于",
  lte: "小于等于",
  exists: "存在",
  absent: "不存在",
  screen: "全屏",
  element: "元素",
  roi: "区域",
  grayscale: "灰度",
  color: "彩色",
  same: "相同",
  different: "不同",
  boolean: "布尔",
  number: "数值",
  image: "图片",
};
const selectorNames: Record<string, string> = {
  resourceId: "Resource ID",
  xpath: "XPath",
  label: "文本或描述",
  className: "类名",
  package: "包名",
};

function optionsText(step: Step) {
  return Object.entries(step.options ?? {})
    .filter(
      ([key, value]) =>
        value != null &&
        value !== "" &&
        !["baselineId", "baselineProjectId", "dbcKey"].includes(key)
    )
    .map(
      ([key, value]) =>
        `${labels[key] ?? key} ${typeof value === "object" ? JSON.stringify(value) : (values[String(value)] ?? String(value))}`
    )
    .join(" · ");
}

export function UiCaseLibrary<T extends LibraryCase>({
  cases,
  projects,
  selected,
  busy,
  onSelect,
  onEdit,
  onDelete,
  onExport,
}: Props<T>) {
  const [activeId, setActiveId] = useState("");
  const active = cases.find(test => test.id === activeId);
  const refs = useMemo(() => {
    const images = new Map<
      string,
      { id: string; projectId: string; steps: string[] }
    >();
    const elements = new Map<
      string,
      { selector: NonNullable<Step["selector"]>; steps: string[] }
    >();
    active?.steps.forEach((step, index) => {
      const operation = step.condition ?? step;
      const title =
        step.name?.trim() || `${index + 1}. ${stepTypes[step.type]}`;
      const id = operation.options?.baselineId;
      if (id) {
        const projectId =
          operation.options?.baselineProjectId ?? defaultProject.id;
        const key = `${projectId}/${id}`;
        const entry = images.get(key) ?? { id, projectId, steps: [] };
        entry.steps.push(title);
        images.set(key, entry);
      }
      if (
        operation.selector &&
        Object.values(operation.selector).some(Boolean)
      ) {
        const selector = operation.selector;
        const key = JSON.stringify(
          Object.entries(selector)
            .filter(([, v]) => v)
            .sort()
        );
        const entry = elements.get(key) ?? { selector, steps: [] };
        entry.steps.push(title);
        elements.set(key, entry);
      }
    });
    return {
      images: Array.from(images.values()),
      elements: Array.from(elements.values()),
    };
  }, [active]);
  const [catalogs, setCatalogs] = useState<Record<string, UiImage[]>>({});
  const [catalogError, setCatalogError] = useState(false);
  const projectIds = Array.from(
    new Set(refs.images.map(item => item.projectId))
  )
    .sort()
    .join(",");
  useEffect(() => {
    let cancelled = false;
    setCatalogs({});
    setCatalogError(false);
    if (!projectIds) return;
    void Promise.all(
      projectIds.split(",").map(async id => {
        const response = await fetch(`${BACKEND_URL}/api${uiImagesPath(id)}`);
        if (!response.ok) throw Error("图片信息读取失败");
        return [id, (await response.json()) as UiImage[]] as const;
      })
    )
      .then(items => {
        if (!cancelled) setCatalogs(Object.fromEntries(items));
      })
      .catch(() => {
        if (!cancelled) setCatalogError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectIds]);
  const projectList = [...projects];
  for (const test of cases) {
    const id = test.projectId ?? defaultProject.id;
    if (!projectList.some(project => project.id === id))
      projectList.push({ id, name: `未知项目 (${id})` });
  }
  return (
    <div className="grid min-h-[32rem] gap-4 lg:grid-cols-[minmax(230px,300px)_minmax(0,1fr)]">
      <aside className="min-w-0 rounded-lg border border-border bg-card">
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <h2 className="font-semibold">
            用例库{" "}
            <span className="text-sm font-normal text-muted-foreground">
              {cases.length}
            </span>
          </h2>
          <Button
            variant="ghost"
            size="sm"
            disabled={!selected.length || busy}
            onClick={onExport}
            title="导出选中的用例"
          >
            <Download className="size-4" /> 导出 {selected.length || ""}
          </Button>
        </div>
        <div className="max-h-[calc(100vh-240px)] min-h-64 overflow-auto p-2 text-sm">
          {!cases.length && (
            <p className="px-2 py-6 text-muted-foreground">尚未保存用例</p>
          )}
          {projectList.map(project => {
            const projectCases = cases.filter(
              test => (test.projectId ?? defaultProject.id) === project.id
            );
            if (!projectCases.length) return null;
            const groups = Array.from(
              new Set(projectCases.map(test => test.group.trim()))
            );
            return (
              <details key={project.id}>
                <summary className="cursor-pointer rounded px-2 py-2 font-medium hover:bg-muted">
                  <Folder className="mr-2 inline size-4 text-muted-foreground" />
                  {project.name}{" "}
                  <span className="text-xs text-muted-foreground">
                    {projectCases.length}
                  </span>
                </summary>
                <div className="ml-3 border-l border-border pl-2">
                  {groups.map(group => {
                    const groupCases = projectCases.filter(
                      test => test.group.trim() === group
                    );
                    const tags = Array.from(
                      new Set(
                        groupCases.flatMap(test => {
                          const names = test.tags
                            .map(tag => tag.trim())
                            .filter(Boolean);
                          return names.length ? names : [""];
                        })
                      )
                    );
                    return (
                      <details key={group}>
                        <summary className="cursor-pointer rounded px-2 py-2 hover:bg-muted">
                          {group || "未分组"}{" "}
                          <span className="text-xs text-muted-foreground">
                            {groupCases.length}
                          </span>
                        </summary>
                        <div className="ml-3 border-l border-border pl-2">
                          {tags.map(tag => {
                            const tagged = groupCases.filter(test => {
                              const names = test.tags
                                .map(name => name.trim())
                                .filter(Boolean);
                              return names.length
                                ? names.includes(tag)
                                : tag === "";
                            });
                            return (
                              <details key={tag}>
                                <summary className="cursor-pointer rounded px-2 py-2 text-muted-foreground hover:bg-muted">
                                  {tag || "未标记"}{" "}
                                  <span className="text-xs">
                                    {tagged.length}
                                  </span>
                                </summary>
                                <ul className="ml-2 border-l border-border pl-2">
                                  {tagged.map(test => (
                                    <li
                                      key={test.id}
                                      className={`flex min-w-0 items-center rounded ${activeId === test.id ? "bg-primary/10 text-primary" : "hover:bg-muted"}`}
                                    >
                                      <input
                                        className="ml-2 shrink-0"
                                        aria-label={`选择 ${test.name}`}
                                        type="checkbox"
                                        checked={selected.includes(test.id)}
                                        onChange={event =>
                                          onSelect(
                                            event.target.checked
                                              ? [...selected, test.id]
                                              : selected.filter(
                                                  id => id !== test.id
                                                )
                                          )
                                        }
                                      />
                                      <button
                                        type="button"
                                        className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left"
                                        onClick={() => setActiveId(test.id)}
                                        aria-current={
                                          activeId === test.id
                                            ? "true"
                                            : undefined
                                        }
                                      >
                                        <FileText className="size-4 shrink-0" />
                                        <span
                                          className="truncate"
                                          title={test.name}
                                        >
                                          {test.name}
                                        </span>
                                      </button>
                                    </li>
                                  ))}
                                </ul>
                              </details>
                            );
                          })}
                        </div>
                      </details>
                    );
                  })}
                </div>
              </details>
            );
          })}
        </div>
      </aside>
      <section className="min-w-0 rounded-lg border border-border bg-card">
        {!active ? (
          <div className="flex min-h-80 items-center justify-center text-sm text-muted-foreground">
            从左侧选择用例查看详情
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
              <div className="min-w-0">
                <h2 className="break-words text-lg font-semibold">
                  {active.name}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {projects.find(
                    project =>
                      project.id === (active.projectId ?? defaultProject.id)
                  )?.name ?? "未知项目"}{" "}
                  · {active.group || "未分组"} · {active.steps.length} 步 · v
                  {active.version}
                </p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => onEdit(active)}>
                  <Pencil className="size-4" />
                  编辑
                </Button>
                <Button
                  aria-label={`删除 ${active.name}`}
                  variant="outline"
                  size="icon"
                  disabled={busy}
                  onClick={() => onDelete(active)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
            <div className="max-h-[calc(100vh-240px)] space-y-6 overflow-auto p-5">
              <section>
                <h3 className="mb-3 text-sm font-semibold">
                  用例步骤{" "}
                  <span className="font-normal text-muted-foreground">
                    {active.steps.length}
                  </span>
                </h3>
                <ol className="space-y-2">
                  {stepLayout(active.steps).map(({ step, indent }, index) => {
                    const operation = step.condition ?? step;
                    const summary = optionsText(operation);
                    return (
                      <li
                        key={step.id}
                        style={{ marginLeft: `${Math.min(indent, 6) * 16}px` }}
                        className="rounded-md border border-border bg-background px-3 py-2"
                      >
                        <div className="flex flex-wrap items-center gap-2 text-sm">
                          <span className="text-xs tabular-nums text-muted-foreground">
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className="font-medium">
                            {step.name?.trim() || stepTypes[step.type]}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {step.name?.trim() ? stepTypes[step.type] : ""}
                            {step.condition
                              ? ` · ${stepTypes[step.condition.type]}`
                              : ""}
                            {step.branch === "else" ? " · Else 分支" : ""}
                            {!step.enabled ? " · 已停用" : ""}
                          </span>
                        </div>
                        {operation.prompt &&
                          operation.prompt !== stepTypes[operation.type] && (
                            <p className="mt-1 break-words text-sm text-muted-foreground">
                              {operation.prompt}
                            </p>
                          )}
                        {operation.value && (
                          <p className="mt-1 break-words text-xs text-muted-foreground">
                            输入值 {operation.value}
                          </p>
                        )}
                        {summary && (
                          <p className="mt-1 break-words text-xs leading-relaxed text-muted-foreground">
                            {summary}
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </section>
              <section>
                <h3 className="mb-3 text-sm font-semibold">
                  图片资源{" "}
                  <span className="font-normal text-muted-foreground">
                    {refs.images.length}
                  </span>
                </h3>
                {catalogError && (
                  <p className="mb-2 text-xs text-destructive">
                    图片信息读取失败，请刷新页面重试。
                  </p>
                )}
                {refs.images.length ? (
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3">
                    {refs.images.map(ref => {
                      const item = catalogs[ref.projectId]?.find(
                        image =>
                          image.id === ref.id || image.aliases?.includes(ref.id)
                      );
                      const name = item?.name ?? `基准图 ${ref.id.slice(0, 8)}`;
                      return (
                        <a
                          key={`${ref.projectId}/${ref.id}`}
                          href={uiImageUrl(ref.projectId, ref.id)}
                          target="_blank"
                          rel="noreferrer"
                          className="min-w-0 rounded-md border border-border bg-background p-2"
                        >
                          <img
                            src={uiImageUrl(ref.projectId, ref.id)}
                            alt={name}
                            className="h-28 w-full rounded bg-muted object-contain"
                          />
                          <p
                            className="mt-2 truncate text-sm font-medium"
                            title={name}
                          >
                            {name}
                          </p>
                          <p
                            className="truncate text-xs text-muted-foreground"
                            title={ref.steps.join("、")}
                          >
                            {ref.steps.join("、")}
                          </p>
                          {item && (
                            <p className="text-xs text-muted-foreground">
                              {item.width} × {item.height} px
                            </p>
                          )}
                        </a>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    此用例没有引用图片资源
                  </p>
                )}
              </section>
              <section>
                <h3 className="mb-3 text-sm font-semibold">
                  页面元素{" "}
                  <span className="font-normal text-muted-foreground">
                    {refs.elements.length}
                  </span>
                </h3>
                {refs.elements.length ? (
                  <ul className="grid gap-2 md:grid-cols-2">
                    {refs.elements.map(({ selector, steps }, index) => (
                      <li
                        key={index}
                        className="min-w-0 rounded-md border border-border bg-background p-3"
                      >
                        <p
                          className="mb-2 truncate text-sm font-medium"
                          title={steps.join("、")}
                        >
                          {steps.join("、")}
                        </p>
                        <dl className="space-y-1 text-xs">
                          {Object.entries(selector)
                            .filter(([, value]) => value)
                            .map(([key, value]) => (
                              <div
                                key={key}
                                className="grid grid-cols-[88px_minmax(0,1fr)] gap-2"
                              >
                                <dt className="text-muted-foreground">
                                  {selectorNames[key] ?? key}
                                </dt>
                                <dd className="break-all">{String(value)}</dd>
                              </div>
                            ))}
                        </dl>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    此用例没有引用页面元素
                  </p>
                )}
              </section>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
