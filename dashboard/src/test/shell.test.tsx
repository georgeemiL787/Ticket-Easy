import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ApiError, withQuery } from "../api/client";
import { DICTIONARIES, directionOf, translate } from "../i18n";
import { bucketFor, windowFor } from "../lib/dates";

const overview = {
  tenant_id: "shop_001",
  period_start: "2026-09-21T00:00:00Z",
  period_end: "2026-09-28T00:00:00Z",
  previous_start: "2026-09-14T00:00:00Z",
  previous_end: "2026-09-21T00:00:00Z",
  conversations: { value: 120, previous: 100, change: 20 },
  automation_rate: { value: 0.8, previous: 0.7, change: 0.1 },
  escalation_rate: { value: 0.2, previous: 0.3, change: -0.1 },
  unverified_results: { value: 0, previous: 0, change: 0 },
  p95_latency_ms: { value: 410, previous: null, change: null },
  open_cases: 3,
};
const tenants = { tenants: [{ tenant_id: "shop_001", display_name: "Nile Style", default_locale: "en" }] };

function mockFetch(routes: Record<string, () => Response | Promise<Response>>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    const handler = routes[url.pathname];
    return handler ? handler() : new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "not found" } }), { status: 404 });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
const json = (body: unknown, status = 200) => () => new Response(JSON.stringify(body), { status });

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  document.documentElement.lang = "en";
  document.documentElement.dir = "ltr";
});
afterEach(() => vi.unstubAllGlobals());

describe("the shell", () => {
  it("shows the navigation, the top bar and the overview numbers", async () => {
    mockFetch({ "/v1/dashboard/tenants": json(tenants), "/v1/dashboard/overview": json(overview) });
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    for (const name of ["Overview", "Conversations", "Escalations", "Actions", "Unanswered questions", "Alerts"]) {
      expect(nav).toHaveTextContent(name);
    }
    expect(await screen.findByLabelText("Conversations", { selector: "section" })).toHaveTextContent("120");
    expect(screen.getByLabelText("Automation rate")).toHaveTextContent("80%");
    expect(screen.getByLabelText("Escalation rate")).toHaveTextContent("down 10 pts");
    expect(await screen.findByRole("option", { name: "Nile Style" })).toBeInTheDocument();
    expect(screen.getByLabelText("Period")).toBeInTheDocument();
    expect(screen.getByLabelText("Language")).toBeInTheDocument();
  });

  it("switches to Arabic with right-to-left text when the address says lang=ar", async () => {
    mockFetch({ "/v1/dashboard/tenants": json(tenants), "/v1/dashboard/overview": json(overview) });
    renderApp("/?lang=ar");
    expect(await screen.findByRole("navigation", { name: "القائمة الرئيسية" })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
    expect(document.documentElement.lang).toBe("ar");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("نظرة عامة");
  });

  it("keeps the filters in the address when moving between pages", async () => {
    mockFetch({ "/v1/dashboard/tenants": json(tenants), "/v1/dashboard/overview": json(overview) });
    renderApp("/?range=30d&lang=ar");
    const link = await screen.findByRole("link", { name: "المحادثات" });
    expect(link.getAttribute("href")).toBe("/conversations?range=30d&lang=ar");
  });

  it("asks for the window the chosen range stands for", async () => {
    const fetchMock = mockFetch({ "/v1/dashboard/tenants": json(tenants), "/v1/dashboard/overview": json(overview) });
    renderApp("/?range=24h");
    await screen.findByLabelText("Automation rate");
    const call = fetchMock.mock.calls.map(([u]) => new URL(String(u), "http://test")).find((u) => u.pathname.endsWith("/overview"))!;
    const span = Date.parse(call.searchParams.get("to")!) - Date.parse(call.searchParams.get("from")!);
    expect(span).toBe(24 * 3_600_000);
    expect(call.searchParams.get("tenant_id")).toBe("shop_001");
  });

  it("changing the period changes the address and loads again", async () => {
    const fetchMock = mockFetch({ "/v1/dashboard/tenants": json(tenants), "/v1/dashboard/overview": json(overview) });
    renderApp("/");
    await screen.findByLabelText("Automation rate");
    await userEvent.selectOptions(screen.getByLabelText("Period"), "30d");
    await waitFor(() => {
      const spans = fetchMock.mock.calls
        .map(([u]) => new URL(String(u), "http://test"))
        .filter((u) => u.pathname.endsWith("/overview"))
        .map((u) => Date.parse(u.searchParams.get("to")!) - Date.parse(u.searchParams.get("from")!));
      expect(spans).toContain(30 * 24 * 3_600_000);
    });
  });
});

describe("empty and error states", () => {
  it("shows an empty state when the period has no conversations", async () => {
    const none = { ...overview, conversations: { value: 0, previous: 0, change: 0 } };
    mockFetch({ "/v1/dashboard/tenants": json(tenants), "/v1/dashboard/overview": json(none) });
    renderApp("/");
    expect(await screen.findByText("Nothing to show for this period.")).toBeInTheDocument();
  });

  it("shows the service's own message and a retry button when a page fails", async () => {
    let calls = 0;
    mockFetch({
      "/v1/dashboard/tenants": json(tenants),
      "/v1/dashboard/overview": () => {
        calls += 1;
        return calls === 1
          ? new Response(JSON.stringify({ error: { code: "UPSTREAM_UNAVAILABLE", message: "the database is unavailable" } }), { status: 503 })
          : new Response(JSON.stringify(overview), { status: 200 });
      },
    });
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("the database is unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Automation rate")).toBeInTheDocument();
  });

  it("every other page has a loading, empty and error state too", async () => {
    mockFetch({
      "/v1/dashboard/tenants": json(tenants),
      "/v1/dashboard/conversations": json({ items: [], next_cursor: null }),
      "/v1/dashboard/escalations": json({ tenant_id: "shop_001", period_start: "", period_end: "", conversations: 0, by_reason_total: {}, by_reason: [], open_cases: 0, claimed_cases: 0, top_rules: [] }),
      "/v1/dashboard/tools": json({ tenant_id: "shop_001", period_start: "", period_end: "", tools: [] }),
      "/v1/dashboard/knowledge-gaps": json({ tenant_id: "shop_001", period_start: "", period_end: "", questions_without_answer: 0, groups: [] }),
    });
    for (const path of ["/conversations", "/escalations", "/actions", "/unanswered"]) {
      const { unmount } = renderApp(path);
      expect(await screen.findByText("Nothing to show for this period.")).toBeInTheDocument();
      unmount();
    }
    mockFetch({ "/v1/dashboard/tenants": json(tenants) }); // no alerts endpoint yet: 404
    renderApp("/alerts");
    expect(await screen.findByText("Alerts are not available yet.")).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("has the same texts in English and Arabic", () => {
    expect(Object.keys(DICTIONARIES.ar).sort()).toEqual(Object.keys(DICTIONARIES.en).sort());
    for (const [key, text] of Object.entries(DICTIONARIES.ar)) expect(text.trim(), key).not.toBe("");
  });

  it("fills placeholders and picks the direction", () => {
    expect(translate("en", "common.noData")).toBe("—");
    expect(directionOf("ar")).toBe("rtl");
    expect(directionOf("en")).toBe("ltr");
  });

  it("works out windows and buckets", () => {
    const now = new Date("2026-09-28T12:00:00Z");
    expect(windowFor("7d", now)).toEqual({ from: "2026-09-21T12:00:00.000Z", to: "2026-09-28T12:00:00.000Z" });
    expect(windowFor("custom", now, "2026-09-01", "2026-09-03")).toEqual({ from: "2026-09-01T00:00:00.000Z", to: "2026-09-04T00:00:00.000Z" });
    expect(windowFor("custom", now, "nonsense", "2026-09-03").to).toBe("2026-09-28T12:00:00.000Z"); // falls back to 7 days
    expect(bucketFor(windowFor("24h", now))).toBe("hour");
    expect(bucketFor(windowFor("7d", now))).toBe("day");
    expect(bucketFor(windowFor("custom", now, "2026-01-01", "2026-06-30"))).toBe("week");
  });

  it("builds query strings without empty values and keeps the error code", () => {
    expect(withQuery("/x", { a: "1", b: undefined, c: "", d: 0 })).toBe("/x?a=1&d=0");
    const error = new ApiError(409, "INVALID_STATE", "no");
    expect([error.status, error.code, error.message]).toEqual([409, "INVALID_STATE", "no"]);
  });
});
