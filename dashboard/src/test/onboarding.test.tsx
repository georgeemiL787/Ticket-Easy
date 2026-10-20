import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";

const T = "cairo_books";
const tenants = { tenants: [{ tenant_id: "shop_001", display_name: "Nile Style", default_locale: "en" }] };
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const profile = { name: "Cairo Books", locales: ["en"], default_locale: "en", tone: "friendly", order_id_prefix: "CB-", order_id_digits: 5, contacts: [] };
const draft = (extra: Record<string, unknown> = {}) => ({
  tenant_id: T, step: "profile", steps: ["profile", "knowledge", "guardrails", "connect", "tools", "review"], profile, verify_tool: null, live: false, bundle_version: 1, tools: [], ...extra,
});
const readiness = (extra: Record<string, unknown> = {}) => ({
  tenant_id: T, documents: { indexed: 1, failed: 0, waiting: 0 }, rules: { approved: 1, pending: 0, stale: 0 }, tools: ["look_up_order"], verify_tool: null,
  test_messages: 0, blockers: [{ code: "no_verify_tool", message: "x" }, { code: "no_test_chat", message: "y" }], ready: false, live: false, ...extra,
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

function stub(handler: (url: URL, init?: RequestInit) => unknown) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://test");
      if (url.pathname === "/v1/auth/me") return respond({ auth_required: false, user: null, csrf_token: null });
      if (url.pathname === "/v1/dashboard/tenants") return respond(tenants);
      calls.push({ path: url.pathname, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
      const answer = handler(url, init);
      return answer === undefined ? respond({ error: { code: "NOT_FOUND", message: "no" } }, 404) : respond(answer);
    }),
  );
  return calls;
}

describe("onboarding", () => {
  it("lists businesses being set up and creates a new one from the profile", async () => {
    const calls = stub((url, init) => {
      if (url.pathname === "/v1/onboarding" && init?.method === "POST") return draft();
      if (url.pathname === "/v1/onboarding") return { businesses: [draft({ tenant_id: "old_shop", profile: { ...profile, name: "Old Shop" } })] };
      if (url.pathname === `/v1/onboarding/${T}`) return draft();
      if (url.pathname.endsWith("/review")) return readiness();
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/onboarding"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Set up a business", level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement.getAttribute("data-theme")).toBe("aurora-light"));
    expect(await screen.findByRole("article", { name: "Old Shop" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Continue setting up" })).toBeInTheDocument();
    const create = screen.getByRole("button", { name: "Create the business" });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText("Business name"), "Cairo Books");
    await user.type(screen.getByLabelText("Order number starts with"), "CB-");
    await user.type(screen.getByLabelText(/People who take over/), "Mona, Sami");
    await user.click(create);
    const sent = calls.find((c) => c.method === "POST")!.body as typeof profile;
    expect(sent).toMatchObject({ name: "Cairo Books", order_id_prefix: "CB-", contacts: ["Mona", "Sami"], locales: ["en"], tone: "friendly" });
    expect(await screen.findByRole("heading", { name: "Cairo Books", level: 1 })).toBeInTheDocument();
  });

  it("resumes at the saved step, shows progress and links to the step's own screen", async () => {
    stub((url) => {
      if (url.pathname === `/v1/onboarding/${T}`) return draft({ step: "knowledge" });
      if (url.pathname.endsWith("/review")) return readiness();
      return undefined;
    });
    render(<MemoryRouter initialEntries={[`/onboarding/${T}`]}><App /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Cairo Books", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText("Step 2 of 6")).toBeInTheDocument();
    expect(await screen.findByText("1 documents searchable")).toBeInTheDocument();
    const open = screen.getByRole("link", { name: "Open this step" });
    expect(open).toHaveAttribute("href", `/knowledge?tenant=${T}&wizard=${T}`);
    expect(screen.getByRole("button", { name: "2 · Policies" })).toHaveAttribute("aria-current", "step");
  });

  it("will not go live while anything blocks it, and goes live when ready", async () => {
    let ready = false;
    const calls = stub((url, init) => {
      if (url.pathname === `/v1/onboarding/${T}` && !init?.method) return draft({ step: "review", live: ready && calls_live() });
      if (url.pathname.endsWith("/review")) return ready ? readiness({ ready: true, blockers: [], verify_tool: "look_up_order", test_messages: 2 }) : readiness();
      if (url.pathname.endsWith("/preview")) return { token: "123.abc", chat_url: `/chat?tenant_id=${T}&preview=123.abc`, live: false };
      if (url.pathname.endsWith("/step")) return draft({ step: "review", verify_tool: "look_up_order" });
      if (url.pathname.endsWith("/go-live")) return { tenant_id: T, live: true, bundle_version: 3, tools: ["look_up_order"] };
      return undefined;
    });
    const calls_live = () => calls.some((c) => c.path.endsWith("/go-live"));
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={[`/onboarding/${T}`]}><App /></MemoryRouter>);
    const go = await screen.findByRole("button", { name: "Go live" });
    expect(go).toBeDisabled();
    expect(await screen.findByText("Choose the tool that checks who the customer is.")).toBeInTheDocument();
    expect(screen.getByText("Send a message in the test chat first.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open the test chat" }));
    expect(await screen.findByTitle("Try your assistant here. Tools run in test mode only.")).toHaveAttribute("src", `/chat?tenant_id=${T}&preview=123.abc`);
    ready = true;
    await user.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Go live" })).toBeEnabled());
    expect(screen.getByText("Everything is ready. You can go live.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Go live" }));
    expect(await screen.findByText(/Your business is live/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/go-live") && c.method === "POST")).toBe(true);
  });

  it("shows the service's refusal when going live is not allowed", async () => {
    stub((url, init) => {
      if (url.pathname === `/v1/onboarding/${T}` && !init?.method) return draft({ step: "review" });
      if (url.pathname.endsWith("/review")) return readiness({ ready: true, blockers: [] });
      if (url.pathname.endsWith("/go-live")) return undefined;
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={[`/onboarding/${T}`]}><App /></MemoryRouter>);
    await waitFor(async () => expect(await screen.findByRole("button", { name: "Go live" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Go live" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("the pages the wizard opens link back to the setup", async () => {
    stub((url) => (url.pathname === "/v1/dashboard/knowledge/documents" ? { tenant_id: T, managed: true, documents: [] } : undefined));
    render(<MemoryRouter initialEntries={[`/knowledge?tenant=${T}&wizard=${T}`]}><App /></MemoryRouter>);
    const back = await screen.findByRole("link", { name: /Back to setup/ });
    expect(back).toHaveAttribute("href", `/onboarding/${T}?tenant=${T}`);
  });
});
