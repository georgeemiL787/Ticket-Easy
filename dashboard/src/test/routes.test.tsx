import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";

// A safety net for restyling: every page still renders its title, in both languages, when the service fails,
// and nothing is logged as an error by React along the way.
const T = "shop_001";
const ROUTES = ["/", "/conversations", "/conversations/c1", "/escalations", "/actions", "/unanswered", "/alerts", "/tools", "/tools/connect", "/tools/review", "/tools/publish", "/knowledge", "/guardrails", "/onboarding", "/onboarding/cairo_books"];

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

let errors: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    if (url.pathname === "/v1/auth/me") return respond({ auth_required: false, user: null, csrf_token: null });
    if (url.pathname === "/v1/dashboard/tenants") {
      return respond({ tenants: [{ tenant_id: T, display_name: "Nile Style", default_locale: "en" }] });
    }
    return respond({ error: { code: "UPSTREAM_UNAVAILABLE", message: "down", request_id: "r1" } }, 503);
  }));
});

afterEach(() => {
  errors.mockRestore();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("dir");
});

describe("every route renders", () => {
  for (const lang of ["en", "ar"] as const) {
    for (const path of ROUTES) {
      it(`${path} (${lang})`, async () => {
        render(<MemoryRouter initialEntries={[`${path}?tenant_id=${T}&lang=${lang}`]}><App /></MemoryRouter>);
        expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
        expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0); // the failed load is shown, not hidden
        expect(document.documentElement.dir).toBe(lang === "ar" ? "rtl" : "ltr");
        expect(errors).not.toHaveBeenCalled();
      });
    }
  }

  it("an unknown address goes to the overview", async () => {
    render(<MemoryRouter initialEntries={[`/no-such-page?tenant_id=${T}`]}><App /></MemoryRouter>);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(errors).not.toHaveBeenCalled();
  });
});
