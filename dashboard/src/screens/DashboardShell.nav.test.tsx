// Step 6.2: sidebar navigation actually switches the rendered screen.
// A separate file from DashboardShell.test.tsx (Step 6.1) rather than
// appended to it -- that file is scoped to the logout flow alone.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DashboardShell } from "./DashboardShell";
import * as tenantApi from "@/lib/tenant-api";

const session = {
  user: { id: "u1", email: "owner@example.com" },
  tenant: { id: "t1", name: "Acme Inc" },
};

describe("DashboardShell navigation", () => {
  it("starts on Overview and switches screens when a sidebar item is clicked", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockImplementation(() => new Promise(() => {}));
    render(<DashboardShell session={session} onLoggedOut={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
