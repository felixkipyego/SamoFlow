import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InboxScreen } from "./InboxScreen";
import { ApiError } from "@/lib/api";
import * as escalationApi from "@/lib/escalation-api";

const BASE_ESCALATION: escalationApi.EscalationSummary = {
  id: "e1",
  status: "new",
  contact_name: "Jane",
  contact_email: "jane@example.com",
  contact_phone: null,
  created_at: "2026-10-15T10:00:00Z",
};

describe("InboxScreen", () => {
  it("shows an empty state with no escalations", async () => {
    vi.spyOn(escalationApi, "listEscalations").mockResolvedValue({ escalations: [] });

    render(<InboxScreen />);
    expect(await screen.findByText(/No escalation requests yet/)).toBeInTheDocument();
  });

  it("shows a permission-denied message on a 403, with no internal detail text", async () => {
    vi.spyOn(escalationApi, "listEscalations").mockRejectedValue(
      new ApiError(403, "internal detail"),
    );

    render(<InboxScreen />);
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.queryByText("internal detail")).not.toBeInTheDocument();
  });

  it("renders a row with its contact info and status", async () => {
    vi.spyOn(escalationApi, "listEscalations").mockResolvedValue({
      escalations: [BASE_ESCALATION],
    });

    render(<InboxScreen />);
    expect(await screen.findByText(/Jane/)).toBeInTheDocument();
    expect(screen.getByText("new")).toBeInTheDocument();
  });

  it("loads and shows the unescaped transcript as plain text when View is clicked", async () => {
    vi.spyOn(escalationApi, "listEscalations").mockResolvedValue({
      escalations: [BASE_ESCALATION],
    });
    vi.spyOn(escalationApi, "getEscalation").mockResolvedValue({
      ...BASE_ESCALATION,
      transcript: "Visitor: hi\n\nAssistant: hello there",
    });

    render(<InboxScreen />);
    await screen.findByText(/Jane/);
    await userEvent.click(screen.getByRole("button", { name: "View" }));
    expect(await screen.findByText(/Assistant: hello there/)).toBeInTheDocument();
  });

  it("marks an escalation as resolved and refetches", async () => {
    vi.spyOn(escalationApi, "listEscalations").mockResolvedValue({
      escalations: [BASE_ESCALATION],
    });
    const statusSpy = vi
      .spyOn(escalationApi, "updateEscalationStatus")
      .mockResolvedValue({ ...BASE_ESCALATION, status: "resolved" });
    const onOpenCountChanged = vi.fn();

    render(<InboxScreen onOpenCountChanged={onOpenCountChanged} />);
    await screen.findByText(/Jane/);
    await userEvent.click(screen.getByRole("button", { name: "Mark resolved" }));

    await waitFor(() => expect(statusSpy).toHaveBeenCalledWith("e1", "resolved"));
    await waitFor(() => expect(onOpenCountChanged).toHaveBeenCalled());
  });

  it("the delete confirmation names what will be removed, and deletes on confirm", async () => {
    vi.spyOn(escalationApi, "listEscalations").mockResolvedValue({
      escalations: [BASE_ESCALATION],
    });
    const deleteSpy = vi.spyOn(escalationApi, "deleteEscalation").mockResolvedValue(undefined);

    render(<InboxScreen />);
    await screen.findByText(/Jane/);
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/permanently removes this escalation/)).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteSpy).toHaveBeenCalledWith("e1"));
  });
});
