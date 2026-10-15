import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DashboardShell } from "./DashboardShell";
import * as api from "@/lib/api";

const session = {
  user: { id: "u1", email: "owner@example.com" },
  tenant: { id: "t1", name: "Acme Inc" },
};

describe("DashboardShell", () => {
  it("shows the tenant name and the signed-in user's email", () => {
    render(<DashboardShell session={session} onLoggedOut={vi.fn()} />);
    expect(screen.getByText("Acme Inc")).toBeInTheDocument();
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
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
