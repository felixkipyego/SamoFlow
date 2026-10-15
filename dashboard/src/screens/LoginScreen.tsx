import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, ensureCsrfCookie, login, type DashboardSessionResponse } from "@/lib/api";

interface LoginScreenProps {
  onLoggedIn: (session: DashboardSessionResponse) => void;
}

export function LoginScreen({ onLoggedIn }: LoginScreenProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Primes the CSRF cookie before the form can ever be submitted --
    // see src/lib/api.ts's own header comment.
    void ensureCsrfCookie();
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const session = await login(email, password);
      onLoggedIn(session);
    } catch (err) {
      // Deliberately the SAME generic message regardless of the real
      // cause (wrong password, unknown email, rate limited) -- matches
      // the backend's own "no oracle" failure shape, app/dashboard/
      // routes.py.
      if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts. Please wait and try again.");
      } else {
        setError("Email or password is incorrect.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          {/* shadcn's CardTitle renders a plain <div> (no `asChild`
           * support, confirmed by reading it directly) -- this login
           * page IS effectively the whole page, so it needs a real
           * landmark heading for screen-reader users navigating by
           * heading list. A real <h1>, with CardTitle's own classes
           * applied directly, not a second visual element alongside it. */}
          <h1 className="font-heading text-base leading-snug font-medium">Sign in</h1>
          <CardDescription>Sign in to your SamoFlow dashboard.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={submitting} className="w-full">
              {submitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
