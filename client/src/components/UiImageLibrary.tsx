import { useState, type FormEvent } from "react";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { uiImageUrl, type UiImage, type UiProject } from "@/lib/uiImages";

export function UiProjectOptions({ projects }: { projects: UiProject[] }) {
  return (
    <>
      {projects.map(project => (
        <option value={project.id} key={project.id}>
          {project.name}
        </option>
      ))}
    </>
  );
}

export function UiImageLibrary({
  projects,
  projectId,
  onSelect,
  images,
  loading,
  busy,
  onCreate,
}: {
  projects: UiProject[];
  projectId: string;
  onSelect: (id: string) => void;
  images: UiImage[];
  loading: boolean;
  busy: boolean;
  onCreate: (name: string) => Promise<boolean>;
}) {
  const [name, setName] = useState("");
  async function create(event: FormEvent) {
    event.preventDefault();
    if (name.trim() && (await onCreate(name.trim()))) setName("");
  }

  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="grid gap-1 text-sm">
          项目
          <select
            aria-label="图片库项目"
            className="h-9 min-w-40 rounded-md border border-input bg-background px-2"
            value={projectId}
            onChange={event => onSelect(event.target.value)}
          >
            <UiProjectOptions projects={projects} />
          </select>
        </label>
        <form onSubmit={create} className="flex flex-wrap items-center gap-2">
          <Input
            className="w-44"
            aria-label="新项目名称"
            placeholder="新项目名称"
            maxLength={100}
            value={name}
            onChange={event => setName(event.target.value)}
          />
          <Button
            type="submit"
            variant="outline"
            disabled={busy || !name.trim()}
          >
            新建项目
          </Button>
        </form>
      </div>
      {loading ? (
        <p className="text-sm text-muted-foreground">正在读取图片…</p>
      ) : images.length ? (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-3">
          {images.map(item => (
            <li
              key={item.id}
              className="min-w-0 rounded-md border border-border bg-card p-2"
            >
              <a
                href={uiImageUrl(projectId, item.id)}
                target="_blank"
                rel="noreferrer"
                className="block"
              >
                <img
                  loading="lazy"
                  src={uiImageUrl(projectId, item.id)}
                  alt={item.name}
                  className="h-32 w-full rounded bg-muted object-contain"
                />
                <div
                  className="mt-2 truncate text-sm font-medium"
                  title={item.name}
                >
                  {item.name}
                </div>
              </a>
              <div className="text-xs text-muted-foreground">
                {item.width} × {item.height} px ·{" "}
                {
                  { roi: "框选", element: "元素截图", legacy: "既有基准图" }[
                    item.source
                  ]
                }
              </div>
              {item.region && (
                <div className="text-xs text-muted-foreground">
                  ({item.region.left}, {item.region.top}) → ({item.region.right},{" "}
                  {item.region.bottom})
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          当前项目暂无图片。可在{" "}
          <Link href="/ui?section=structure" className="text-primary underline">
            页面与元素
          </Link>{" "}
          中保存截图。
        </p>
      )}
    </section>
  );
}
