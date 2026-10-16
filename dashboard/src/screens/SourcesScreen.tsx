// Step 6.3: Sources -- the sources table, add-source flows (urls/crawl/
// upload), per-source disable/delete/refresh-now, the "may need
// JavaScript" flag, the "answerable to any visitor" upload warning, and
// domain verification. docs/SPEC.md §12: "Destructive actions confirm
// and say exactly what will be removed."
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { DataView } from "@/components/DataView";
import { SaveError } from "@/components/SaveError";
import { errorMessageFor } from "@/lib/error-messages";
import {
  claimDomain,
  createCrawlSource,
  createUrlsSource,
  deleteSource,
  disableSource,
  enableSource,
  listDomains,
  listSources,
  refreshSourceNow,
  uploadFile,
  verifyDomain,
  type Domain,
  type Source,
} from "@/lib/sources-api";
import { useAsyncData } from "@/lib/use-async-data";

const REFRESH_INTERVALS = ["daily", "weekly", "monthly", "quarterly"] as const;

function statusBadgeVariant(status: Source["status"]): "default" | "secondary" | "destructive" {
  if (status === "error") return "destructive";
  if (status === "ok") return "default";
  return "secondary";
}

function SourceRow({ source, onChanged }: { source: Source; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);

  async function run(action: () => Promise<Source>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(errorMessageFor(err, "This action could not be completed. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    setError(null);
    try {
      await deleteSource(source.id);
      onChanged();
    } catch (err) {
      setError(errorMessageFor(err, "This could not be deleted. Please try again."));
      setBusy(false);
    }
  }

  const urlsOrSeed =
    source.type === "urls"
      ? (source.config.urls as string[] | undefined)?.join(", ")
      : source.type === "crawl"
        ? (source.config.seed_url as string | undefined)
        : source.type === "upload"
          ? `${(source.config.uploads as unknown[] | undefined)?.length ?? 0} file(s)`
          : "";

  return (
    <TableRow>
      <TableCell className="font-medium">{source.type}</TableCell>
      <TableCell className="max-w-64 truncate text-muted-foreground">{urlsOrSeed}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Badge variant={statusBadgeVariant(source.status)}>{source.status}</Badge>
          {source.may_need_javascript && (
            <Badge variant="secondary" title="This page may need JavaScript to render fully">
              may need JavaScript
            </Badge>
          )}
          {source.exceeds_plan && <Badge variant="destructive">exceeds plan</Badge>}
        </div>
      </TableCell>
      <TableCell className="tabular-nums text-sm text-muted-foreground">
        {source.last_run_at ? new Date(source.last_run_at).toLocaleString() : "never"}
      </TableCell>
      <TableCell className="tabular-nums text-sm text-muted-foreground">
        {source.next_run_at ? new Date(source.next_run_at).toLocaleString() : "—"}
      </TableCell>
      <TableCell className="tabular-nums">{source.document_count}</TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {source.enabled ? (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => run(() => disableSource(source.id))}
            >
              Disable
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => run(() => enableSource(source.id))}
            >
              Enable
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={busy || !source.enabled}
            onClick={() => run(() => refreshSourceNow(source.id))}
          >
            Refresh now
          </Button>
          <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={busy}>
                Delete
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete this source?</DialogTitle>
                <DialogDescription>
                  This permanently removes {source.document_count} indexed page
                  {source.document_count === 1 ? "" : "s"} from this source, and the visitor
                  widget can no longer answer from its content. This cannot be undone.
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
        <TableCell colSpan={7}>
          <SaveError message={error} />
        </TableCell>
      )}
    </TableRow>
  );
}

function AddUrlsForm({ onAdded }: { onAdded: () => void }) {
  const [urls, setUrls] = useState("");
  const [refreshInterval, setRefreshInterval] = useState("daily");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setSaving(true);
    setError(null);
    try {
      const list = urls
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      await createUrlsSource(list, refreshInterval);
      setUrls("");
      onAdded();
    } catch (err) {
      setError(errorMessageFor(err, "This source could not be added. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Label htmlFor="add-urls">Page URLs (one per line, verified domains only)</Label>
      <Textarea
        id="add-urls"
        value={urls}
        onChange={(e) => setUrls(e.target.value)}
        placeholder="https://example.com/about"
      />
      <Label htmlFor="add-urls-interval">Refresh interval</Label>
      <Select value={refreshInterval} onValueChange={setRefreshInterval}>
        <SelectTrigger id="add-urls-interval">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {REFRESH_INTERVALS.map((interval) => (
            <SelectItem key={interval} value={interval}>
              {interval}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <SaveError message={error} />
      <Button onClick={handleSubmit} disabled={saving || urls.trim() === ""} className="self-start">
        {saving ? "Adding…" : "Add URLs"}
      </Button>
    </div>
  );
}

function AddCrawlForm({ onAdded }: { onAdded: () => void }) {
  const [seedUrl, setSeedUrl] = useState("");
  const [refreshInterval, setRefreshInterval] = useState("daily");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setSaving(true);
    setError(null);
    try {
      await createCrawlSource(seedUrl, refreshInterval);
      setSeedUrl("");
      onAdded();
    } catch (err) {
      setError(
        errorMessageFor(
          err,
          "This source could not be added — the domain must be verified first.",
        ),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Label htmlFor="add-crawl-seed">Starting page (verified domain only)</Label>
      <Input
        id="add-crawl-seed"
        value={seedUrl}
        onChange={(e) => setSeedUrl(e.target.value)}
        placeholder="https://example.com/"
      />
      <Label htmlFor="add-crawl-interval">Refresh interval</Label>
      <Select value={refreshInterval} onValueChange={setRefreshInterval}>
        <SelectTrigger id="add-crawl-interval">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {REFRESH_INTERVALS.map((interval) => (
            <SelectItem key={interval} value={interval}>
              {interval}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <SaveError message={error} />
      <Button onClick={handleSubmit} disabled={saving || seedUrl.trim() === ""} className="self-start">
        {saving ? "Adding…" : "Add crawl"}
      </Button>
    </div>
  );
}

function AddUploadForm({ onAdded }: { onAdded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    if (!file) return;
    setSaving(true);
    setError(null);
    try {
      await uploadFile(file);
      setFile(null);
      setConfirmed(false);
      onAdded();
    } catch (err) {
      setError(
        errorMessageFor(
          err,
          "This file could not be uploaded (allowed types: PDF, .docx, .txt, .md).",
        ),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Label htmlFor="add-upload-file">File (PDF, .docx, .txt or .md)</Label>
      <Input
        id="add-upload-file"
        type="file"
        accept=".pdf,.docx,.txt,.md"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      {file && (
        <label className="flex items-start gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            I understand this file&apos;s content will be answerable to any visitor who asks the
            widget about it.
          </span>
        </label>
      )}
      <SaveError message={error} />
      <Button onClick={handleSubmit} disabled={saving || !file || !confirmed} className="self-start">
        {saving ? "Uploading…" : "Upload"}
      </Button>
    </div>
  );
}

function AddSourceDialog({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);

  function handleAdded() {
    setOpen(false);
    onAdded();
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>Add a source</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a source</DialogTitle>
          <DialogDescription>
            Pages, a crawl, or an uploaded file the widget can answer from.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="urls">
          <TabsList>
            <TabsTrigger value="urls">URLs</TabsTrigger>
            <TabsTrigger value="crawl">Crawl</TabsTrigger>
            <TabsTrigger value="upload">Upload</TabsTrigger>
          </TabsList>
          <TabsContent value="urls">
            <AddUrlsForm onAdded={handleAdded} />
          </TabsContent>
          <TabsContent value="crawl">
            <AddCrawlForm onAdded={handleAdded} />
          </TabsContent>
          <TabsContent value="upload">
            <AddUploadForm onAdded={handleAdded} />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function DomainVerificationCard() {
  const fetcher = useCallback(() => listDomains(), []);
  const { state, reload } = useAsyncData(fetcher);
  const [domain, setDomain] = useState("");
  const [method, setMethod] = useState<"dns" | "file" | "meta_tag">("dns");
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClaim() {
    setClaiming(true);
    setError(null);
    try {
      await claimDomain(domain, method);
      setDomain("");
      reload();
    } catch (err) {
      setError(errorMessageFor(err, "This domain could not be claimed. Please try again."));
    } finally {
      setClaiming(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Domain verification</CardTitle>
        <CardDescription>
          Crawling and listed pages only work on domains you have verified you own.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <DataView
          state={state}
          onRetry={reload}
          skeleton={<Skeleton className="h-16 w-full" />}
          isEmpty={(domains) => domains.length === 0}
          empty={<p className="text-sm text-muted-foreground">No domains claimed yet.</p>}
        >
          {(domains: Domain[]) => (
            <div className="flex flex-col gap-3">
              {domains.map((d) => (
                <DomainRow key={d.id} domain={d} onChanged={reload} />
              ))}
            </div>
          )}
        </DataView>
        <div className="flex flex-col gap-2 border-t border-border pt-4">
          <Label htmlFor="claim-domain">Claim a new domain</Label>
          <div className="flex gap-2">
            <Input
              id="claim-domain"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="example.com"
            />
            <Select value={method} onValueChange={(v) => setMethod(v as typeof method)}>
              <SelectTrigger className="w-40" aria-label="Verification method">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="dns">DNS record</SelectItem>
                <SelectItem value="file">File upload</SelectItem>
                <SelectItem value="meta_tag">Meta tag</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={handleClaim} disabled={claiming || domain.trim() === ""}>
              {claiming ? "Claiming…" : "Claim"}
            </Button>
          </div>
          <SaveError message={error} />
        </div>
      </CardContent>
    </Card>
  );
}

function DomainRow({ domain, onChanged }: { domain: Domain; onChanged: () => void }) {
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleVerify() {
    setVerifying(true);
    setError(null);
    try {
      await verifyDomain(domain.id);
      onChanged();
    } catch (err) {
      setError(
        errorMessageFor(err, "Verification did not succeed yet — check the instructions below."),
      );
    } finally {
      setVerifying(false);
    }
  }

  const instructions =
    domain.method === "dns"
      ? `Add a DNS TXT record on ${domain.domain} with value: samoflow-verify=${domain.verification_token}`
      : domain.method === "file"
        ? `Upload a file at https://${domain.domain}/.well-known/samoflow-verify-${domain.verification_token}.txt`
        : `Add <meta name="samoflow-verify" content="${domain.verification_token}"> to ${domain.domain}'s homepage`;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{domain.domain}</span>
        <Badge variant={domain.status === "verified" ? "default" : "secondary"}>
          {domain.status}
        </Badge>
      </div>
      {domain.status === "pending" && (
        <>
          <p className="text-sm text-muted-foreground">{instructions}</p>
          <SaveError message={error} />
          <Button
            variant="outline"
            size="sm"
            onClick={handleVerify}
            disabled={verifying}
            className="self-start"
          >
            {verifying ? "Checking…" : "Check verification"}
          </Button>
        </>
      )}
    </div>
  );
}

export function SourcesScreen() {
  const fetcher = useCallback(() => listSources(), []);
  const { state, reload } = useAsyncData(fetcher);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Sources</h1>
        <AddSourceDialog onAdded={reload} />
      </div>
      <DataView
        state={state}
        onRetry={reload}
        skeleton={<Skeleton className="h-48 w-full" />}
        isEmpty={(list) => list.sources.length === 0}
        empty={
          <p className="text-sm text-muted-foreground">
            No sources yet. Add one to let the widget answer from your content.
          </p>
        }
      >
        {(list) => (
          <div className="flex flex-col gap-2">
            {list.total_indexed_pages_limit !== null && (
              <p className="text-sm tabular-nums text-muted-foreground">
                {list.total_indexed_pages} / {list.total_indexed_pages_limit} indexed pages used
              </p>
            )}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last refresh</TableHead>
                  <TableHead>Next refresh</TableHead>
                  <TableHead>Pages</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.sources.map((source) => (
                  <SourceRow key={source.id} source={source} onChanged={reload} />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DataView>
      <DomainVerificationCard />
    </div>
  );
}
