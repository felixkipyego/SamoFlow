// Step 6.2: extracted at the n=4 duplication check (rule 9) -- the one
// small save-failure Alert every settings/sources/escalation card in
// this dashboard renders, kept as a single shared component so a future
// fix (like DataView's own axe contrast fix) only has one definition to
// find.
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function SaveError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <Alert variant="destructive" className="mt-3">
      <AlertTitle>Could not save</AlertTitle>
      <AlertDescription className="!text-destructive">{message}</AlertDescription>
    </Alert>
  );
}
