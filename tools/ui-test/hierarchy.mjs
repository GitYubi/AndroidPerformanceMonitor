import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash, randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { XMLParser, XMLValidator } from "fast-xml-parser";
const exec = promisify(execFile);
const helper = fileURLToPath(
  new URL("../../backend/app/ui_hierarchy_capture.py", import.meta.url)
);
export const selectorFields = ["package", "className", "resourceId", "label", "xpath"];
const list = value =>
  value == null ? [] : Array.isArray(value) ? value : [value];

export function parseHierarchy(xml) {
  if (
    Buffer.byteLength(xml) > 8_000_000 ||
    /<!DOCTYPE|<!ENTITY/.test(xml) ||
    XMLValidator.validate(xml) !== true
  )
    throw new Error("控件树 XML 无效");
  const root = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "",
    parseAttributeValue: false,
    trimValues: false,
  }).parse(xml).hierarchy;
  if (root === undefined) throw new Error("控件树没有 hierarchy 根节点");
  const nodes = [];
  function visit(raw, parent, depth, xpath) {
    if (depth > 80 || nodes.length >= 5000)
      throw new Error("控件树超过解析范围");
    const coords = /^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$/
      .exec(raw.bounds || "")
      ?.slice(1)
      .map(Number);
    const node = {
      index: nodes.length,
      parent,
      xpath,
      children: [],
      resourceId: raw["resource-id"] || "",
      text: raw.password === "true" ? "" : String(raw.text || ""),
      description:
        raw.password === "true" ? "" : String(raw["content-desc"] || ""),
      className: raw.class || "",
      package: raw.package || "",
      clickable: raw.clickable === "true",
      checkable: raw.checkable === "true",
      checked: raw.checked === "true",
      enabled: raw.enabled === "true",
      scrollable: raw.scrollable === "true",
      password: raw.password === "true",
      selected: raw.selected === "true",
      bounds: coords || null,
      label: "",
    };
    nodes.push(node);
    list(raw.node).forEach((child, index) =>
      node.children.push(
        visit(child, node.index, depth + 1, `${xpath}/node[${index + 1}]`)
      )
    );
    return node.index;
  }
  list(root.node).forEach((raw, index) =>
    visit(raw, null, 0, `/hierarchy/node[${index + 1}]`)
  );
  const labels = Array(nodes.length).fill("");
  for (let index = nodes.length - 1; index >= 0; index--) {
    const node = nodes[index];
    if (node.password || /(?:^|\/)(?:widget_)?summary$/i.test(node.resourceId))
      continue;
    labels[index] = node.text || node.description ||
      labels[node.children.find(child => labels[child])] || "";
  }
  for (const node of nodes) {
    node.label = labels[node.index];
    if (node.checkable && !node.password && !node.label) {
      let parent = node.parent;
      for (let depth = 0; parent !== null && depth < 3; depth++) {
        const label = labels[parent];
        if (label) {
          node.label = label;
          break;
        }
        parent = nodes[parent].parent;
      }
    }
  }
  const content = nodes.filter(
    n => n.package && n.package !== "com.android.systemui"
  );
  const fingerprint = createHash("sha256")
    .update(
      JSON.stringify(
        content
          .filter(n => !n.resourceId.endsWith("/summary"))
          .map(n => [
            n.package,
            n.className,
            n.resourceId,
            n.className.includes("EditText") ? "" : n.text,
            n.description,
          ])
      )
    )
    .digest("hex")
    .slice(0, 20);
  const packages = [...new Set(content.map(n => n.package))];
  return {
    fingerprint,
    rotation: root.rotation ?? "0",
    packages,
    nodes,
    hasScrollableContent: content.some(n => n.scrollable),
  };
}

export function selectorFor(node) {
  return {
    package: node.package,
    className: node.className,
    resourceId: node.resourceId,
    label: node.label,
  };
}
export function matchingNodes(tree, selector) {
  if (selector.treeSource && selector.treeSource !== tree.treeSource)
    throw new Error("控件树来源与元素定位不同");
  if (!selectorFields.some(key => selector[key]))
    throw new Error("缺少结构定位条件");
  return tree.nodes.filter(node =>
    selectorFields.every(
      key =>
        !selector[key] ||
        (key === "label" && selector.treeSource
          ? [node.text, node.description, node.label].includes(selector.label)
          : node[key] === selector[key])
    )
  );
}

export function resolveSelector(tree, selector) {
  const matches = matchingNodes(tree, selector);
  const actionable = matches.filter(
    n =>
      n.enabled &&
      n.bounds &&
      n.bounds[2] > n.bounds[0] &&
      n.bounds[3] > n.bounds[1]
  );
  if (actionable.length !== 1 || (selector.treeSource && matches.length !== 1))
    throw new Error("控件结构定位不唯一或控件已消失");
  return actionable[0];
}

function inspectionKey(tree) {
  return JSON.stringify([
    tree.rotation,
    tree.nodes.map(node => [
      node.xpath,
      node.package,
      node.className,
      node.resourceId,
      node.package === "com.android.systemui" ? "" : node.text,
      node.package === "com.android.systemui" ? "" : node.description,
      node.bounds,
      node.enabled,
      node.clickable,
      node.checked,
      node.selected,
    ]),
  ]);
}

export function createTreeDevice(
  serial,
  python = process.platform === "win32" ? "python" : "python3"
) {
  if (!/^[a-zA-Z0-9._:-]{1,128}$/.test(serial))
    throw new Error("设备序列号无效");
  const adb = async args =>
    (
      await exec("adb", ["-s", serial, ...args], {
        timeout: 20000,
        maxBuffer: 8 * 1024 * 1024,
      })
    ).stdout;
  async function capture(source) {
    if (source === "uiautomator2") {
      try {
        const { stdout } = await exec(python, [helper, serial], {
          timeout: 20000,
          maxBuffer: 8 * 1024 * 1024,
          windowsHide: true,
        });
        const tree = parseHierarchy(stdout);
        if (!tree.nodes.length) throw new Error("空控件树");
        return { ...tree, treeSource: source };
      } catch {
        throw new Error("UIAutomator2 多窗口控件树读取失败");
      }
    }
    if (source === "adb") {
      try {
        await exec(python, [helper, serial, "--check-active-display"], {
          timeout: 12000,
          windowsHide: true,
        });
      } catch {
        throw new Error("无法确认活动窗口位于默认屏幕");
      }
    }
    const path = `/data/local/tmp/codex-ui-${randomUUID()}.xml`;
    try {
      await adb(["shell", "uiautomator", "dump", path]);
      return { ...parseHierarchy(await adb(["exec-out", "cat", path])), treeSource: "adb" };
    } finally {
      try {
        await adb(["shell", "rm", "-f", path]);
      } catch {
        /* Only this capture's temporary file. */
      }
    }
  }
  async function read(source) {
    if (source && !["adb", "uiautomator2"].includes(source))
      throw new Error("控件树来源无效");
    let previous = await capture(source);
    for (let attempt = 0; attempt < 3; attempt++) {
      const current = await capture(source);
      const stable = source
        ? current.nodes.length > 0 &&
          inspectionKey(previous) === inspectionKey(current)
        : current.fingerprint === previous.fingerprint &&
          current.rotation === previous.rotation &&
          current.packages.length > 0;
      if (stable) return current;
      previous = current;
    }
    throw new Error("页面结构尚未稳定");
  }
  async function screenshot() {
    const options = { encoding: "buffer", timeout: 20000, maxBuffer: 32 * 1024 * 1024 };
    let { stdout } = await exec(
      "adb",
      ["-s", serial, "exec-out", "screencap", "-p"],
      options
    );
    if (stdout.subarray(0, 40).toString().startsWith("[Warning] Multiple displays were found")) {
      ({ stdout } = await exec(python, [helper, serial, "--capture-default"], {
        ...options, windowsHide: true,
      }));
    }
    if (
      stdout.length < 33 ||
      stdout.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a"
    )
      throw new Error("ADB 截图无效");
    return stdout;
  }
  async function physicalSize() {
    const png = await screenshot();
    return {
      width: png.readUInt32BE(16),
      height: png.readUInt32BE(20),
    };
  }
  return {
    read,
    screenshot,
    physicalSize,
    async captureElement(selector) {
      const source = selector.treeSource;
      for (let attempt = 0; attempt < 2; attempt++) {
        const before = await capture(source);
        const image = await screenshot();
        const after = await capture(source);
        if (!after.nodes.length || inspectionKey(before) !== inspectionKey(after)) continue;
        const matches = matchingNodes(after, selector);
        if (matches.length > 1)
          return { state: "failed", message: "匹配到多个元素，请补充定位条件" };
        const bounds = matches[0]?.bounds;
        if (!bounds) return { state: "failed", message: "未找到可截图的匹配元素" };
        const width = image.readUInt32BE(16);
        const height = image.readUInt32BE(20);
        const [x1, y1, x2, y2] = bounds;
        const left = Math.max(0, x1), top = Math.max(0, y1);
        const right = Math.min(width, x2), bottom = Math.min(height, y2);
        if (left >= right || top >= bottom)
          return { state: "failed", message: "元素未显示在默认屏幕内" };
        const { cropScreenshot } = await import("./image-assertion.mjs");
        return await cropScreenshot(image, { left, top, right, bottom });
      }
      return { state: "error", message: "截图期间控件布局发生变化" };
    },
    async tapCoordinates({ x, y }) {
      await adb(["shell", "input", "tap", String(x), String(y)]);
    },
    async swipeCoordinates({ startX, startY, endX, endY, durationMs }) {
      await adb([
        "shell",
        "input",
        "swipe",
        ...[startX, startY, endX, endY, durationMs].map(String),
      ]);
    },
    async tap(selector, validate = () => {}) {
      const tree = await read(selector.treeSource || undefined);
      validate(tree);
      const node = resolveSelector(tree, selector);
      const [x1, y1, x2, y2] = node.bounds;
      const x = Math.floor((x1 + x2) / 2);
      const y = Math.floor((y1 + y2) / 2);
      if (selector.treeSource) {
        if (!node.clickable)
          throw new Error("该节点不可点击，请选择可点击的候选节点");
        const { width, height } = await physicalSize();
        if (x < 0 || y < 0 || x >= width || y >= height)
          throw new Error("元素中心不在默认屏幕内");
      }
      await adb([
        "shell",
        "input",
        "tap",
        String(x),
        String(y),
      ]);
    },
    async back() {
      await adb(["shell", "input", "keyevent", "4"]);
    },
    async settings() {
      await adb([
        "shell",
        "am",
        "start",
        "-a",
        "android.settings.SETTINGS",
        "-f",
        "0x14000000",
      ]);
    },
  };
}
