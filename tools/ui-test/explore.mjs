import { selectorFor, resolveSelector } from "./hierarchy.mjs";

const blocked =
  /恢复出厂|清除数据|重置|删除|卸载|安装|USB|调试|断电|关机|制动|驾驶|转向|付款|购买|factory|reset|delete|uninstall|install|debug|power off|brake|steering|payment/i;

// DFS over observed hierarchy states. The model never chooses a target or route.
export async function exploreTree(agent, device, step, emit) {
  const scope = step.exploration;
  const visited = new Set(),
    covered = new Set();
  let actions = 0,
    failed = false,
    incomplete = false,
    halted = false;
  const withinScope = tree =>
    tree.packages.length > 0 &&
    tree.packages.every(pkg => scope.allowedPackages.includes(pkg));
  const root = await device.read();
  if (!withinScope(root)) throw new Error("当前页面包名不在探索范围");
  const canSpend = (count, depth) =>
    actions + count + depth <= scope.maxActions;
  const descendants = (tree, node) =>
    node.children.flatMap(index => [
      tree.nodes[index],
      ...descendants(tree, tree.nodes[index]),
    ]);

  async function visit(initial, depth) {
    if (visited.has(initial.fingerprint) || halted) return;
    visited.add(initial.fingerprint);
    emit({
      type: "graph-page",
      state: "visited",
      fingerprint: initial.fingerprint,
      depth,
      packages: initial.packages,
      nodeCount: initial.nodes.length,
      tree: initial,
    });
    if (initial.hasScrollableContent) {
      incomplete = true;
      emit({
        type: "coverage",
        state: "skipped",
        message: "当前页存在滚动区域；本轮仅覆盖可见控件",
        fingerprint: initial.fingerprint,
      });
    }
    if (scope.visualAssertion) {
      const ok = await agent.aiBoolean(scope.visualAssertion);
      failed ||= !ok;
      emit({
        type: "visual",
        state: ok ? "passed" : "failed",
        fingerprint: initial.fingerprint,
        message: scope.visualAssertion,
      });
      if (!ok) return;
    }
    const switches = initial.nodes.filter(
      n =>
        n.enabled &&
        n.checkable &&
        scope.allowedControls.includes(n.label) &&
        !blocked.test(n.label)
    );
    for (const control of switches) {
      if (halted) return;
      if (!canSpend(2, depth)) {
        incomplete = true;
        break;
      }
      const selector = selectorFor(control);
      let current;
      try {
        current = resolveSelector(await device.read(), selector);
      } catch {
        incomplete = true;
        continue;
      }
      const original = current.checked;
      let restored = false;
      emit({
        type: "exploration",
        state: "running",
        control: current.label,
        original,
        selector,
        message: "根据控件树切换开关，随后恢复",
      });
      try {
        actions++;
        await device.tap(selector);
        const after = resolveSelector(await device.read(), selector);
        const changed = after.checked !== original;
        failed ||= !changed;
        if (changed) covered.add(current.label);
        emit({
          type: "exploration",
          state: changed ? "passed" : "failed",
          control: current.label,
          original,
          selector,
          message: changed ? "控件树已确认状态改变" : "控件状态未改变",
        });
        if (changed && scope.visualAssertion) {
          const ok = await agent.aiBoolean(scope.visualAssertion);
          failed ||= !ok;
          emit({
            type: "visual",
            state: ok ? "passed" : "failed",
            control: current.label,
            message: scope.visualAssertion,
          });
        }
      } finally {
        try {
          const tree = await device.read();
          if (withinScope(tree)) {
            const now = resolveSelector(tree, selector);
            if (now.checked !== original) {
              actions++;
              await device.tap(selector);
            }
            restored =
              resolveSelector(await device.read(), selector).checked ===
              original;
          }
        } catch {
          restored = false;
        }
        emit({
          type: "exploration",
          state: restored ? "restored" : "needs_review",
          control: current.label,
          original,
          selector,
          actions,
          message: restored
            ? "控件树确认已恢复原状态"
            : "无法确认恢复，停止遍历，请检查原状态",
        });
        if (!restored) halted = true;
      }
    }
    if (halted) return;
    const navigation = initial.nodes.filter(
      n =>
        n.enabled &&
        n.clickable &&
        !n.checkable &&
        (scope.allowedNavigation.includes(n.label) ||
          (scope.autoNavigation &&
            /(?:LinearLayout|RelativeLayout|ViewGroup)$/.test(n.className) &&
            descendants(initial, n).some(child =>
              child.resourceId.endsWith("/title")
            ))) &&
        !blocked.test(n.label) &&
        Boolean(n.label) &&
        !n.className.includes("EditText") &&
        !descendants(initial, n).some(child => child.checkable)
    );
    const attempted = new Set();
    for (const node of navigation) {
      const selector = selectorFor(node),
        key = JSON.stringify(selector);
      if (attempted.has(key) || halted) continue;
      attempted.add(key);
      if (
        depth >= scope.maxDepth ||
        visited.size >= scope.maxPages ||
        !canSpend(2, depth)
      ) {
        incomplete = true;
        emit({
          type: "graph-edge",
          state: "skipped",
          from: initial.fingerprint,
          label: node.label,
          depth,
          message: "达到深度、页面数或动作预算",
        });
        continue;
      }
      const before = await device.read();
      if (before.fingerprint !== initial.fingerprint) {
        halted = true;
        break;
      }
      try {
        resolveSelector(before, selector);
      } catch {
        incomplete = true;
        emit({
          type: "graph-edge",
          state: "skipped",
          from: initial.fingerprint,
          label: node.label,
          message: "定位不唯一或控件当前不可见",
        });
        continue;
      }
      actions++;
      await device.tap(selector);
      const child = await device.read();
      const changed = child.fingerprint !== before.fingerprint;
      emit({
        type: "graph-edge",
        state: changed ? "entered" : "unchanged",
        from: before.fingerprint,
        to: child.fingerprint,
        label: node.label,
        selector,
        depth: depth + 1,
      });
      if (!changed) continue;
      try {
        if (!withinScope(child)) {
          incomplete = true;
          emit({
            type: "coverage",
            state: "skipped",
            message: "目标页面超出允许包名范围，返回父页面",
            fingerprint: child.fingerprint,
            packages: child.packages,
            tree: child,
          });
        } else await visit(child, depth + 1);
      } finally {
        if (!halted) {
          actions++;
          await device.back();
          const returned = await device.read();
          const restored = returned.fingerprint === before.fingerprint;
          emit({
            type: "graph-return",
            state: restored ? "restored" : "needs_review",
            from: child.fingerprint,
            to: returned.fingerprint,
            tree: restored ? undefined : returned,
            message: restored
              ? "已返回并校验父页面"
              : "返回页面与父页面不一致，停止遍历",
          });
          if (!restored) halted = true;
        }
      }
    }
  }
  await visit(root, 0);
  for (const name of scope.allowedControls) {
    if (!covered.has(name)) {
      incomplete = true;
      emit({
        type: "coverage",
        state: "skipped",
        control: name,
        message: blocked.test(name)
          ? "该控件被操作规则排除"
          : "本轮未完成此控件的切换验证",
      });
    }
  }
  emit({
    type: "coverage-summary",
    state: halted || incomplete ? "needs_review" : failed ? "failed" : "passed",
    pages: visited.size,
    actions,
    controls: [...covered],
    root: root.fingerprint,
    message: halted
      ? "页面或控件未确认恢复，请人工检查"
      : "已按记录路径返回起始页面",
  });
  return halted
    ? "needs_review"
    : failed
      ? "failed"
      : incomplete
        ? "needs_review"
        : "passed";
}
