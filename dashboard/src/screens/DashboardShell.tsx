import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Sidebar } from "@/components/Sidebar";
import { logout, type DashboardSessionResponse } from "@/lib/api";
import { getOpenEscalationCount } from "@/lib/escalation-api";
import { getInitialTheme, setTheme, type Theme } from "@/lib/theme";
import { useAsyncData } from "@/lib/use-async-data";
import { InboxScreen } from "@/screens/InboxScreen";
import { OverviewScreen } from "@/screens/OverviewScreen";
import type { ScreenKey } from "@/screens/screens";
import { SettingsScreen } from "@/screens/SettingsScreen";
import { SourcesScreen } from "@/screens/SourcesScreen";
import { UsageScreen } from "@/screens/UsageScreen";
import { WidgetScreen } from "@/screens/WidgetScreen";

interface DashboardShellProps {
  session: DashboardSessionResponse;
  onLoggedOut: () => void;
}

function ActiveScreen({
  screen,
  onOpenCountChanged,
}: {
  screen: ScreenKey;
  onOpenCountChanged: () => void;
}) {
  switch (screen) {
    case "overview":
      return <OverviewScreen />;
    case "widget":
      return <WidgetScreen />;
    case "sources":
      return <SourcesScreen />;
    case "inbox":
      return <InboxScreen onOpenCountChanged={onOpenCountChanged} />;
    case "usage":
      return <UsageScreen />;
    case "settings":
      return <SettingsScreen />;
  }
}

export function DashboardShell({ session, onLoggedOut }: DashboardShellProps) {
  const [loggingOut, setLoggingOut] = useState(false);
  const [screen, setScreen] = useState<ScreenKey>("overview");
  const [theme, setThemeState] = useState<Theme>(getInitialTheme);

  function handleToggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    setThemeState(next);
  }
  const openCountFetcher = useCallback(() => getOpenEscalationCount(), []);
  const { state: openCountState, reload: reloadOpenCount } = useAsyncData(openCountFetcher);
  const openEscalationCount = openCountState.status === "ready" ? openCountState.data.count : null;

  async function handleConfirmLogout() {
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      // Even a failed logout call still clears local state -- the
      // session cookie is HttpOnly and this screen has no other way to
      // act on it; a stale server-side session (if the call genuinely
      // failed) is not reachable from here anyway.
    } finally {
      setLoggingOut(false);
      onLoggedOut();
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <p className="text-sm font-medium text-foreground">{session.tenant.name}</p>
          <p className="text-sm text-muted-foreground">{session.user.email}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={handleToggleTheme}>
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </Button>
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline">Log out</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Log out of SamoFlow?</DialogTitle>
                <DialogDescription>
                  You will need to sign in again to access this dashboard.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                {/* `variant="destructive"` was tried here first and
                 * dropped for two reasons, found live: logging out
                 * deletes nothing (docs/SPEC.md §12's own "destructive
                 * actions... say exactly what will be removed" doesn't
                 * really describe this action), and the Nova preset's
                 * own destructive color pairing measured at 3.82:1
                 * contrast here (a real axe-core finding, below WCAG
                 * AA's 4.5:1) -- the default variant's solid colors both
                 * fit the action better and pass contrast. */}
                <Button onClick={handleConfirmLogout} disabled={loggingOut}>
                  {loggingOut ? "Logging out…" : "Log out"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </header>
      <div className="flex flex-1">
        <Sidebar
          active={screen}
          onNavigate={setScreen}
          openEscalationCount={openEscalationCount}
        />
        <main className="flex-1 p-6">
          <ActiveScreen screen={screen} onOpenCountChanged={reloadOpenCount} />
        </main>
      </div>
    </div>
  );
}
