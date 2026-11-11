import { BACKEND_URL } from "@/lib/backend";
/**
 * 设计提示：驾驶舱遥测仪。深色工业基底、遥测青状态色与紧凑等宽数据标签。
 */
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/NotFound";
import { Link, Route, Switch, useLocation, useSearchParams } from "wouter";
import { useEffect, useState } from "react";
import { Activity, Gauge, Moon, Sun, type LucideIcon } from "lucide-react";
import { canSections, getCanSection } from "@/lib/canNavigation";
import { getUiSection, uiSections } from "@/lib/uiNavigation";
import ErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider, useTheme } from "./contexts/ThemeContext";
import Home from "./pages/Home";
import UITesting from "./pages/UITesting";
import MonkeyTesting from "./pages/MonkeyTesting";
import CanBus from "./pages/CanBus";

function SidebarLink({
  href,
  label,
  icon: Icon,
  current,
  detail,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  current: boolean;
  detail?: string;
}) {
  return (
    <Link
      href={href}
      title={detail ? `${label} · ${detail}` : label}
      aria-current={current ? "page" : undefined}
      className={`relative flex min-h-10 items-center justify-center gap-2.5 rounded-md px-1 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-sidebar-ring md:justify-start md:px-2.5 ${current ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:rounded-full before:bg-sidebar-primary" : "text-muted-foreground hover:bg-muted hover:text-sidebar-foreground"}`}
    >
      <Icon size={16} aria-hidden="true" className="shrink-0" />
      <span className="sr-only min-w-0 flex-1 truncate md:not-sr-only">
        {label}
      </span>
      {detail && (
        <span className="hidden shrink-0 text-[10px] text-primary md:inline">
          {detail}
        </span>
      )}
    </Link>
  );
}

function WorkspaceNav() {
  const [location] = useLocation();
  const [search] = useSearchParams();
  const canSection = getCanSection(search);
  const uiSection = getUiSection(search);
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
    <aside className="sticky top-0 z-20 flex h-dvh min-w-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <Link
        href="/"
        className="flex min-h-16 shrink-0 items-center justify-center gap-2.5 border-b border-sidebar-border px-2 md:justify-start md:px-4"
        title="座舱测试工作台"
      >
        <span
          aria-hidden="true"
          className="grid size-8 shrink-0 place-items-center rounded-md bg-sidebar-primary font-mono text-xs font-bold text-sidebar-primary-foreground"
        >
          CT
        </span>
        <span className="sr-only truncate text-sm font-semibold md:not-sr-only">
          座舱测试工作台
        </span>
      </Link>
      <nav
        aria-label="工作台导航"
        className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-1.5 py-3 md:px-2.5"
      >
        <div className="space-y-1" role="group" aria-label="性能测试">
          <p
            aria-hidden="true"
            className="hidden px-2.5 pb-1 text-xs font-medium text-muted-foreground md:block"
          >
            性能测试
          </p>
          <SidebarLink
            href="/"
            label="性能监控"
            icon={Gauge}
            current={location === "/"}
            detail={status.performance ? "采样中" : undefined}
          />
          <SidebarLink
            href="/monkey"
            label="Monkey 压测"
            icon={Activity}
            current={location === "/monkey"}
            detail={status.monkey ? `${status.monkey} 运行中` : undefined}
          />
        </div>
        <div className="space-y-1" role="group" aria-label="UI 自动化">
          <p
            aria-hidden="true"
            className="hidden px-2.5 pb-1 text-xs font-medium text-muted-foreground md:block"
          >
            UI 自动化
          </p>
          {uiSections.map(({ id, label, icon }) => (
            <SidebarLink
              key={id}
              href={id === "execute" ? "/ui" : `/ui?section=${id}`}
              label={label}
              icon={icon}
              current={location === "/ui" && uiSection === id}
              detail={
                id === "execute" && status.ui
                  ? `${status.ui} 运行中`
                  : undefined
              }
            />
          ))}
        </div>
        <div className="space-y-1" role="group" aria-label="总线控制">
          <p
            aria-hidden="true"
            className="hidden px-2.5 pb-1 text-xs font-medium text-muted-foreground md:block"
          >
            总线控制
          </p>
          {canSections.map(({ id, label, icon }) => (
            <SidebarLink
              key={id}
              href={id === "send" ? "/can" : `/can?section=${id}`}
              label={label}
              icon={icon}
              current={location === "/can" && canSection === id}
            />
          ))}
        </div>
      </nav>
      <div className="shrink-0 border-t border-sidebar-border p-1.5 md:p-2.5">
        <button
          type="button"
          title={theme === "dark" ? "切换浅色" : "切换深色"}
          className="flex min-h-10 w-full items-center justify-center gap-2.5 rounded-md px-1 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-sidebar-foreground focus-visible:outline-2 focus-visible:outline-sidebar-ring md:justify-start md:px-2.5"
          onClick={toggleTheme}
        >
          {theme === "dark" ? (
            <Sun size={16} aria-hidden="true" />
          ) : (
            <Moon size={16} aria-hidden="true" />
          )}
          <span className="sr-only md:not-sr-only">
            {theme === "dark" ? "切换浅色" : "切换深色"}
          </span>
        </button>
      </div>
    </aside>
  );
}

function Router() {
  return (
    <Switch>
      <Route path={"/"} component={Home} />
      <Route path={"/ui"} component={UITesting} />
      <Route path={"/monkey"} component={MonkeyTesting} />
      <Route path={"/can"} component={CanBus} />
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
          <div className="grid min-h-dvh grid-cols-[3.25rem_minmax(0,1fr)] md:grid-cols-[12.5rem_minmax(0,1fr)]">
            <WorkspaceNav />
            <div className="min-w-0">
              <Router />
            </div>
          </div>
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
