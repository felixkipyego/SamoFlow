import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SettingsScreen } from "./SettingsScreen";
import { ApiError } from "@/lib/api";
import * as tenantApi from "@/lib/tenant-api";

const BASE_CONFIG: tenantApi.TenantConfig = {
  support_email: null,
  support_phone: null,
  support_contact_link: null,
  support_hours: null,
  support_message: null,
  alert_email: null,
  escalation_email: null,
  title: "Chat",
  greeting: "Hi!",
  colors: { primary: "#4f46e5" },
  logo_url: null,
  position: "right",
  privacy_notice_url: null,
  retention_days: 30,
  escalation_retention_months: 12,
  trace_text_enabled: true,
};

describe("SettingsScreen", () => {
  it("shows a loading skeleton, then the real values once loaded", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockResolvedValue(BASE_CONFIG);
    vi.spyOn(tenantApi, "listSiteKeys").mockResolvedValue([]);

    render(<SettingsScreen />);
    await waitFor(() => expect(screen.getByLabelText("Support email")).toBeInTheDocument());
  });

  it("shows a permission-denied message on a 401, with no internal detail text", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockRejectedValue(new ApiError(401, "no cookie at all"));

    render(<SettingsScreen />);
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.queryByText("no cookie at all")).not.toBeInTheDocument();
  });

  it("shows a generic retryable error on a 500, with no internal detail text", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockRejectedValue(
      new ApiError(500, "db connection string leaked here"),
    );

    render(<SettingsScreen />);
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.queryByText("db connection string leaked here")).not.toBeInTheDocument();
  });

  it("saves support details and refetches on success", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockResolvedValue(BASE_CONFIG);
    vi.spyOn(tenantApi, "listSiteKeys").mockResolvedValue([]);
    const updateSpy = vi
      .spyOn(tenantApi, "updateSupportDetails")
      .mockResolvedValue({ ...BASE_CONFIG, support_email: "help@acme.example" });

    render(<SettingsScreen />);
    const emailInput = await screen.findByLabelText("Support email");
    await userEvent.type(emailInput, "help@acme.example");

    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    await userEvent.click(saveButtons[0]);

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ support_email: "help@acme.example" }),
      ),
    );
  });

  it("shows a save error without internal detail text when a save fails", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockResolvedValue(BASE_CONFIG);
    vi.spyOn(tenantApi, "listSiteKeys").mockResolvedValue([]);
    vi.spyOn(tenantApi, "updateSupportDetails").mockRejectedValue(
      new ApiError(500, "stack trace leaked here"),
    );

    render(<SettingsScreen />);
    await screen.findByLabelText("Support email");
    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    await userEvent.click(saveButtons[0]);

    expect(await screen.findByText("This could not be saved. Please try again.")).toBeInTheDocument();
    expect(screen.queryByText("stack trace leaked here")).not.toBeInTheDocument();
  });
});
