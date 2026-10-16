import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, getInitialTheme, setTheme } from "./theme";

describe("theme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });

  it("falls back to the system preference when nothing is stored", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
    } as MediaQueryList);
    expect(getInitialTheme()).toBe("dark");
  });

  it("prefers a stored choice over the system preference", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
    } as MediaQueryList);
    localStorage.setItem("samoflow-dashboard-theme", "light");
    expect(getInitialTheme()).toBe("light");
  });

  it("applyTheme toggles the .dark class on the root element", () => {
    applyTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    applyTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("setTheme persists the choice and applies it", () => {
    setTheme("dark");
    expect(localStorage.getItem("samoflow-dashboard-theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
