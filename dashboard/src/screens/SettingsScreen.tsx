// Step 6.2: Settings -- support details, alert/escalation email,
// retention, trace-text switch, site keys and allowed origins. docs/
// SPEC.md §12: "retention controls within the allowed ranges, with the
// effect explained before saving" and "changing site keys or allowed
// origins" needs recent authentication (already enforced server-side;
// a stale-session 403 here surfaces through the same generic error
// card every other save failure does, since this screen cannot tell
// recent-auth-expired apart from any other 403 without the server
// naming it, and inventing a THIRD client-side meaning for 403 beyond
// "permission denied" risks disagreeing with the server's own
// taxonomy later).
import { useCallback, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { DataView } from "@/components/DataView";
import { ApiError } from "@/lib/api";
import {
  createSiteKey,
  getTenantConfig,
  listSiteKeys,
  updateAlertEmails,
  updateRetention,
  updateSiteKeyOrigins,
  updateSupportDetails,
  updateTraceText,
  type SiteKey,
  type TenantConfig,
} from "@/lib/tenant-api";
import { useAsyncData } from "@/lib/use-async-data";

function SaveError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <Alert variant="destructive" className="mt-3">
      <AlertTitle>Could not save</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

function errorMessageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.status === 422) {
    return "Please check the values above and try again.";
  }
  if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
    return "You do not have permission to do this. Try signing in again.";
  }
  return fallback;
}

function SupportDetailsCard({
  config,
  onSaved,
}: {
  config: TenantConfig;
  onSaved: () => void;
}) {
  const [email, setEmail] = useState(config.support_email ?? "");
  const [phone, setPhone] = useState(config.support_phone ?? "");
  const [contactLink, setContactLink] = useState(config.support_contact_link ?? "");
  const [hours, setHours] = useState(config.support_hours ?? "");
  const [message, setMessage] = useState(config.support_message ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateSupportDetails({
        support_email: email || null,
        support_phone: phone || null,
        support_contact_link: contactLink || null,
        support_hours: hours || null,
        support_message: message || null,
      });
      onSaved();
    } catch (err) {
      setError(errorMessageFor(err, "This could not be saved. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Support details</CardTitle>
        <CardDescription>
          Shown to visitors who need a person, and used in the fallback message when the AI
          cannot help.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="support-email">Support email</Label>
          <Input
            id="support-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="support-phone">Support phone</Label>
          <Input id="support-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="support-contact-link">Contact page (https:// only)</Label>
          <Input
            id="support-contact-link"
            value={contactLink}
            onChange={(e) => setContactLink(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="support-hours">Hours</Label>
          <Input
            id="support-hours"
            value={hours}
            onChange={(e) => setHours(e.target.value)}
            placeholder="Mon-Fri 9am-5pm"
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="support-message">Fallback message</Label>
          <Textarea
            id="support-message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Shown to a visitor when the AI cannot help."
          />
        </div>
        <SaveError message={error} />
        <Button onClick={handleSave} disabled={saving} className="self-start">
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}

function AlertEmailsCard({ config, onSaved }: { config: TenantConfig; onSaved: () => void }) {
  const [alertEmail, setAlertEmail] = useState(config.alert_email ?? "");
  const [escalationEmail, setEscalationEmail] = useState(config.escalation_email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateAlertEmails({
        alert_email: alertEmail || null,
        escalation_email: escalationEmail || null,
      });
      onSaved();
    } catch (err) {
      setError(errorMessageFor(err, "This could not be saved. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Alert and escalation email</CardTitle>
        <CardDescription>
          Escalation email receives every "talk to a person" request. Alert email receives usage
          warnings at 80% and 100% of your plan.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="escalation-email">Escalation email</Label>
          <Input
            id="escalation-email"
            type="email"
            value={escalationEmail}
            onChange={(e) => setEscalationEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="alert-email">Usage alert email</Label>
          <Input
            id="alert-email"
            type="email"
            value={alertEmail}
            onChange={(e) => setAlertEmail(e.target.value)}
          />
        </div>
        <SaveError message={error} />
        <Button onClick={handleSave} disabled={saving} className="self-start">
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}

function RetentionCard({ config, onSaved }: { config: TenantConfig; onSaved: () => void }) {
  const [retentionDays, setRetentionDays] = useState(String(config.retention_days));
  const [escalationMonths, setEscalationMonths] = useState(
    String(config.escalation_retention_months),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsedDays = Number.parseInt(retentionDays, 10);
  const shortening = Number.isFinite(parsedDays) && parsedDays < config.retention_days;

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateRetention({
        retention_days: Number.parseInt(retentionDays, 10),
        escalation_retention_months: Number.parseInt(escalationMonths, 10),
      });
      onSaved();
    } catch (err) {
      setError(errorMessageFor(err, "This could not be saved. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Retention</CardTitle>
        <CardDescription>
          How long chats and escalation requests are kept before they are permanently deleted.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="retention-days">Chat retention (1-90 days)</Label>
          <Input
            id="retention-days"
            type="number"
            min={1}
            max={90}
            value={retentionDays}
            onChange={(e) => setRetentionDays(e.target.value)}
          />
          {shortening && (
            <p className="text-sm text-muted-foreground">
              Shortening this deletes older chats at the next nightly sweep — this cannot be
              undone.
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="escalation-months">Escalation retention (1-24 months)</Label>
          <Input
            id="escalation-months"
            type="number"
            min={1}
            max={24}
            value={escalationMonths}
            onChange={(e) => setEscalationMonths(e.target.value)}
          />
        </div>
        <SaveError message={error} />
        <Button onClick={handleSave} disabled={saving} className="self-start">
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}

function TraceTextCard({ config, onSaved }: { config: TenantConfig; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(config.trace_text_enabled);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleToggle(next: boolean) {
    setEnabled(next);
    setSaving(true);
    setError(null);
    try {
      await updateTraceText({ trace_text_enabled: next });
      onSaved();
    } catch (err) {
      setEnabled(!next);
      setError(errorMessageFor(err, "This could not be saved. Please try again."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Diagnostic trace text</CardTitle>
        <CardDescription>
          When on, the text of conversations is kept in diagnostic traces for up to 30 days (less
          if your chat retention is shorter). Turn off to exclude message text from traces
          entirely.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <Switch
            id="trace-text"
            checked={enabled}
            onCheckedChange={handleToggle}
            disabled={saving}
          />
          <Label htmlFor="trace-text">Keep message text in diagnostic traces</Label>
        </div>
        <SaveError message={error} />
      </CardContent>
    </Card>
  );
}

function SiteKeysCard() {
  const fetcher = useCallback(() => listSiteKeys(), []);
  const { state, reload } = useAsyncData(fetcher);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function handleCreate() {
    setCreating(true);
    setCreateError(null);
    try {
      await createSiteKey();
      reload();
    } catch (err) {
      setCreateError(errorMessageFor(err, "Could not create a site key. Please try again."));
    } finally {
      setCreating(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Site keys and allowed origins</CardTitle>
        <CardDescription>
          Each site key is tied to the exact origins it may be embedded on. Changing these
          requires a recent sign-in.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <DataView
          state={state}
          onRetry={reload}
          skeleton={<Skeleton className="h-20 w-full" />}
          isEmpty={(keys) => keys.length === 0}
          empty={
            <p className="text-sm text-muted-foreground">No site keys yet. Create one below.</p>
          }
        >
          {(keys: SiteKey[]) => (
            <div className="flex flex-col gap-4">
              {keys.map((key) => (
                <SiteKeyRow key={key.id} siteKey={key} onSaved={reload} />
              ))}
            </div>
          )}
        </DataView>
        <Separator />
        <SaveError message={createError} />
        <Button variant="outline" onClick={handleCreate} disabled={creating} className="self-start">
          {creating ? "Creating…" : "Create a new site key"}
        </Button>
      </CardContent>
    </Card>
  );
}

function SiteKeyRow({ siteKey, onSaved }: { siteKey: SiteKey; onSaved: () => void }) {
  const [origins, setOrigins] = useState(siteKey.allowed_origins.join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const list = origins
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0);
      await updateSiteKeyOrigins(siteKey.id, list);
      onSaved();
    } catch (err) {
      setError(
        errorMessageFor(
          err,
          "This could not be saved — check each origin is scheme://host (e.g. https://example.com).",
        ),
      );
    } finally {
      setSaving(false);
    }
  }

  const statusVariant = siteKey.status === "live" ? "default" : "secondary";

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border p-4">
      <div className="flex items-center justify-between gap-2">
        <code className="text-sm">{siteKey.key}</code>
        <Badge variant={statusVariant}>{siteKey.status}</Badge>
      </div>
      <Label htmlFor={`origins-${siteKey.id}`}>Allowed origins (one per line)</Label>
      <Textarea
        id={`origins-${siteKey.id}`}
        value={origins}
        onChange={(e) => setOrigins(e.target.value)}
        placeholder="https://example.com"
      />
      <SaveError message={error} />
      <Button variant="outline" size="sm" onClick={handleSave} disabled={saving} className="self-start">
        {saving ? "Saving…" : "Save origins"}
      </Button>
    </div>
  );
}

export function SettingsScreen() {
  const fetcher = useCallback(() => getTenantConfig(), []);
  const { state, reload } = useAsyncData(fetcher);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-foreground">Settings</h1>
      <DataView
        state={state}
        onRetry={reload}
        skeleton={
          <div className="flex flex-col gap-4">
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        }
      >
        {(config) => (
          <div className="flex flex-col gap-6">
            <SupportDetailsCard config={config} onSaved={reload} />
            <AlertEmailsCard config={config} onSaved={reload} />
            <RetentionCard config={config} onSaved={reload} />
            <TraceTextCard config={config} onSaved={reload} />
            <SiteKeysCard />
          </div>
        )}
      </DataView>
    </div>
  );
}
