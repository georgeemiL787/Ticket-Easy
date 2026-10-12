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
  tenant_id: T, configured: true, test_only: true, business_id: "biz-1", builder_url: "http://127.0.0.1:8101/businesses/biz-1",
  builder_base: "http://127.0.0.1:8101", listed_at: "2026-10-12T08:05:00Z", team_c_error: null, mapped_customers: 2,
  tools: [
    { name: "get_order", used_by: ["order_status"], source: "team_c", status: published ? "available" : "missing",
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
  it("shows which tools Team C serves, marks them TEST-ONLY, and disables one through the service", async () => {
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
    expect(screen.getByRole("note")).toHaveTextContent("TEST-ONLY");
    const tools = (await screen.findAllByRole("table"))[0]!;
    expect(within(tools).getByText("Team C · sandbox")).toBeInTheDocument();
    expect(within(tools).getByText("Stand-in shop")).toBeInTheDocument();
    expect(screen.getByText("sandbox · not production")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the tool builder (Team C)" })).toHaveAttribute("href", "http://127.0.0.1:8101/businesses/biz-1");
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
