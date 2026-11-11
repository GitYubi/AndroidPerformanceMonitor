import { BookOpen, FileText, Layers, ListChecks } from "lucide-react";

export const uiSections = [
  { id: "execute", label: "执行与编辑", icon: ListChecks },
  { id: "cases", label: "用例库", icon: BookOpen },
  { id: "reports", label: "测试报告", icon: FileText },
  { id: "structure", label: "页面与元素", icon: Layers },
] as const;

export function getUiSection(search: URLSearchParams) {
  return (
    uiSections.find(({ id }) => id === search.get("section"))?.id ?? "execute"
  );
}
