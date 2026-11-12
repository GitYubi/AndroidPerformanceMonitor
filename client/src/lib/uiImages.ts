import { BACKEND_URL } from "@/lib/backend";

export const defaultProject = { id: "default", name: "默认项目" };
export type UiProject = typeof defaultProject;
export type UiImage = {
  id: string;
  name: string;
  width: number;
  height: number;
  source: "roi" | "element" | "legacy";
  region?: { left: number; top: number; right: number; bottom: number } | null;
};

export function uiImagesPath(projectId: string) {
  return `/ui/projects/${encodeURIComponent(projectId)}/images`;
}

export function uiImageUrl(projectId: string, imageId: string) {
  return `${BACKEND_URL}/api${uiImagesPath(projectId)}/${encodeURIComponent(imageId)}`;
}
