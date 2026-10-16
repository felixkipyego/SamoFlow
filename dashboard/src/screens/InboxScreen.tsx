// Step 6.4: the escalation inbox -- list/transcript/status/delete,
// tenant-scoped. Transcripts are already markdown-unescaped by the
// server (app/text_escaping.py's shared helper) and are rendered here
// as a plain text node (`whitespace-pre-wrap`, never
// dangerouslySetInnerHTML) -- the "then HTML-escape" half of docs/
// SPEC.md §7's own "always escaped on output" rule, satisfied by
// construction: React can never reinterpret a text child as markup.
import { useCallback, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DataView } from "@/components/DataView";
import { SaveError } from "@/components/SaveError";
import {
  deleteEscalation,
  getEscalation,
  listEscalations,
  updateEscalationStatus,
  type EscalationDetail,
  type EscalationSummary,
} from "@/lib/escalation-api";
import { errorMessageFor } from "@/lib/error-messages";
import { useAsyncData } from "@/lib/use-async-data";

function statusBadgeVariant(status: EscalationSummary["status"]): "default" | "secondary" | "outline" {
  if (status === "new") return "default";
  if (status === "seen") return "secondary";
  return "outline";
}

function contactSummary(escalation: EscalationSummary): string {
  const parts = [escalation.contact_name, escalation.contact_email, escalation.contact_phone].filter(
    (p): p is string => Boolean(p),
  );
  return parts.length > 0 ? parts.join(" · ") : "No contact info provided";
}

function TranscriptDialog({ escalationId }: { escalationId: string }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<EscalationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next && detail === null) {
      try {
        setDetail(await getEscalation(escalationId));
      } catch (err) {
        setError(errorMessageFor(err, "This could not be loaded. Please try again."));
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          View
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Escalation transcript</DialogTitle>
          <DialogDescription>
            {detail ? contactSummary(detail) : "Loading…"}
          </DialogDescription>
        </DialogHeader>
        <SaveError message={error} />
        {detail && (
          <p className="whitespace-pre-wrap text-sm text-foreground">{detail.transcript}</p>
        )}
      </DialogContent>
    </Dialog>
  );
}

function EscalationRow({
  escalation,
  onChanged,
}: {
  escalation: EscalationSummary;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  async function handleStatus(status: "seen" | "resolved") {
    setBusy(true);
    setError(null);
    try {
      await updateEscalationStatus(escalation.id, status);
      onChanged();
    } catch (err) {
      setError(errorMessageFor(err, "This could not be updated. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    setError(null);
    try {
      await deleteEscalation(escalation.id);
      onChanged();
    } catch (err) {
      setError(errorMessageFor(err, "This could not be deleted. Please try again."));
      setBusy(false);
    }
  }

  return (
    <TableRow>
      <TableCell className="text-sm text-muted-foreground">
        {new Date(escalation.created_at).toLocaleString()}
      </TableCell>
      <TableCell className="max-w-64 truncate">{contactSummary(escalation)}</TableCell>
      <TableCell>
        <Badge variant={statusBadgeVariant(escalation.status)}>{escalation.status}</Badge>
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <TranscriptDialog escalationId={escalation.id} />
          {escalation.status === "new" && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => handleStatus("seen")}>
              Mark seen
            </Button>
          )}
          {escalation.status !== "resolved" && (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => handleStatus("resolved")}
            >
              Mark resolved
            </Button>
          )}
          <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={busy}>
                Delete
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete this escalation?</DialogTitle>
                <DialogDescription>
                  This permanently removes this escalation request and its transcript. This cannot
                  be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button
                  onClick={async () => {
                    await handleDelete();
                    setDeleteOpen(false);
                  }}
                  disabled={busy}
                >
                  {busy ? "Deleting…" : "Delete"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </TableCell>
      {error && (
        <TableCell colSpan={4}>
          <SaveError message={error} />
        </TableCell>
      )}
    </TableRow>
  );
}

export function InboxScreen({ onOpenCountChanged }: { onOpenCountChanged?: () => void }) {
  const fetcher = useCallback(() => listEscalations(), []);
  const { state, reload } = useAsyncData(fetcher);

  function handleChanged() {
    reload();
    onOpenCountChanged?.();
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-foreground">Inbox</h1>
      <Card>
        <CardHeader>
          <CardTitle>Escalation requests</CardTitle>
          <CardDescription>
            Visitors who asked to talk to a person. Transcripts are shown as plain text.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DataView
            state={state}
            onRetry={reload}
            isEmpty={(data) => data.escalations.length === 0}
            empty={<p className="text-sm text-muted-foreground">No escalation requests yet.</p>}
          >
            {(data) => (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Created</TableHead>
                    <TableHead>Contact</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.escalations.map((escalation) => (
                    <EscalationRow
                      key={escalation.id}
                      escalation={escalation}
                      onChanged={handleChanged}
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </DataView>
        </CardContent>
      </Card>
    </div>
  );
}
