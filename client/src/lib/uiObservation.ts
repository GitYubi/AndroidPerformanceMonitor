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
};

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
      if (!bounds) return [];
      const [left, top, right, bottom] = bounds;
      if (x < left || x >= right || y < top || y >= bottom) return [];
      return [{ node, area: (right - left) * (bottom - top) }];
    })
    .sort((a, b) => a.area - b.area)
    .map(({ node }) => node);
}
