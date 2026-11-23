import { BACKEND_URL } from "@/lib/backend";

export const defaultProject = { id: "default", name: "默认项目" };
export type UiProject = typeof defaultProject;
export type UiImage = {
  id: string;
  name: string;
  width: number;
  height: number;
  source: "roi" | "element" | "legacy";
  aliases?: string[];
  region?: { left: number; top: number; right: number; bottom: number } | null;
};
export type ImageRegion = [
  left: number,
  top: number,
  right: number,
  bottom: number,
];
export type UiBaseline = {
  image: string;
  region: ImageRegion;
  source: "roi" | "element";
  name?: string;
};

export function uiImagesPath(projectId: string) {
  return `/ui/projects/${encodeURIComponent(projectId)}/images`;
}

export function uiImageUrl(projectId: string, imageId: string) {
  return `${BACKEND_URL}/api${uiImagesPath(projectId)}/${encodeURIComponent(imageId)}`;
}

export async function saveUiBaseline(
  projectId: string,
  draft: UiBaseline,
  name: string
): Promise<UiImage> {
  const source = new Image();
  source.src = draft.image;
  await source.decode();
  const [left, top, right, bottom] = draft.region;
  const x = Math.max(0, left);
  const y = Math.max(0, top);
  const width = Math.min(source.naturalWidth, right) - x;
  const height = Math.min(source.naturalHeight, bottom) - y;
  if (width <= 0 || height <= 0 || width * height > 16_000_000)
    throw Error("截图范围无效或过大");
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw Error("当前浏览器无法生成截图");
  context.drawImage(source, x, y, width, height, 0, 0, width, height);
  const png = await new Promise<Blob | null>(resolve =>
    canvas.toBlob(resolve, "image/png")
  );
  if (!png) throw Error("截图生成失败");
  const data = new FormData();
  data.append("file", png, "baseline.png");
  data.append("name", name);
  data.append("source", draft.source);
  data.append(
    "region",
    JSON.stringify({ left: x, top: y, right: x + width, bottom: y + height })
  );
  const response = await fetch(`${BACKEND_URL}/api${uiImagesPath(projectId)}`, {
    method: "POST",
    body: data,
  });
  const result = await response.json();
  if (!response.ok)
    throw Error(
      typeof result.detail === "string" ? result.detail : "基准图保存失败"
    );
  return result;
}
