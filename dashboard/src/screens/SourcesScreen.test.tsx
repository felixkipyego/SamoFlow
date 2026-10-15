import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SourcesScreen } from "./SourcesScreen";
import { ApiError } from "@/lib/api";
import * as sourcesApi from "@/lib/sources-api";

const BASE_SOURCE: sourcesApi.Source = {
  id: "s1",
  type: "urls",
  status: "ok",
  enabled: true,
  refresh_interval: "daily",
  last_run_at: null,
  next_run_at: null,
  exceeds_plan: false,
  document_count: 3,
  may_need_javascript: false,
  config: { urls: ["https://example.com/a"] },
};

describe("SourcesScreen", () => {
  it("shows an empty state with no sources", async () => {
    vi.spyOn(sourcesApi, "listSources").mockResolvedValue({
      sources: [],
      total_indexed_pages: 0,
      total_indexed_pages_limit: null,
    });
    vi.spyOn(sourcesApi, "listDomains").mockResolvedValue([]);

    render(<SourcesScreen />);
    expect(await screen.findByText(/No sources yet/)).toBeInTheDocument();
  });

  it("shows a permission-denied message on a 403, with no internal detail text", async () => {
    vi.spyOn(sourcesApi, "listSources").mockRejectedValue(new ApiError(403, "internal detail"));
    vi.spyOn(sourcesApi, "listDomains").mockResolvedValue([]);

    render(<SourcesScreen />);
    expect(await screen.findByText("Permission denied")).toBeInTheDocument();
    expect(screen.queryByText("internal detail")).not.toBeInTheDocument();
  });

  it("renders a source row with its document count and status", async () => {
    vi.spyOn(sourcesApi, "listSources").mockResolvedValue({
      sources: [BASE_SOURCE],
      total_indexed_pages: 3,
      total_indexed_pages_limit: 5000,
    });
    vi.spyOn(sourcesApi, "listDomains").mockResolvedValue([]);

    render(<SourcesScreen />);
    expect(await screen.findByText("urls")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("the delete confirmation names exactly what will be removed", async () => {
    vi.spyOn(sourcesApi, "listSources").mockResolvedValue({
      sources: [BASE_SOURCE],
      total_indexed_pages: 3,
      total_indexed_pages_limit: null,
    });
    vi.spyOn(sourcesApi, "listDomains").mockResolvedValue([]);

    render(<SourcesScreen />);
    await screen.findByText("urls");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText(/permanently removes 3 indexed pages/)).toBeInTheDocument();
  });

  it("the upload form disables submit until the warning checkbox is confirmed", async () => {
    vi.spyOn(sourcesApi, "listSources").mockResolvedValue({
      sources: [],
      total_indexed_pages: 0,
      total_indexed_pages_limit: null,
    });
    vi.spyOn(sourcesApi, "listDomains").mockResolvedValue([]);
    const uploadSpy = vi.spyOn(sourcesApi, "uploadFile").mockResolvedValue(BASE_SOURCE);

    render(<SourcesScreen />);
    await userEvent.click(screen.getByRole("button", { name: "Add a source" }));
    await userEvent.click(screen.getByRole("tab", { name: "Upload" }));

    const file = new File(["hello"], "report.txt", { type: "text/plain" });
    const fileInput = screen.getByLabelText(/File \(PDF/);
    await userEvent.upload(fileInput, file);

    const uploadButton = screen.getByRole("button", { name: "Upload" });
    expect(uploadButton).toBeDisabled();

    await userEvent.click(
      screen.getByText(/content will be answerable to any visitor/),
    );
    expect(uploadButton).not.toBeDisabled();

    await userEvent.click(uploadButton);
    await waitFor(() => expect(uploadSpy).toHaveBeenCalledWith(file));
  });

  it("adds a urls source and reloads the list", async () => {
    vi.spyOn(sourcesApi, "listSources")
      .mockResolvedValueOnce({ sources: [], total_indexed_pages: 0, total_indexed_pages_limit: null })
      .mockResolvedValueOnce({
        sources: [BASE_SOURCE],
        total_indexed_pages: 3,
        total_indexed_pages_limit: null,
      });
    vi.spyOn(sourcesApi, "listDomains").mockResolvedValue([]);
    const createSpy = vi.spyOn(sourcesApi, "createUrlsSource").mockResolvedValue(BASE_SOURCE);

    render(<SourcesScreen />);
    await userEvent.click(screen.getByRole("button", { name: "Add a source" }));
    const textarea = screen.getByLabelText(/Page URLs/);
    await userEvent.type(textarea, "https://example.com/a");
    await userEvent.click(screen.getByRole("button", { name: "Add URLs" }));

    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(["https://example.com/a"], "daily"),
    );
  });
});
