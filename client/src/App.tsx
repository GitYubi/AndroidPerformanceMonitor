/**
 * 设计提示：驾驶舱遥测仪。深色工业基底、遥测青状态色与紧凑等宽数据标签。
 */
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Link, Route, Switch, useLocation } from "wouter";
import { useEffect, useState } from "react";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider } from "./contexts/ThemeContext";
import Home from "./pages/Home";
import UITesting from "./pages/UITesting";

function WorkspaceNav() {
  const [location] = useLocation();
  const [status, setStatus] = useState({ performance: false, ui: 0 });
  useEffect(() => {
    let disposed = false;
    const refresh = async () => {
      try {
        const base =
          import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8090";
        const [perf, ui] = await Promise.all([
          fetch(`${base}/api/sessions/active`).then(r =>
            r.ok ? r.json() : null
          ),
          fetch(`${base}/api/ui/runs`).then(r => (r.ok ? r.json() : [])),
        ]);
        if (!disposed)
          setStatus({
            performance: perf?.state === "running",
            ui: ui.filter((r: { state: string }) =>
              ["queued", "running"].includes(r.state)
            ).length,
          });
      } catch {
        /* Keep the last known status during a temporary connection failure. */
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);
  return (
    <nav
      aria-label="测试工作台"
      className="flex items-center gap-6 border-b border-border bg-background px-6 py-3 text-sm"
    >
      <Link
        href="/"
        className={
          location === "/"
            ? "text-cyan-400 font-medium"
            : "text-muted-foreground"
        }
      >
        性能监控{status.performance && " · 采样中"}
      </Link>
      <Link
        href="/ui"
        className={
          location === "/ui"
            ? "text-cyan-400 font-medium"
            : "text-muted-foreground"
        }
      >
        UI 自动化{status.ui > 0 && ` · ${status.ui} 个任务运行中`}
      </Link>
    </nav>
  );
}

function Router() {
  return (
    <Switch>
      <Route path={"/"} component={Home} />
      <Route path={"/ui"} component={UITesting} />
      <Route path={"/404"} component={NotFound} />
      {/* Final fallback route */}
      <Route component={NotFound} />
    </Switch>
  );
}

// NOTE: About Theme
// - First choose a default theme according to your design style (dark or light bg), than change color palette in index.css
//   to keep consistent foreground/background color across components
// - If you want to make theme switchable, pass `switchable` ThemeProvider and use `useTheme` hook

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider
        defaultTheme="dark"
        // switchable
      >
        <TooltipProvider>
          <Toaster />
          <WorkspaceNav />
          <Router />
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
