// Step 6.2: extracted at the n=4 duplication check (rule 9) -- identical
// in SettingsScreen.tsx and WidgetScreen.tsx, and the same "no internals,
// a safe generic message per status code" shape every 6.3/6.4 screen's
// own save handlers will need too.
import { ApiError } from "@/lib/api";

export function errorMessageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.status === 422) {
    return "Please check the values above and try again.";
  }
  if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
    return "You do not have permission to do this. Try signing in again.";
  }
  return fallback;
}
