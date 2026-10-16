// Step 6.2: the shared loading(skeleton)/error(no internals)/permission-
// denied/ready-or-empty renderer every data view in this dashboard uses.
import type { ReactNode } from "react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { AsyncState } from "@/lib/use-async-data";

interface DataViewProps<T> {
  state: AsyncState<T>;
  onRetry: () => void;
  skeleton?: ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}

export function DataView<T>({
  state,
  onRetry,
  skeleton,
  isEmpty,
  empty,
  children,
}: DataViewProps<T>) {
  if (state.status === "loading") {
    return (
      <div aria-live="polite" aria-busy="true">
        {skeleton ?? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}
      </div>
    );
  }

  if (state.status === "error") {
    if (state.status_code === 401 || state.status_code === 403) {
      return (
        <Alert variant="destructive">
          <AlertTitle>Permission denied</AlertTitle>
          {/* !text-destructive, not the bare destructive-variant
           * default: a real axe-core finding, found live (4.49:1,
           * below WCAG AA's 4.5:1) -- AlertDescription's own
           * destructive-variant text color is a 90%-opacity red
           * (shadcn's own generated alert.tsx, not hand-edited here,
           * per this project's own "generated diffs reviewed, not
           * patched after the fact" convention) -- full opacity
           * passes; the 90% reduction does not. `!` forces this at
           * the usage site rather than touching the generated file. */}
          <AlertDescription className="!text-destructive">
            You do not have access to this. Try signing in again.
          </AlertDescription>
        </Alert>
      );
    }
    return (
      <Alert variant="destructive">
        <AlertTitle>Something went wrong</AlertTitle>
        <AlertDescription className="!text-destructive">
          This could not be loaded. Please try again.
        </AlertDescription>
        {/* AlertAction, not a button nested inside AlertDescription: a
         * second real axe-core finding from the identical root cause --
         * CSS `color` inheritance was carrying the reduced-opacity red
         * down into a nested button's own text too. AlertAction is a
         * sibling slot, outside that rule's own selector, so the
         * button keeps the Button component's own (already-passing)
         * text color instead. */}
        <AlertAction>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </AlertAction>
      </Alert>
    );
  }

  if (isEmpty?.(state.data)) {
    return <>{empty}</>;
  }

  return <>{children(state.data)}</>;
}
