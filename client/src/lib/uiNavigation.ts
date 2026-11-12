import {
  BookOpen,
  FileText,
  FolderKanban,
  Images,
  Layers,
  ListChecks,
} from "lucide-react";

export const uiSections = [
  { id: "projects", label: "项目管理", icon: FolderKanban },
  { id: "execute", label: "执行与编辑", icon: ListChecks },
  { id: "cases", label: "用例库", icon: BookOpen },
  { id: "images", label: "图片库", icon: Images },
  { id: "reports", label: "测试报告", icon: FileText },
  { id: "structure", label: "页面与元素", icon: Layers },
] as const;

export function getUiSection(search: URLSearchParams) {
  return (
    uiSections.find(({ id }) => id === search.get("section"))?.id ?? "execute"
  );
}
