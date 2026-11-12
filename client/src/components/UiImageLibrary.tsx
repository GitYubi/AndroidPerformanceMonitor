import { Link } from "wouter";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { BACKEND_URL } from "@/lib/backend";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  uiImagesPath,
  uiImageUrl,
  type UiImage,
  type UiProject,
} from "@/lib/uiImages";

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
  onDeleted,
}: {
  projects: UiProject[];
  projectId: string;
  onSelect: (id: string) => void;
  images: UiImage[];
  loading: boolean;
  onDeleted: (projectId: string, imageId: string) => void;
}) {
  const [target, setTarget] = useState<{
    projectId: string;
    image: UiImage;
    cases: { id: string; name: string }[];
  } | null>(null);
  const [busy, setBusy] = useState(false);
  async function request(path: string, image: UiImage) {
    const response = await fetch(`${BACKEND_URL}/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: image.source === "legacy" ? "legacy" : "library",
      }),
    });
    const result = await response.json();
    if (!response.ok)
      throw Error(
        typeof result.detail === "string" ? result.detail : "图片操作失败"
      );
    return result;
  }
  async function prepareDelete(image: UiImage) {
    setBusy(true);
    try {
      const result = await request(
        `${uiImagesPath(projectId)}/${encodeURIComponent(image.id)}/references`,
        image
      );
      setTarget({ projectId, image, cases: result.cases });
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function deleteImage() {
    if (!target) return;
    setBusy(true);
    try {
      const result = await request(
        `${uiImagesPath(target.projectId)}/${encodeURIComponent(target.image.id)}/delete`,
        target.image
      );
      onDeleted(target.projectId, target.image.id);
      setTarget(null);
      if (result.cleanupErrors?.length) {
        toast.error(
          `图片已删除，部分文件清理失败：${result.cleanupErrors.join("、")}`
        );
      } else {
        toast.success("图片已删除");
      }
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
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
            disabled={busy}
            onChange={event => onSelect(event.target.value)}
          >
            <UiProjectOptions projects={projects} />
          </select>
        </label>
        <Link
          href="/ui?section=projects"
          className="text-sm text-primary hover:underline"
        >
          项目管理
        </Link>
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
                  ({item.region.left}, {item.region.top}) → ({item.region.right}
                  , {item.region.bottom})
                </div>
              )}
              <div className="mt-2 flex justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  aria-label={`删除图片 ${item.name}`}
                  onClick={() => void prepareDelete(item)}
                >
                  <Trash2 className="size-4" />
                  删除
                </Button>
              </div>
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
      <AlertDialog
        open={Boolean(target)}
        onOpenChange={open => {
          if (!open && !busy) setTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              删除图片“{target?.image.name}”？
            </AlertDialogTitle>
            <AlertDialogDescription>
              {target?.cases.length
                ? `有 ${target.cases.length} 个用例引用了这张图片。删除后需要为这些用例重新选择图片。`
                : "引用这张图片的用例在删除后需要重新选择图片。"}
              删除后无法恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {!!target?.cases.length && (
            <ul className="max-h-32 overflow-auto rounded border border-border px-3 py-2 text-sm">
              {target.cases.slice(0, 8).map(item => (
                <li key={item.id} className="truncate" title={item.name}>
                  {item.name}
                </li>
              ))}
              {target.cases.length > 8 && (
                <li className="text-muted-foreground">
                  还有 {target.cases.length - 8} 个用例
                </li>
              )}
            </ul>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              disabled={busy}
              onClick={() => void deleteImage()}
            >
              删除图片
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
