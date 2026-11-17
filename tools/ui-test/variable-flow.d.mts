export function assignmentsOnEntry(steps: ReadonlyArray<{
  id: string;
  type: string;
  enabled?: boolean;
  parentId?: string | null;
  branch?: "else" | null;
  options?: { variable?: string; valueType?: string } | null;
}>): Map<string, Map<string, string>>;
