// Step 6.2: one small hook behind every data-fetching screen in this
// dashboard -- docs/SPEC.md §12's own acceptance line ("every data view
// has loading (skeleton), empty, error (no internals) and permission-
// denied states") applies identically to every screen 6.2-6.4 builds;
// a single shared hook means that property holds everywhere by
// construction, rather than five screens each reimplementing it
// slightly differently.
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "error"; status_code: number | null }
  | { status: "ready"; data: T };

export function useAsyncData<T>(fetcher: () => Promise<T>) {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });

  const load = useCallback(() => {
    setState({ status: "loading" });
    fetcher()
      .then((data) => setState({ status: "ready", data }))
      .catch((err) => {
        // "error (no internals)" -- the real ApiError.message (server
        // detail text) is never rendered; only its status code (401 vs
        // 403 vs anything else) ever reaches a screen, which is what
        // distinguishes "permission denied" from a generic error state.
        setState({ status: "error", status_code: err instanceof ApiError ? err.status : null });
      });
  }, [fetcher]);

  useEffect(() => {
    load();
  }, [load]);

  return { state, reload: load, setState };
}
