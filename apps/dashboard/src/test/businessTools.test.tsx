import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";

const T = "shop_001";
const tenants = { tenants: [{ tenant_id: T, display_name: "Nile Style", default_locale: "en" }] };
const publication = {
  id: "pub-1", tool_name: "look_up_order_v1_abc", status: "published", effective_status: "published", problem: null,
  environment: "sandbox", production_ready: false, published_at: "2026-10-12T08:00:00Z", disabled_at: null,
  proposal_id: "prop-1", version: 1, bound_to: "get_order",
};
const page = (published: boolean) => ({
  tenant_id: T, configured: true, sandbox_only: true, business_id: "biz-1", builder_url: "http://127.0.0.1:8101/businesses/biz-1",
  builder_base: "http://127.0.0.1:8101", listed_at: "2026-10-12T08:05:00Z", team_c_error: null, mapped_customers: 2,
  tools: [
    { name: "get_order", used_by: ["order_status"], source: "team_c", environment: "sandbox", api_host: null, status: published ? "available" : "missing",
      reason: published ? null : "not_published", detail: published ? "" : "no sandbox publication of this tool is active in Team C",
      operation_kind: published ? "read" : null, team_c_tool: published ? publication.tool_name : null, publication_id: null,
      proposal_id: "prop-1", proposal_version: 1, artifact_sha256: null },
    { name: "verify_customer", used_by: ["identity"], source: "standin", status: "available", reason: null, detail: "",
      operation_kind: "read", team_c_tool: null, publication_id: null, proposal_id: null, proposal_version: null, artifact_sha256: null },
  ],
  publications: [published ? publication : { ...publication, status: "disabled", effective_status: "disabled", disabled_at: "2026-10-12T09:00:00Z" }],
  publications_error: null,
  proposals: [{ id: "prop-2", name: "Open a ticket", version: 1, state: "ready_for_review" }],
});

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

afterEach(() => vi.unstubAllGlobals());

describe("business tools", () => {
  it("shows which tools Team C serves, marks them sandbox-only, and disables one through the service", async () => {
    let published = true;
    const posted: { path: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://test");
        if (url.pathname === "/v1/auth/me") return respond({ auth_required: false, user: null, csrf_token: null });
        if (url.pathname === "/v1/dashboard/tenants") return respond(tenants);
        if (init?.method === "POST") {
          posted.push({ path: url.pathname, body: JSON.parse(String(init.body)) });
          if (url.pathname.endsWith("/disable")) published = false;
          return respond(page(published));
        }
        if (url.pathname === "/v1/dashboard/business-tools") return respond(page(published));
        return respond({ error: { code: "NOT_FOUND", message: "not found" } }, 404);
      }),
    );
    render(<MemoryRouter initialEntries={["/tools"]}><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Business tools", level: 1 })).toBeInTheDocument();
    const tools = (await screen.findAllByRole("table"))[0]!;
    expect(screen.queryByText(/Sandbox-only/)).not.toBeInTheDocument();
    expect(within(tools).getByText("Tool service")).toBeInTheDocument();
    expect(within(tools).getByText("SANDBOX")).toBeInTheDocument();
    expect(within(tools).getByText("Simulated shop")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the advanced tool builder" })).toHaveAttribute("href", "http://127.0.0.1:8101/businesses/biz-1");
    expect(screen.getByText("Ready for your decision")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: "Why (required)" }), "stop the demo tool");
    await user.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() => expect(posted).toEqual([{ path: "/v1/dashboard/business-tools/publications/pub-1/disable", body: { note: "stop the demo tool" } }]));
    // the page shows what the service holds after the call, not what the button assumed
    expect(await screen.findByText("Not published (no sandbox publication of this tool is active in Team C)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Refresh tool list" }));
    await waitFor(() => expect(posted.at(-1)?.path).toBe("/v1/dashboard/business-tools/refresh"));
  });

  it("says plainly when Team C is not connected and the service refuses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(String(input), "http://test");
        if (url.pathname === "/v1/auth/me") return respond({ auth_required: false, user: null, csrf_token: null });
        if (url.pathname === "/v1/dashboard/tenants") return respond(tenants);
        return respond({ error: { code: "FORBIDDEN", message: "this needs the role manager" } }, 403);
      }),
    );
    render(<MemoryRouter initialEntries={["/tools"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("this needs the role manager");
  });
});

describe("business tools in Live Demo", () => {
  it("labels a live tool truthfully and checks a connector through the service", async () => {
    const posted: string[] = [];
    let checked = false;
    const live = () => {
      const base = page(true);
      return {
        ...base,
        live_tools: 1,
        sandbox_only: false,
        tools: base.tools.map((tool) => (tool.name === "get_order" ? { ...tool, environment: "live_demo", api_host: "api.example.com" } : tool)),
        publications: base.publications.map((p) => ({ ...p, environment: "live_demo", execution_enabled: true })),
        connectors: [
          {
            id: "live-abc", environment: "live_demo", host: "api.example.com:443", identities: 1,
            checked_at: checked ? "2026-10-12T09:00:00Z" : null, ready: checked ? false : null,
            checks: checked ? [{ check: "reachable", status: "ok", detail: "The API answered HTTP 200" }, { check: "authentication", status: "unknown", detail: "run a read test" }] : [],
          },
        ],
      };
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://test");
        if (url.pathname === "/v1/auth/me") return respond({ auth_required: false, user: null, csrf_token: null });
        if (url.pathname === "/v1/dashboard/tenants") return respond(tenants);
        if (init?.method === "POST") {
          posted.push(url.pathname);
          checked = true;
          return respond(live());
        }
        if (url.pathname === "/v1/dashboard/business-tools") return respond(live());
        return respond({ error: { code: "NOT_FOUND", message: "not found" } }, 404);
      }),
    );
    render(<MemoryRouter initialEntries={["/tools"]}><App /></MemoryRouter>);
    expect(await screen.findByText(/LIVE DEMO: 1 tool\(s\) call the owner-configured real API/)).toBeInTheDocument();
    expect(screen.queryByText("sandbox-only.")).toBeNull();
    expect(screen.getByText("calls the real API at api.example.com")).toBeInTheDocument();
    expect(screen.getAllByText("LIVE DEMO").length).toBeGreaterThanOrEqual(2); // tool, connector
    expect(screen.getByText("Not checked yet.")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Check connection" }));
    await waitFor(() => expect(posted).toEqual(["/v1/dashboard/business-tools/connectors/live-abc/check"]));
    expect(await screen.findByText("Endpoint reachable")).toBeInTheDocument();
    expect(screen.getByText("Not proven")).toBeInTheDocument();
  });

  it("shows a built tool's readiness and starts the automatic tests through the service", async () => {
    let started = false;
    const posted: string[] = [];
    const body = () => ({
      ...page(true),
      proposals: [{ id: "prop-2", name: "Open a ticket", version: 1, state: "approved_to_build", artifact_id: "art-1",
        readiness_state: started ? "ready_to_publish" : "not_tested", auto_test_status: null, auto_test_question: null }],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://test");
        if (url.pathname === "/v1/auth/me") return respond({ auth_required: false, user: null, csrf_token: null });
        if (url.pathname === "/v1/dashboard/tenants") return respond(tenants);
        if (init?.method === "POST") {
          posted.push(url.pathname);
          started = true;
          return respond(body());
        }
        if (url.pathname === "/v1/dashboard/business-tools") return respond(body());
        return respond({ error: { code: "NOT_FOUND", message: "not found" } }, 404);
      }),
    );
    render(<MemoryRouter initialEntries={["/tools"]}><App /></MemoryRouter>);
    expect(await screen.findByText("Not tested")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Run automatic tests" }));
    expect(await screen.findByText("Ready to publish")).toBeInTheDocument();
    expect(posted).toEqual(["/v1/dashboard/business-tools/artifacts/art-1/auto-test"]);
  });


  it("an admin sees PRODUCTION and can promote and roll back; a manager sees the state but no production controls", async () => {
    let promoted = false;
    let rolledBack = false;
    const posted: { path: string; body: unknown }[] = [];
    const body = () => ({
      ...page(true),
      sandbox_only: false,
      production_tools: promoted && !rolledBack ? 1 : 0,
      production_connector: true,
      proposals: [{ id: "prop-2", name: "Open a ticket", version: 1, state: "approved_to_build", artifact_id: "art-1",
        readiness_state: "published", auto_test_status: null, auto_test_question: null, promotable: !promoted, promoted }],
      promotions: promoted
        ? [{ id: "promo-1", source_artifact_id: "art-1", status: rolledBack ? "rolled_back" : "active", actor: "Ali Admin", note: "go live",
            rollback_contact: "oncall@example.com", rolled_back_by: rolledBack ? "Ali Admin" : null, rollback_note: rolledBack ? "stop" : null,
            checklist: [{ name: "smoke", ok: true, detail: "passed" }] }]
        : [],
    });
    const install = (role: "admin" | "manager") =>
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = new URL(String(input), "http://test");
          if (url.pathname === "/v1/auth/me")
            return respond({ auth_required: true, user: { user_id: "u1", email: "a@x.eg", display_name: "Ali Admin", role, tenants: [T] }, csrf_token: "tok" });
          if (url.pathname === "/v1/dashboard/tenants") return respond(tenants);
          if (init?.method === "POST") {
            posted.push({ path: url.pathname, body: JSON.parse(String(init.body)) });
            if (url.pathname.endsWith("/promote")) promoted = true;
            if (url.pathname.endsWith("/rollback")) rolledBack = true;
            return respond(body());
          }
          if (url.pathname === "/v1/dashboard/business-tools") return respond(body());
          return respond({ error: { code: "NOT_FOUND", message: "not found" } }, 404);
        }),
      );
    install("manager");
    const first = render(<MemoryRouter initialEntries={["/tools"]}><App /></MemoryRouter>);
    expect((await screen.findAllByText("Published")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Promote to production")).not.toBeInTheDocument();
    first.unmount();
    vi.unstubAllGlobals();
    install("admin");
    render(<MemoryRouter initialEntries={["/tools"]}><App /></MemoryRouter>);
    await userEvent.click(await screen.findByText("Promote to production"));
    await userEvent.type(screen.getByLabelText("Why this tool goes live (required)"), "go live");
    await userEvent.type(screen.getByLabelText("Who to reach to switch it off (required)"), "oncall@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Promote" }));
    expect(await screen.findByText(/PRODUCTION: 1 tool\(s\) act on real customers/)).toBeInTheDocument();
    expect(posted[0]).toEqual({ path: "/v1/dashboard/business-tools/artifacts/art-1/promote",
      body: { note: "go live", rollback_contact: "oncall@example.com", accept_no_idempotency: false } });
    expect(await screen.findByText(/Decided by Ali Admin/)).toBeInTheDocument();
    const rollback = within(screen.getByRole("form", { name: "Roll back promo-1" }));
    await userEvent.type(rollback.getByLabelText("Why (required)"), "stop");
    await userEvent.click(rollback.getByRole("button", { name: "Roll back" }));
    expect(await screen.findByText("Rolled back")).toBeInTheDocument();
    expect(posted[1]).toEqual({ path: "/v1/dashboard/business-tools/promotions/promo-1/rollback", body: { note: "stop" } });
  });
});
