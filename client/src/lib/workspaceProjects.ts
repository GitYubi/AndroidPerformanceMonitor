import { useEffect, useState } from "react";
import { BACKEND_URL } from "@/lib/backend";
import { defaultProject, type UiProject } from "@/lib/uiImages";

const key = "acpm.selectedProject";
export function currentProjectId() {
  try {
    return localStorage.getItem(key) || defaultProject.id;
  } catch {
    return defaultProject.id;
  }
}
export function rememberProject(id: string) {
  try {
    localStorage.setItem(key, id);
  } catch {
    /* Selection still works in the current page. */
  }
  window.dispatchEvent(new CustomEvent("acpm-project-change", { detail: id }));
}
export function useWorkspaceProjects() {
  const [projects, setProjects] = useState<UiProject[]>([defaultProject]);
  const [projectId, setProjectId] = useState(currentProjectId);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    fetch(`${BACKEND_URL}/api/ui/projects`)
      .then(response => {
        if (!response.ok) throw Error("读取项目失败");
        return response.json() as Promise<UiProject[]>;
      })
      .then(items => {
        if (cancelled) return;
        setProjects(items);
        const selected = currentProjectId();
        if (!items.some(item => item.id === selected)) {
          setProjectId(defaultProject.id);
          rememberProject(defaultProject.id);
        }
      })
      .catch(error => {
        if (!cancelled) setError((error as Error).message);
      });
    const changed = (event: Event) =>
      setProjectId((event as CustomEvent<string>).detail);
    window.addEventListener("acpm-project-change", changed);
    return () => {
      cancelled = true;
      window.removeEventListener("acpm-project-change", changed);
    };
  }, []);
  const selectProject = (id: string) => {
    setProjectId(id);
    rememberProject(id);
  };
  return { projects, projectId, selectProject, projectError: error };
}
