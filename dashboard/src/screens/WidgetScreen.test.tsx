import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { WidgetScreen } from "./WidgetScreen";
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

describe("WidgetScreen", () => {
  it("disables save and shows a client-side error for an invalid color", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockResolvedValue(BASE_CONFIG);
    vi.spyOn(tenantApi, "getInstallSnippet").mockResolvedValue({
      site_key: "pk_live_x",
      base_url: null,
    });
    const updateSpy = vi.spyOn(tenantApi, "updateWidgetLook");

    render(<WidgetScreen />);
    const colorInput = await screen.findByLabelText("Primary color");
    await userEvent.clear(colorInput);
    await userEvent.type(colorInput, "red");

    expect(screen.getByText("Must be a hex color, e.g. #4f46e5.")).toBeInTheDocument();
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it("saves a valid hex color", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockResolvedValue(BASE_CONFIG);
    vi.spyOn(tenantApi, "getInstallSnippet").mockResolvedValue({
      site_key: "pk_live_x",
      base_url: null,
    });
    const updateSpy = vi
      .spyOn(tenantApi, "updateWidgetLook")
      .mockResolvedValue({ ...BASE_CONFIG, colors: { primary: "#112233" } });

    render(<WidgetScreen />);
    const colorInput = await screen.findByLabelText("Primary color");
    await userEvent.clear(colorInput);
    await userEvent.type(colorInput, "#112233");

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ colors: { primary: "#112233" } }),
      ),
    );
  });

  it("tells the tenant to ask their platform admin when no base URL is configured", async () => {
    vi.spyOn(tenantApi, "getTenantConfig").mockResolvedValue(BASE_CONFIG);
    vi.spyOn(tenantApi, "getInstallSnippet").mockResolvedValue({
      site_key: "pk_live_x",
      base_url: null,
    });

    render(<WidgetScreen />);
    expect(await screen.findByText("Not ready yet")).toBeInTheDocument();
  });
});
