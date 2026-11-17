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

export function DeleteRecordButton({
  path,
  title,
  description,
  disabled,
  onDeleted,
  className,
}: {
  path: string;
  title: string;
  description: string;
  disabled?: boolean;
  onDeleted: () => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  async function remove() {
    setBusy(true);
    try {
      const response = await fetch(`${BACKEND_URL}/api${path}`, {
        method: "POST",
      });
      const result = await response.json();
      if (!response.ok)
        throw Error(
          typeof result.detail === "string" ? result.detail : "删除失败"
        );
      onDeleted();
      setOpen(false);
      if (result.cleanupErrors?.length)
        toast.error(
          `记录已删除，部分文件清理失败：${result.cleanupErrors.join("、")}`
        );
      else toast.success("记录已删除");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className={className}
        disabled={disabled || busy}
        aria-label={title}
        onClick={() => setOpen(true)}
      >
        <Trash2 className="size-4" />
        删除
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={next => {
          if (!busy) setOpen(next);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{title}？</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => void remove()}
            >
              确认删除
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
