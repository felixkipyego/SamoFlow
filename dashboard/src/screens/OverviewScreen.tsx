// Step 6.2: the go-live checklist -- a read-only mirror of the real
// server-side draft-to-live gate (GET .../tenant/go-live-check,
// app/dashboard/tenant_routes.py). This screen computes NOTHING
// itself: every value shown is server-computed state, so it can never
// drift from the actual rule the server enforces (docs/SPEC.md: "a
// site key moves from draft to live only through a server-side check
// -- at least one contact method and one allowed origin"). Promoting
// a site key to live stays a platform-admin CLI action (6.5 Platform
// admin, POST-PILOT, out of this screen's scope) -- this screen only
// explains what is still missing.
import { useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DataView } from "@/components/DataView";
import { getGoLiveCheck, type GoLiveCheck } from "@/lib/tenant-api";
import { useAsyncData } from "@/lib/use-async-data";

function ChecklistItem({ label, done, required }: { label: string; done: boolean; required: boolean }) {
  // variant="destructive" was tried here first and dropped, found live:
  // this theme's destructive color measures 4.0:1 against the badge's
  // own tinted background (a real axe-core "color-contrast" finding,
  // below WCAG AA's 4.5:1) -- "outline" (plain border + text-foreground,
  // the same pairing that passes everywhere else on this page) still
  // reads as visually distinct from "default"/"secondary" without
  // fighting the theme's own red.
  return (
    <div className="flex items-center justify-between gap-2 rounded-md border border-border p-3">
      <span className="text-sm">{label}</span>
      <Badge variant={done ? "default" : required ? "outline" : "secondary"}>
        {done ? "Done" : required ? "Needed" : "Not yet"}
      </Badge>
    </div>
  );
}

export function OverviewScreen() {
  const fetcher = useCallback(() => getGoLiveCheck(), []);
  const { state, reload } = useAsyncData(fetcher);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-foreground">Overview</h1>
      <DataView
        state={state}
        onRetry={reload}
        skeleton={<Skeleton className="h-64 w-full" />}
      >
        {(check: GoLiveCheck) => (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>Go-live checklist</CardTitle>
                <Badge variant={check.ready_for_go_live ? "default" : "secondary"}>
                  {check.ready_for_go_live ? "Ready for go-live" : "Not ready yet"}
                </Badge>
              </div>
              <CardDescription>
                A site key only moves from draft to live through a server-side check. The two
                starred items below are that check; the server enforces this even if a request
                goes straight to the API.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <ChecklistItem
                label="At least one contact method (email, phone or contact link) *"
                done={check.has_contact_method}
                required
              />
              <ChecklistItem
                label="At least one allowed origin on a site key *"
                done={check.has_allowed_origin}
                required
              />
              <ChecklistItem
                label="At least one verified domain"
                done={check.has_verified_domain}
                required={false}
              />
              <ChecklistItem
                label="At least one enabled source"
                done={check.has_enabled_source}
                required={false}
              />
            </CardContent>
          </Card>
        )}
      </DataView>
    </div>
  );
}
