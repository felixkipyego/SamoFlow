import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardShell } from "./DashboardShell";
import * as api from "@/lib/api";
import * as escalationApi from "@/lib/escalation-api";
import * as tenantApi from "@/lib/tenant-api";

const session = {
  user: { id: "u1", email: "owner@example.com" },
  tenant: { id: "t1", name: "Acme Inc" },
};

describe("DashboardShell", () => {
  beforeEach(() => {
    // DashboardShell's default screen is Overview, which fetches the
    // real go-live checklist on mount -- mocked here so these tests
    // (none of which are about Overview itself) don't leave a real,
    // unmocked fetch() pending past the test's own end (a real act()
    // warning, found live once OverviewScreen stopped being a static
    // placeholder).
    vi.spyOn(tenantApi, "getGoLiveCheck").mockResolvedValue({
      has_contact_method: false,
      has_allowed_origin: false,
      has_verified_domain: false,
      has_enabled_source: false,
      ready_for_go_live: false,
    });
    // DashboardShell also fetches the sidebar's own open-escalation
    // count unconditionally on mount, regardless of which screen is
    // active -- mocked for the identical reason as getGoLiveCheck
    // above.
    vi.spyOn(escalationApi, "getOpenEscalationCount").mockResolvedValue({ count: 0 });
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });

  it("shows the tenant name and the signed-in user's email", async () => {
    render(<DashboardShell session={session} onLoggedOut={vi.fn()} />);
    expect(screen.getByText("Acme Inc")).toBeInTheDocument();
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    // Lets the default Overview screen's own mocked fetch settle before
    // the test ends -- otherwise its state update lands after this test
    // already returned (a real act() warning, found live).
    await screen.findByText("Not ready yet");
  });

  it("does not log out immediately -- a confirmation dialog appears first", async () => {
    const logoutSpy = vi.spyOn(api, "logout").mockResolvedValue(undefined);
    render(<DashboardShell session={session} onLoggedOut={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /log out/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(logoutSpy).not.toHaveBeenCalled();
  });

  it("calls the real logout endpoint and onLoggedOut only after confirming", async () => {
    const logoutSpy = vi.spyOn(api, "logout").mockResolvedValue(undefined);
    const onLoggedOut = vi.fn();
    render(<DashboardShell session={session} onLoggedOut={onLoggedOut} />);

    await userEvent.click(screen.getByRole("button", { name: /log out/i }));
    const dialog = await screen.findByRole("dialog");
    const confirmButton = Array.from(dialog.querySelectorAll("button")).find((b) =>
      b.textContent?.match(/^log out$/i),
    )!;
    await userEvent.click(confirmButton);

    await waitFor(() => expect(logoutSpy).toHaveBeenCalled());
    await waitFor(() => expect(onLoggedOut).toHaveBeenCalled());
  });

  it("toggles the dark theme and persists the choice", async () => {
    render(<DashboardShell session={session} onLoggedOut={vi.fn()} />);

    const toggle = screen.getByRole("button", { name: "Dark mode" });
    await userEvent.click(toggle);

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("samoflow-dashboard-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Light mode" })).toBeInTheDocument();
  });

  it("still calls onLoggedOut even if the logout request fails", async () => {
    vi.spyOn(api, "logout").mockRejectedValue(new api.ApiError(500, "boom"));
    const onLoggedOut = vi.fn();
    render(<DashboardShell session={session} onLoggedOut={onLoggedOut} />);

    await userEvent.click(screen.getByRole("button", { name: /log out/i }));
    const dialog = await screen.findByRole("dialog");
    const confirmButton = Array.from(dialog.querySelectorAll("button")).find((b) =>
      b.textContent?.match(/^log out$/i),
    )!;
    await userEvent.click(confirmButton);

    await waitFor(() => expect(onLoggedOut).toHaveBeenCalled());
  });
});
