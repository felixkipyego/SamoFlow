import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { UsageScreen } from "./UsageScreen";
import { ApiError } from "@/lib/api";
import * as escalationApi from "@/lib/escalation-api";

describe("UsageScreen", () => {
  it("shows a permission-denied message on a 403, with no internal detail text", async () => {
    vi.spyOn(escalationApi, "getUsage").mockRejectedValue(new ApiError(403, "internal detail"));

    render(<UsageScreen />);
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.queryByText("internal detail")).not.toBeInTheDocument();
  });

  it("shows the no-plan state when no plan is assigned", async () => {
    vi.spyOn(escalationApi, "getUsage").mockResolvedValue({
      plan_assigned: false,
      quota: null,
      used_this_month: 3,
      fraction_used: null,
      state: "no_plan",
    });

    render(<UsageScreen />);
    expect(await screen.findByText("No plan assigned")).toBeInTheDocument();
    expect(screen.getByText(/No plan is assigned/)).toBeInTheDocument();
  });

  it("shows the warning state at 80%, matching the alert email threshold", async () => {
    vi.spyOn(escalationApi, "getUsage").mockResolvedValue({
      plan_assigned: true,
      quota: 100,
      used_this_month: 80,
      fraction_used: 0.8,
      state: "warning",
    });

    render(<UsageScreen />);
    expect(await screen.findByText("80% used")).toBeInTheDocument();
    // The "80 / 100 messages" count is split across a text node and a
    // nested <span> -- matched here by the enclosing <p>'s own full
    // text content rather than a plain string/regex match, which only
    // matches a single node's own text.
    expect(
      screen.getByText(
        (_, node) => node?.tagName === "P" && /80\s*\/\s*100\s*messages/.test(node.textContent ?? ""),
      ),
    ).toBeInTheDocument();
  });

  it("shows the exceeded state at 100%, matching the alert email threshold", async () => {
    vi.spyOn(escalationApi, "getUsage").mockResolvedValue({
      plan_assigned: true,
      quota: 100,
      used_this_month: 100,
      fraction_used: 1.0,
      state: "exceeded",
    });

    render(<UsageScreen />);
    expect(await screen.findByText("100% used")).toBeInTheDocument();
  });
});
