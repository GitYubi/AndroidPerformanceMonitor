import { createHash } from "node:crypto";
import { resolveSelector, selectorFor } from "./hierarchy.mjs";

// Only a whole, navigation-only instruction can bypass model planning.
export function navigationTarget(prompt) {
  if (/^打开/.test(prompt.trim()) && !/页面[。]?$/.test(prompt.trim()))
    return null;
  const match =
    /^(?:打开|进入|前往|跳转到|导航到)\s*([^，。；、\n,;]{1,40}?)(?:页面)?[。]?$/u.exec(
      prompt.trim()
    );
  if (
    !match ||
    /并|然后|之后|再|点击|关闭|开启|输入|设置为|切换|开关/.test(match[1])
  )
    return null;
  return match[1].trim();
}
export function pageKey(tree) {
  const clean = (n, value = "") => {
    if (
      /\/(?:widget_)?summary$/.test(n.resourceId) ||
      n.className.includes("EditText")
    )
      return "";
    return /^\s*\d+(?:[.,]\d+)?\s*(?:%|MB|GB|分钟|小时|秒)\s*$/i.test(value)
      ? "<value>"
      : value;
  };
  return createHash("sha256")
    .update(
      JSON.stringify(
        tree.nodes
          .filter(n => n.package !== "com.android.systemui")
          .map(n => [
            n.parent,
            n.package,
            n.className,
            n.resourceId,
            clean(n, n.text),
            clean(n, n.description),
            Boolean(n.selected),
          ])
      )
    )
    .digest("hex");
}
export function findRoute(edges, source, target) {
  const queue = [[source, []]],
    seen = new Set();
  while (queue.length) {
    const [at, path] = queue.shift();
    if (at === target) return path;
    if (seen.has(at) || path.length >= 12) continue;
    seen.add(at);
    for (const edge of edges)
      if (!edge.stale && edge.from === at)
        queue.push([edge.to, [...path, edge]]);
  }
  return null;
}
export function pointSelector(tree, point) {
  const nodes = tree.nodes.filter(
    n =>
      n.clickable &&
      !n.checkable &&
      n.enabled &&
      n.bounds &&
      point.x >= n.bounds[0] &&
      point.x < n.bounds[2] &&
      point.y >= n.bounds[1] &&
      point.y < n.bounds[3]
  );
  nodes.sort(
    (a, b) =>
      (a.bounds[2] - a.bounds[0]) * (a.bounds[3] - a.bounds[1]) -
      (b.bounds[2] - b.bounds[0]) * (b.bounds[3] - b.bounds[1])
  );
  if (!nodes.length) return null;
  const selector = selectorFor(nodes[0]);
  try {
    return resolveSelector(tree, selector).index === nodes[0].index
      ? selector
      : null;
  } catch {
    return null;
  }
}

export class NavigationMemory {
  constructor({ knowledge = {}, seeds = {}, device, emit = () => {} }) {
    this.device = device;
    this.emit = emit;
    this.edges = structuredClone(knowledge.edges || []).slice(-500);
    this.goals = structuredClone(knowledge.goals || []).slice(-200);
    this.seedGoals = [];
    const pages = new Map(
      (seeds.pages || []).map(p => [p.id, { ...p, key: pageKey(p) }])
    );
    for (const p of pages.values())
      this.seedGoals.push({ name: p.name, target: p.key });
    for (const e of seeds.edges || []) {
      const from = pages.get(e.from),
        to = pages.get(e.to);
      if (
        from &&
        to &&
        !this.edges.some(
          x =>
            x.from === from.key &&
            x.to === to.key &&
            JSON.stringify(x.selector) === JSON.stringify(e.selector)
        )
      )
        this.edges.push({
          from: from.key,
          to: to.key,
          selector: e.selector,
          seed: true,
        });
    }
  }
  save() {
    this.emit({
      type: "navigation_update",
      knowledge: {
        edges: this.edges.filter(e => !e.seed || e.stale).slice(-500),
        goals: this.goals.slice(-200),
      },
    });
  }
  // Called by the actual SDK tap primitive. Capture failure never repeats or blocks its action.
  async observeTap(point, logicalSize, invoke) {
    if (!this.collecting) return invoke();
    let before, selector;
    try {
      const size = await this.device.physicalSize();
      before = await this.device.read();
      selector = pointSelector(before, {
        x: Math.round((point.x * size.width) / logicalSize.width),
        y: Math.round((point.y * size.height) / logicalSize.height),
      });
    } catch {
      /* Incomplete evidence is not learned. */
    }
    const result = await invoke();
    if (before && selector) {
      try {
        const after = await this.device.read();
        const from = pageKey(before),
          to = pageKey(after);
        if (from !== to) this.pending.push({ from, to, selector });
      } catch {
        /* Preserve execution result, omit incomplete transition. */
      }
    }
    return result;
  }
  async run(prompt, fallback, verify) {
    const name = navigationTarget(prompt);
    if (!name) return fallback();
    const started = Date.now();
    let hits = 0,
      aiRequests = 0,
      learned = 0,
      reason = "missing";
    try {
      let current;
      try {
        current = await this.device.read();
      } catch {
        reason = "observation-unavailable";
      }
      const source = current && pageKey(current);
      const exactCandidates = this.goals.filter(
        g => g.prompt === prompt && g.source === source
      );
      const candidates = exactCandidates.length
        ? exactCandidates
        : this.goals.filter(g => g.prompt === prompt);
      const targets = [
        ...new Set(
          (candidates.length
            ? candidates
            : this.seedGoals.filter(g => g.name === name)
          ).map(g => g.target)
        ),
      ];
      const route =
        current && targets.length === 1
          ? findRoute(this.edges, source, targets[0])
          : null;
      if (route) {
        for (const edge of route) {
          // Missing/ambiguous locators may fall back before sending input.
          try {
            const n = resolveSelector(current, edge.selector);
            if (!n.clickable || n.checkable) throw new Error();
          } catch {
            edge.stale = true;
            reason = "locator-invalid";
            break;
          }
          // If sending input fails its outcome is unknown: stop, never repeat through AI.
          await this.device.tap(edge.selector, tree => {
            if (pageKey(tree) !== edge.from) throw new Error("导航来源已变化");
          });
          current = await this.device.read();
          if (pageKey(current) !== edge.to) {
            edge.stale = true;
            reason = "destination-changed";
            break;
          }
          hits++;
        }
        if (pageKey(current) === targets[0]) {
          reason = "hit";
          return "passed";
        }
      }
      this.pending = [];
      this.collecting = true;
      aiRequests++;
      const result = await fallback();
      this.collecting = false;
      // A completed aiAct alone does not prove the requested destination was reached.
      aiRequests++;
      if ((await verify(name)) === true) {
        let after;
        try {
          after = await this.device.read();
        } catch {
          reason = "verification-unavailable";
        }
        if (after && source) {
          for (const edge of this.pending) {
            const exists = this.edges.find(
              e =>
                !e.seed &&
                e.from === edge.from &&
                e.to === edge.to &&
                JSON.stringify(e.selector) === JSON.stringify(edge.selector)
            );
            if (exists) exists.stale = false;
            else {
              this.edges.push(edge);
              learned++;
            }
          }
          this.goals = this.goals.filter(
            g => !(g.source === source && g.prompt === prompt)
          );
          this.goals.push({ source, prompt, target: pageKey(after) });
        }
      } else {
        reason = "goal-unconfirmed";
        return "needs_review";
      }
      return result;
    } finally {
      this.collecting = false;
      this.save();
      this.emit({
        type: "navigation",
        state: reason,
        hits,
        learned,
        aiRequests,
        elapsedMs: Date.now() - started,
        message: `路径复用 ${hits} 步，新增入口 ${learned} 个，Midscene 调用 ${aiRequests} 次`,
      });
    }
  }
}

// Installed @midscene/android 1.12.6 exposes pointer primitives before actionSpace is built.
export function attachNavigationObserver(device, memory) {
  const pointer = device.inputPrimitives?.pointer;
  if (typeof pointer?.tap !== "function") return false;
  const tap = pointer.tap.bind(pointer);
  pointer.tap = async point => {
    if (!memory.collecting) return tap(point);
    let size;
    try {
      size = await device.size();
    } catch {
      return tap(point);
    }
    return memory.observeTap(point, size, () => tap(point));
  };
  return true;
}
