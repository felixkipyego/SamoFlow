// Step 6.2: Widget -- appearance and the install snippet. docs/SPEC.md
// §12's own live-preview feature is deliberately NOT built here (POST-
// PILOT, docs/DECISIONS_REGISTER.md: a new postMessage attack surface
// and a widget/dashboard release-cadence coupling, for a feature the
// pilot's own "save, then open the real widget on a real test page"
// path already covers).
import { useCallback, useState } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { DataView } from "@/components/DataView";
import { SaveError } from "@/components/SaveError";
import { errorMessageFor } from "@/lib/error-messages";
import { getInstallSnippet, getTenantConfig, updateWidgetLook, type TenantConfig } from "@/lib/tenant-api";
import { useAsyncData } from "@/lib/use-async-data";

const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

function WidgetLookCard({ config, onSaved }: { config: TenantConfig; onSaved: () => void }) {
  const [title, setTitle] = useState(config.title);
  const [greeting, setGreeting] = useState(config.greeting);
  const [primaryColor, setPrimaryColor] = useState(config.colors.primary);
  const [logoUrl, setLogoUrl] = useState(config.logo_url ?? "");
  const [position, setPosition] = useState<"left" | "right">(config.position);
  const [privacyNoticeUrl, setPrivacyNoticeUrl] = useState(config.privacy_notice_url ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const colorIsValidHex = HEX_COLOR_PATTERN.test(primaryColor);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateWidgetLook({
        title,
        greeting,
        colors: { primary: primaryColor },
        logo_url: logoUrl || null,
        position,
        privacy_notice_url: privacyNoticeUrl || null,
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
        <CardTitle>Appearance</CardTitle>
        <CardDescription>How the chat widget looks and greets your visitors.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="widget-title">Title</Label>
          <Input id="widget-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="widget-greeting">Greeting</Label>
          <Input
            id="widget-greeting"
            value={greeting}
            onChange={(e) => setGreeting(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="widget-color">Primary color</Label>
          <div className="flex items-center gap-2">
            <input
              type="color"
              aria-label="Pick a primary color"
              value={colorIsValidHex ? primaryColor : "#4f46e5"}
              onChange={(e) => setPrimaryColor(e.target.value)}
              className="h-9 w-9 rounded-md border border-input"
            />
            <Input
              id="widget-color"
              value={primaryColor}
              onChange={(e) => setPrimaryColor(e.target.value)}
              placeholder="#4f46e5"
              aria-invalid={!colorIsValidHex}
            />
          </div>
          {!colorIsValidHex && (
            <p className="text-sm text-destructive">Must be a hex color, e.g. #4f46e5.</p>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="widget-logo">Logo URL (https:// only)</Label>
          <Input id="widget-logo" value={logoUrl} onChange={(e) => setLogoUrl(e.target.value)} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="widget-position">Position</Label>
          <Select value={position} onValueChange={(value) => setPosition(value as "left" | "right")}>
            <SelectTrigger id="widget-position">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="left">Left</SelectItem>
              <SelectItem value="right">Right</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="widget-privacy">Privacy notice URL (https:// only)</Label>
          <Input
            id="widget-privacy"
            value={privacyNoticeUrl}
            onChange={(e) => setPrivacyNoticeUrl(e.target.value)}
          />
        </div>
        <SaveError message={error} />
        <Button onClick={handleSave} disabled={saving || !colorIsValidHex} className="self-start">
          {saving ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}

function InstallSnippetCard() {
  const fetcher = useCallback(() => getInstallSnippet(), []);
  const { state, reload } = useAsyncData(fetcher);
  const [copied, setCopied] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Install snippet</CardTitle>
        <CardDescription>Paste this once into your site, right before &lt;/body&gt;.</CardDescription>
      </CardHeader>
      <CardContent>
        <DataView
          state={state}
          onRetry={reload}
          skeleton={<Skeleton className="h-16 w-full" />}
        >
          {(snippet) =>
            snippet.base_url === null ? (
              <Alert>
                <AlertTitle>Not ready yet</AlertTitle>
                <AlertDescription>
                  Ask your platform admin for the base URL before installing the widget.
                </AlertDescription>
              </Alert>
            ) : (
              <div className="flex flex-col gap-3">
                <pre className="overflow-x-auto rounded-md bg-muted p-4 text-sm">
                  <code>{`<script src="${snippet.base_url}/widget/loader.js" data-site-key="${snippet.site_key}" data-base-url="${snippet.base_url}"></script>`}</code>
                </pre>
                <Button
                  variant="outline"
                  className="self-start"
                  onClick={async () => {
                    await navigator.clipboard.writeText(
                      `<script src="${snippet.base_url}/widget/loader.js" data-site-key="${snippet.site_key}" data-base-url="${snippet.base_url}"></script>`,
                    );
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  }}
                >
                  {copied ? "Copied" : "Copy to clipboard"}
                </Button>
              </div>
            )
          }
        </DataView>
      </CardContent>
    </Card>
  );
}

export function WidgetScreen() {
  const fetcher = useCallback(() => getTenantConfig(), []);
  const { state, reload } = useAsyncData(fetcher);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-foreground">Widget</h1>
      <DataView
        state={state}
        onRetry={reload}
        skeleton={<Skeleton className="h-64 w-full" />}
      >
        {(config) => (
          <div className="flex flex-col gap-6">
            <WidgetLookCard config={config} onSaved={reload} />
            <InstallSnippetCard />
          </div>
        )}
      </DataView>
    </div>
  );
}
