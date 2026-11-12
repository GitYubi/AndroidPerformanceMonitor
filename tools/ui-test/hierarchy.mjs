import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash, randomUUID } from "node:crypto";
import { XMLParser, XMLValidator } from "fast-xml-parser";
const exec = promisify(execFile);
const list = value =>
  value == null ? [] : Array.isArray(value) ? value : [value];

export function parseHierarchy(xml) {
  if (xml.length > 8 * 1024 * 1024 || XMLValidator.validate(xml) !== true)
    throw new Error("控件树 XML 无效");
  const root = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "",
    parseAttributeValue: false,
  }).parse(xml).hierarchy;
  if (!root) throw new Error("控件树没有 hierarchy 根节点");
  const nodes = [];
  function visit(raw, parent, depth, xpath) {
    if (depth > 100 || nodes.length > 5000)
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
  const childLabel = index => {
    const n = nodes[index];
    if (n.password || n.resourceId.endsWith("/summary")) return "";
    if (n.text || n.description) return n.text || n.description;
    return n.children.map(childLabel).find(Boolean) || "";
  };
  for (const node of nodes) {
    node.label = childLabel(node.index);
    if (node.checkable && !node.label) {
      let parent = node.parent;
      for (let depth = 0; parent !== null && depth < 3; depth++) {
        const label = childLabel(parent);
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
  const fields = ["package", "className", "resourceId", "label", "xpath"];
  if (!fields.some(key => selector[key])) throw new Error("缺少结构定位条件");
  return tree.nodes.filter(node =>
    fields.every(key => !selector[key] || node[key] === selector[key])
  );
}

export function resolveSelector(tree, selector) {
  const matches = matchingNodes(tree, selector).filter(
    n =>
      n.enabled &&
      n.bounds &&
      n.bounds[2] > n.bounds[0] &&
      n.bounds[3] > n.bounds[1]
  );
  if (matches.length !== 1) throw new Error("控件结构定位不唯一或控件已消失");
  return matches[0];
}

export function createTreeDevice(serial) {
  if (!/^[a-zA-Z0-9._:-]{1,128}$/.test(serial))
    throw new Error("设备序列号无效");
  const adb = async args =>
    (
      await exec("adb", ["-s", serial, ...args], {
        timeout: 20000,
        maxBuffer: 8 * 1024 * 1024,
      })
    ).stdout;
  async function capture() {
    const path = `/data/local/tmp/codex-ui-${randomUUID()}.xml`;
    try {
      await adb(["shell", "uiautomator", "dump", path]);
      return parseHierarchy(await adb(["exec-out", "cat", path]));
    } finally {
      try {
        await adb(["shell", "rm", "-f", path]);
      } catch {
        /* Only this capture's temporary file. */
      }
    }
  }
  async function read() {
    let previous = await capture();
    for (let attempt = 0; attempt < 3; attempt++) {
      const current = await capture();
      if (
        current.fingerprint === previous.fingerprint &&
        current.packages.length
      )
        return current;
      previous = current;
    }
    throw new Error("页面结构尚未稳定");
  }
  async function screenshot() {
    const { stdout } = await exec(
      "adb",
      ["-s", serial, "exec-out", "screencap", "-p"],
      { encoding: "buffer", timeout: 10000, maxBuffer: 32 * 1024 * 1024 }
    );
    if (
      stdout.length < 33 ||
      stdout.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a"
    )
      throw new Error("ADB 截图无效");
    return stdout;
  }
  return {
    read,
    screenshot,
    async physicalSize() {
      const png = await screenshot();
      return {
        width: png.readUInt32BE(16),
        height: png.readUInt32BE(20),
      };
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
      const tree = await read();
      validate(tree);
      const node = resolveSelector(tree, selector);
      const [x1, y1, x2, y2] = node.bounds;
      await adb([
        "shell",
        "input",
        "tap",
        String(Math.floor((x1 + x2) / 2)),
        String(Math.floor((y1 + y2) / 2)),
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
