import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LoginScreen } from "./LoginScreen";
import * as api from "@/lib/api";

describe("LoginScreen", () => {
  it("primes the CSRF cookie on mount", async () => {
    const ensureCsrfCookie = vi.spyOn(api, "ensureCsrfCookie").mockResolvedValue(undefined);
    render(<LoginScreen onLoggedIn={vi.fn()} />);
    await waitFor(() => expect(ensureCsrfCookie).toHaveBeenCalled());
  });

  it("calls onLoggedIn with the real session on successful login", async () => {
    vi.spyOn(api, "ensureCsrfCookie").mockResolvedValue(undefined);
    const session = {
      user: { id: "u1", email: "owner@example.com" },
      tenant: { id: "t1", name: "Acme Inc" },
    };
    vi.spyOn(api, "login").mockResolvedValue(session);
    const onLoggedIn = vi.fn();

    render(<LoginScreen onLoggedIn={onLoggedIn} />);
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(onLoggedIn).toHaveBeenCalledWith(session));
  });

  it("shows a generic error message on a failed login, never a raw backend detail", async () => {
    vi.spyOn(api, "ensureCsrfCookie").mockResolvedValue(undefined);
    vi.spyOn(api, "login").mockRejectedValue(new api.ApiError(401, "login could not be completed"));

    render(<LoginScreen onLoggedIn={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Email or password is incorrect.");
  });

  it("shows a rate-limit-specific message on a 429", async () => {
    vi.spyOn(api, "ensureCsrfCookie").mockResolvedValue(undefined);
    vi.spyOn(api, "login").mockRejectedValue(new api.ApiError(429, "too many requests"));

    render(<LoginScreen onLoggedIn={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Email"), "owner@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "whatever");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/too many attempts/i);
  });
});
