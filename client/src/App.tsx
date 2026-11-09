import { BACKEND_URL } from "@/lib/backend";
/**
 * 设计提示：驾驶舱遥测仪。深色工业基底、遥测青状态色与紧凑等宽数据标签。
 */
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Link, Route, Switch, useLocation } from "wouter";
import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider, useTheme } from "./contexts/ThemeContext";
import Home from "./pages/Home";
import UITesting from "./pages/UITesting";
import MonkeyTesting from "./pages/MonkeyTesting";

function WorkspaceNav() {
  const [location] = useLocation();
  const { theme, toggleTheme } = useTheme();
  const [status, setStatus] = useState({
    performance: false,
    ui: 0,
    monkey: 0,
  });
  useEffect(() => {
    let disposed = false;
    const refresh = async () => {
      try {
        const base = BACKEND_URL;
        const [perf, ui, monkey] = await Promise.all([
          fetch(`${base}/api/sessions/active`).then(r =>
            r.ok ? r.json() : null
          ),
          fetch(`${base}/api/ui/runs`).then(r => (r.ok ? r.json() : [])),
          fetch(`${base}/api/monkey/runs`).then(r => (r.ok ? r.json() : [])),
        ]);
        if (!disposed)
          setStatus({
            performance: perf?.state === "running",
            ui: ui.filter((r: { state: string }) =>
              ["queued", "running"].includes(r.state)
            ).length,
            monkey: monkey.filter((r: { state: string }) =>
              ["starting", "running", "stopping"].includes(r.state)
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
      className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border bg-background px-6 py-3 text-sm"
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
        href="/monkey"
        className={
          location === "/monkey"
            ? "text-cyan-400 font-medium"
            : "text-muted-foreground"
        }
      >
        Monkey 压测{status.monkey > 0 && " · 运行中"}
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
      <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={toggleTheme}>
        {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
        {theme === "dark" ? "切换浅色" : "切换深色"}
      </Button>
    </nav>
  );
}

function Router() {
  return (
    <Switch>
      <Route path={"/"} component={Home} />
      <Route path={"/ui"} component={UITesting} />
      <Route path={"/monkey"} component={MonkeyTesting} />
      <Route path={"/404"} component={NotFound} />
      {/* Final fallback route */}
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider defaultTheme="dark" switchable>
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
