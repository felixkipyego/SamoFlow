import { useState } from "react";
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
import { logout, type DashboardSessionResponse } from "@/lib/api";

interface DashboardShellProps {
  session: DashboardSessionResponse;
  onLoggedOut: () => void;
}

export function DashboardShell({ session, onLoggedOut }: DashboardShellProps) {
  const [loggingOut, setLoggingOut] = useState(false);

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
    <div className="min-h-screen bg-background">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <p className="text-sm font-medium text-foreground">{session.tenant.name}</p>
          <p className="text-sm text-muted-foreground">{session.user.email}</p>
        </div>
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
      </header>
      <main className="p-6">
        <p className="text-muted-foreground">
          Tenant settings, sources and the escalation inbox land in Steps 6.2-6.4.
        </p>
      </main>
    </div>
  );
}
