import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  saveUiBaseline,
  type UiBaseline,
  type UiImage,
  type UiProject,
} from "@/lib/uiImages";
import { toast } from "sonner";

export function UiBaselineDialog({
  draft,
  project,
  open = true,
  onClose,
  onSaved,
}: {
  draft: UiBaseline;
  project: UiProject;
  open?: boolean;
  onClose: () => void;
  onSaved?: (projectId: string, image: UiImage) => void;
}) {
  const [name, setName] = useState(draft.name ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (busy || !name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const image = await saveUiBaseline(project.id, draft, name.trim());
      onSaved?.(project.id, image);
      onClose();
      toast.success("基准图已保存到项目图片库");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={next => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>保存基准图</DialogTitle>
          <DialogDescription>
            项目 · {project.name} · 同名图片将覆盖
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={event => {
            event.preventDefault();
            void save();
          }}
        >
          <label className="grid gap-1 text-sm">
            基准图名称
            <Input
              value={name}
              maxLength={120}
              disabled={busy}
              onChange={event => setName(event.target.value)}
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <div className="flex justify-end">
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "保存中…" : "保存到图片库"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
