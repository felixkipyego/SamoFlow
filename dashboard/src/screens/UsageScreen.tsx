// Step 6.4: the usage meter -- AI-answered messages against the plan's
// monthly quota, with distinct 80%/100% states matching the usage-alert
// emails (app/ingest/job_handlers.py's handle_usage_alert_sweep()). The
// backend reads the SAME authoritative usage_events count that sweep
// reads, so this screen can never disagree with an alert email the
// tenant may have already received this month.
import { useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DataView } from "@/components/DataView";
import { getUsage, type Usage } from "@/lib/escalation-api";
import { useAsyncData } from "@/lib/use-async-data";

function stateBadgeVariant(state: Usage["state"]): "default" | "secondary" | "outline" {
  // "destructive" was tried here first and dropped: this theme's
  // destructive color measures below WCAG AA contrast on a Badge (a
  // real axe-core finding, found live on OverviewScreen's own
  // checklist badges) -- these three already-proven-safe variants
  // give three visually distinct states without fighting that color.
  if (state === "exceeded") return "outline";
  if (state === "warning") return "secondary";
  return "default";
}

function stateLabel(state: Usage["state"]): string {
  if (state === "no_plan") return "No plan assigned";
  if (state === "exceeded") return "100% used";
  if (state === "warning") return "80% used";
  return "Normal";
}

export function UsageScreen() {
  const fetcher = useCallback(() => getUsage(), []);
  const { state, reload } = useAsyncData(fetcher);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-foreground">Usage</h1>
      <DataView state={state} onRetry={reload} skeleton={<Skeleton className="h-40 w-full" />}>
        {(usage: Usage) => (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>AI-answered messages this month</CardTitle>
                <Badge variant={stateBadgeVariant(usage.state)}>{stateLabel(usage.state)}</Badge>
              </div>
              <CardDescription>
                Counts only messages the AI answered -- escalations don't count against this
                quota.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {usage.plan_assigned ? (
                <>
                  <p className="text-3xl font-semibold tabular-nums text-foreground">
                    {usage.used_this_month}
                    <span className="text-base font-normal text-muted-foreground">
                      {" "}
                      / {usage.quota} messages
                    </span>
                  </p>
                  {usage.state === "exceeded" && (
                    <p className="text-sm text-muted-foreground">
                      You have used 100% or more of your monthly AI message quota.
                    </p>
                  )}
                  {usage.state === "warning" && (
                    <p className="text-sm text-muted-foreground">
                      You have used 80% or more of your monthly AI message quota.
                    </p>
                  )}
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No plan is assigned to this account yet, so there is no quota to compare
                  against. {usage.used_this_month} AI-answered message
                  {usage.used_this_month === 1 ? "" : "s"} recorded this month.
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </DataView>
    </div>
  );
}
