// Step 6.2: docs/SPEC.md §12's own "Tenant sidebar: Overview, Widget,
// Sources, Inbox, Usage, Team, Settings" -- Team omitted (POST-PILOT,
// self-signup/invites, docs/DECISIONS_REGISTER.md's D-postpilot-
// admin-overrides). Hand-built from design tokens only (bg-sidebar/
// text-sidebar-foreground/bg-sidebar-accent, src/index.css's own
// `@theme` registration) -- shadcn's own "sidebar" block is reference
// only (D-shadcn's own constraint), not pulled in as a dependency for
// what six plain nav buttons do not need.
import type { ScreenKey } from "@/screens/screens";

interface SidebarProps {
  active: ScreenKey;
  onNavigate: (screen: ScreenKey) => void;
  openEscalationCount: number | null;
}

const NAV_ITEMS: { key: ScreenKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "widget", label: "Widget" },
  { key: "sources", label: "Sources" },
  { key: "inbox", label: "Inbox" },
  { key: "usage", label: "Usage" },
  { key: "settings", label: "Settings" },
];

export function Sidebar({ active, onNavigate, openEscalationCount }: SidebarProps) {
  return (
    <nav
      aria-label="Dashboard sections"
      className="flex w-56 shrink-0 flex-col gap-1 border-r border-sidebar-border bg-sidebar p-3"
    >
      {NAV_ITEMS.map((item) => {
        const isActive = item.key === active;
        return (
          <button
            key={item.key}
            type="button"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onNavigate(item.key)}
            className={`flex items-center justify-between rounded-md px-3 py-2 text-left text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
              isActive
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            }`}
          >
            <span>{item.label}</span>
            {item.key === "inbox" && openEscalationCount !== null && openEscalationCount > 0 && (
              <span
                className="rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground tabular-nums"
                aria-label={`${openEscalationCount} open escalations`}
              >
                {openEscalationCount}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
