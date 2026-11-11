import {
  ChartNoAxesCombined,
  CircleDot,
  ListTodo,
  Power,
  Radio,
  Send,
} from "lucide-react";

export const canSections = [
  { id: "send", label: "DBC 发送", icon: Send },
  { id: "wake", label: "唤醒车机", icon: Power },
  { id: "receive", label: "报文监控", icon: Radio },
  { id: "trend", label: "信号趋势", icon: ChartNoAxesCombined },
  { id: "tasks", label: "发送任务", icon: ListTodo },
  { id: "record", label: "录制", icon: CircleDot },
] as const;

type CanSection = (typeof canSections)[number]["id"];

export function getCanSection(search: URLSearchParams): CanSection {
  return (
    canSections.find(({ id }) => id === search.get("section"))?.id ?? "send"
  );
}
