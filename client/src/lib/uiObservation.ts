export type MapNode = {
  index: number;
  parent: number | null;
  children: number[];
  label: string;
  text: string;
  description: string;
  resourceId: string;
  xpath: string;
  className: string;
  package: string;
  bounds: number[] | null;
  clickable: boolean;
  enabled: boolean;
  checkable: boolean;
  checked: boolean;
};
export type Observation = {
  id: string;
  serial: string;
  image: string;
  width: number;
  height: number;
  nodes: MapNode[];
  capturedAt: number;
  signature: string;
  identity?: string;
  geometry: string;
  rotation: string;
  treeSource?: "uiautomator2" | "adb" | "screenshot";
};

export const treeSourceLabel = {
  uiautomator2: "UIAutomator2 多窗口",
  adb: "ADB 活动窗口",
  screenshot: "仅截图 · 控件不可用",
};

export function candidateLabel(node: MapNode) {
  return [node.label, node.package, node.resourceId, node.className]
    .filter(Boolean)
    .join(" · ");
}

export function boundsStyle(
  frame: Pick<Observation, "width" | "height">,
  [left, top, right, bottom]: number[]
) {
  return {
    left: `${(left * 100) / frame.width}%`,
    top: `${(top * 100) / frame.height}%`,
    width: `${((right - left) * 100) / frame.width}%`,
    height: `${((bottom - top) * 100) / frame.height}%`,
  };
}

export function screenPixel(
  frame: Pick<Observation, "width" | "height">,
  rect: { left: number; top: number; width: number; height: number },
  clientX: number,
  clientY: number
) {
  if (
    rect.width <= 0 ||
    rect.height <= 0 ||
    frame.width <= 0 ||
    frame.height <= 0
  )
    return null;
  const x = Math.floor(((clientX - rect.left) * frame.width) / rect.width);
  const y = Math.floor(((clientY - rect.top) * frame.height) / rect.height);
  return {
    x: Math.max(0, Math.min(frame.width - 1, x)),
    y: Math.max(0, Math.min(frame.height - 1, y)),
  };
}

export function elementCenter([left, top, right, bottom]: number[]) {
  return {
    x: Math.floor((left + right) / 2),
    y: Math.floor((top + bottom) / 2),
  };
}

export function nodesAtPoint(observation: Observation, x: number, y: number) {
  return observation.nodes
    .flatMap(node => {
      const bounds = node.bounds;
      if (bounds?.length !== 4 || !bounds.every(Number.isFinite)) return [];
      const [left, top, right, bottom] = bounds;
      if (right <= left || bottom <= top) return [];
      if (x < left || x >= right || y < top || y >= bottom) return [];
      return [{ node, area: (right - left) * (bottom - top) }];
    })
    .sort(
      (a, b) =>
        a.area - b.area ||
        b.node.xpath.split("/").length - a.node.xpath.split("/").length
    )
    .map(({ node }) => node);
}

export function pointCandidates(
  observation: Observation,
  event: { currentTarget: HTMLImageElement; clientX: number; clientY: number }
) {
  const point = screenPixel(
    observation,
    event.currentTarget.getBoundingClientRect(),
    event.clientX,
    event.clientY
  );
  return point ? nodesAtPoint(observation, point.x, point.y) : [];
}
