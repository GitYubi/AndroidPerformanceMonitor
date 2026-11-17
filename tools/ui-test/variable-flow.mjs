// Types of variables assigned on every successful path reaching each step.
export function assignmentsOnEntry(steps) {
  const children = new Map();
  for (const step of steps) {
    const parentId = step.parentId ?? null;
    if (!children.has(parentId)) children.set(parentId, []);
    children.get(parentId).push(step);
  }
  const entry = new Map();
  function visit(parentId, branch, incoming, active) {
    let assigned = new Map(incoming);
    for (const step of children.get(parentId) ?? []) {
      if ((step.branch ?? null) !== branch) continue;
      entry.set(step.id, new Map(assigned));
      const enabled = active && step.enabled !== false;
      if (step.type === "if") {
        const yes = visit(step.id, null, assigned, enabled);
        const no = visit(step.id, "else", assigned, enabled);
        assigned = new Map();
        for (const [name, kind] of yes) {
          if (no.has(name)) assigned.set(name, no.get(name) === kind ? kind : "mixed");
        }
      } else if (step.type === "else" || step.type === "for") {
        assigned = visit(step.id, null, assigned, enabled);
      } else if (enabled && step.type === "assign" && step.options?.variable) {
        assigned.set(step.options.variable, step.options.valueType ?? "mixed");
      }
    }
    return assigned;
  }
  visit(null, null, new Map(), true);
  return entry;
}
