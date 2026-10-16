import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OverviewScreen } from "./OverviewScreen";
import { ApiError } from "@/lib/api";
import * as tenantApi from "@/lib/tenant-api";

describe("OverviewScreen", () => {
  it("shows a permission-denied message on a 403, with no internal detail text", async () => {
    vi.spyOn(tenantApi, "getGoLiveCheck").mockRejectedValue(new ApiError(403, "internal detail"));

    render(<OverviewScreen />);
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.queryByText("internal detail")).not.toBeInTheDocument();
  });

  it("shows 'Not ready yet' when a required item is missing", async () => {
    vi.spyOn(tenantApi, "getGoLiveCheck").mockResolvedValue({
      has_contact_method: false,
      has_allowed_origin: true,
      has_verified_domain: false,
      has_enabled_source: false,
      ready_for_go_live: false,
    });

    render(<OverviewScreen />);
    expect(await screen.findByText("Not ready yet")).toBeInTheDocument();
    expect(screen.getAllByText("Needed")).toHaveLength(1);
  });

  it("shows 'Ready for go-live' once both required items are done", async () => {
    vi.spyOn(tenantApi, "getGoLiveCheck").mockResolvedValue({
      has_contact_method: true,
      has_allowed_origin: true,
      has_verified_domain: false,
      has_enabled_source: false,
      ready_for_go_live: true,
    });

    render(<OverviewScreen />);
    expect(await screen.findByText("Ready for go-live")).toBeInTheDocument();
  });
});
