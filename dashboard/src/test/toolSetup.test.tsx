import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { clearChoice } from "../state/setupStore";

const T = "shop_001";
const tenants = { tenants: [{ tenant_id: T, display_name: "Nile Style", default_locale: "en" }] };
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const effects = { financial: false, irreversible: false, external_side_effects: true, idempotent: false };
const empty = { tenant_id: T, business_id: null, specs: [], batches: [] };

const operations = {
  spec_id: "spec-1234", total: 4, matching: 4, offset: 0, limit: 500, converted_from: null, diagnostics: [], unusable: 1,
  counts: { read: 1, write: 1, risky: 1, restricted: 1 },
  operations: [
    { id: "op-get", method: "GET", path: "/orders/{id}", title: "Get order", summary: "", tags: [], class: "read", selectable: true, why: [], reason_unusable: null, suggested_effects: effects },
    { id: "op-post", method: "POST", path: "/tickets", title: "Open ticket", summary: "", tags: [], class: "write", selectable: true, why: [], reason_unusable: null, suggested_effects: effects },
    { id: "op-refund", method: "POST", path: "/refunds", title: "Refund", summary: "", tags: [], class: "risky", selectable: true, why: [], reason_unusable: null, suggested_effects: { ...effects, financial: true } },
    { id: "op-login", method: "POST", path: "/login", title: "Login", summary: "", tags: [], class: "restricted", selectable: false, why: [], reason_unusable: "handles sign-in", suggested_effects: effects },
  ],
};

function item(state: string, extra: Record<string, unknown> = {}) {
  return {
    operation_id: "op-post", method: "POST", path: "/tickets", title: "Open ticket", class: "write", state, message: null, suggested_effects: effects,
    proposal_id: "prop-1", artifact_id: null, tool_name: null, problems: [], needs_owner: [],
    summary: { name: "Open a ticket", purpose: "Opens a support ticket", asks_for: ["subject"], reads: [], changes: ["A ticket"], risk: "medium", limitations: [], why: [] },
    ...extra,
  };
}

const batch = (state: string, extra: Record<string, unknown> = {}) => ({
  id: "batch-0001", business_id: "biz-1", spec_id: "spec-1234", busy: ["preparing", "testing"].includes(state), settings: { accounts: [] },
  items: [item(state, extra)], counts: { [state]: 1 },
});

let errors: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
  window.sessionStorage.clear();
  clearChoice(T);
});
afterEach(() => {
  errors.mockRestore();
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

describe("tool setup: connect", () => {
  it("reads a web address, checks the connection and moves on to choosing tools", async () => {
    const calls = stub((url) => {
      if (url.pathname === "/v1/dashboard/tool-setup") return empty;
      if (url.pathname.endsWith("/import")) return { business_id: "biz-1", spec_id: "spec-1234", valid: true, operations: 4, usable: 3, converted_from: "swagger_2.0", problems: [] };
      if (url.pathname.endsWith("/connection-check")) return { reachable: true, status: 200 };
      if (url.pathname.endsWith("/operations")) return operations;
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tools/connect"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Connect your system", level: 1 })).toBeInTheDocument();
    expect(document.documentElement.getAttribute("data-theme")).toBe("aurora-light");
    await user.type(await screen.findByPlaceholderText("https://your-api.example.com/openapi.json"), "https://shop.example.com/openapi.json");
    await user.click(screen.getByRole("button", { name: "Read the description" }));
    expect(await screen.findByText(/3 of 4 operations can become tools/)).toBeInTheDocument();
    expect(screen.getByText(/Swagger 2 file/)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/API address/), "http://sandbox.test:8102");
    await user.click(screen.getByRole("button", { name: "Check connection" }));
    expect(await screen.findByText("The API answered.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue: choose tools" }));
    expect(await screen.findByRole("heading", { name: "Choose your tools", level: 1 })).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/import"))?.body).toEqual({ url: "https://shop.example.com/openapi.json" });
  });

  it("will not continue without a description", async () => {
    stub((url) => (url.pathname === "/v1/dashboard/tool-setup" ? empty : undefined));
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tools/connect"]}><App /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Continue: choose tools" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Read your API description first.");
  });

  it("shows why a description could not be used", async () => {
    stub((url) => {
      if (url.pathname === "/v1/dashboard/tool-setup") return empty;
      if (url.pathname.endsWith("/import")) return { business_id: "biz-1", spec_id: "s", valid: false, operations: 0, usable: 0, converted_from: null, problems: ["No operations found"] };
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tools/connect"]}><App /></MemoryRouter>);
    await user.type(await screen.findByPlaceholderText("https://your-api.example.com/openapi.json"), "https://x.example.com/a.json");
    await user.click(screen.getByRole("button", { name: "Read the description" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No operations found");
  });
});

describe("tool setup: choose tools", () => {
  beforeEach(() => {
    window.sessionStorage.setItem(
      `te.toolsetup.${T}`,
      JSON.stringify({ specId: "spec-1234", baseUrl: "http://sandbox.test:8102", callers: "customer", ownRecordsOnly: true, fixtures: null }),
    );
  });

  it("groups operations, prepares a tool, and approves a write only after its effects are confirmed", async () => {
    const calls = stub((url, init) => {
      if (url.pathname === "/v1/dashboard/tool-setup") return { ...empty, business_id: "biz-1" };
      if (url.pathname.endsWith("/operations")) return operations;
      if (url.pathname.endsWith("/batches") && init?.method === "POST") return batch("needs_owner");
      if (url.pathname.endsWith("/approve")) return batch("approved");
      if (url.pathname.includes("/batches/")) return batch("testing");
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tools/review"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("tab", { name: /Look things up \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Make changes \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Sensitive changes \(1\)/ })).toBeInTheDocument();
    expect(screen.getByText("1 operations cannot be used")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /Make changes/ }));
    await user.click(screen.getByRole("checkbox", { name: /\/tickets/ }));
    expect(screen.getByText(/1 selected/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Prepare the selected tools" }));
    expect(await screen.findByRole("article", { name: "Open a ticket" })).toBeInTheDocument();
    expect(screen.getByText("Opens a support ticket")).toBeInTheDocument();
    const approve = screen.getByRole("button", { name: "Approve and test the selected tools" });
    expect(approve).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Open a ticket" }));
    expect(approve).toBeDisabled(); // a tool that changes data also needs its effects confirmed
    expect(screen.getByRole("group", { name: "Confirm what this tool does" })).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "I checked this and it is right" }));
    expect(approve).toBeEnabled();
    await user.click(approve);
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/approve"))).toBe(true));
    const sent = calls.find((c) => c.path.endsWith("/approve"))!.body as { proposal_ids: string[]; confirmations: Record<string, unknown> };
    expect(sent.proposal_ids).toEqual(["prop-1"]);
    expect(sent.confirmations["prop-1"]).toEqual(effects);
    expect(await screen.findByRole("heading", { name: "Test and publish", level: 1 })).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/batches") && c.method === "POST")?.body).toMatchObject({
      spec_id: "spec-1234",
      operation_ids: ["op-post"],
      access: { callers: "customer", own_records_only: true },
      connection: { base_url: "http://sandbox.test:8102" },
    });
  });

  it("asks to connect first when no description was read", async () => {
    window.sessionStorage.clear();
    stub((url) => (url.pathname === "/v1/dashboard/tool-setup" ? empty : undefined));
    render(<MemoryRouter initialEntries={["/tools/review"]}><App /></MemoryRouter>);
    expect(await screen.findByText("Start by connecting your system.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Connect" })).toBeInTheDocument();
  });
});

describe("tool setup: test and publish", () => {
  it("publishes what passed into a draft and switches it on only on the second click", async () => {
    let state = "ready_to_publish";
    const calls = stub((url, init) => {
      if (url.pathname === "/v1/dashboard/tool-setup") return { ...empty, business_id: "biz-1", batches: [batch(state)] };
      if (url.pathname.endsWith("/publish")) {
        state = "published";
        return { batch: batch("published"), bundle_version: 3, tools: ["open_a_ticket"] };
      }
      if (url.pathname.endsWith("/activate")) return { bundle_version: 3, status: "active", tools: ["open_a_ticket"] };
      if (url.pathname.includes("/batches/") && init?.method !== "POST") return batch(state);
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/tools/publish?batch=batch-0001"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Test and publish", level: 1 })).toBeInTheDocument();
    expect(await screen.findByText("Ready to publish")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Publish the tools that passed" }));
    expect(await screen.findByText("Saved as version 3. Customers see no change until you use these tools.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/activate"))).toBe(false); // nothing is in use yet
    await user.click(screen.getByRole("button", { name: "Use these tools now" }));
    expect(await screen.findByText("Done. The assistant now uses: open_a_ticket.")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/activate"))?.body).toEqual({ bundle_version: 3 });
  });

  it("cannot publish while the tools are still being tested", async () => {
    stub((url) => {
      if (url.pathname === "/v1/dashboard/tool-setup") return { ...empty, business_id: "biz-1", batches: [batch("testing")] };
      if (url.pathname.includes("/batches/")) return batch("testing");
      return undefined;
    });
    render(<MemoryRouter initialEntries={["/tools/publish"]}><App /></MemoryRouter>);
    expect(await screen.findByText("Testing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish the tools that passed" })).toBeDisabled();
    expect(screen.getByText(/this can take a few minutes/)).toBeInTheDocument();
  });

  it("explains when the tests need something from the owner", async () => {
    const attention = { message: "The automatic tests need something from you.", needs_owner: ["Add a second test account"] };
    stub((url) => {
      if (url.pathname === "/v1/dashboard/tool-setup") return { ...empty, business_id: "biz-1", batches: [batch("needs_attention", attention)] };
      if (url.pathname.includes("/batches/")) return batch("needs_attention", attention);
      return undefined;
    });
    render(<MemoryRouter initialEntries={["/tools/publish"]}><App /></MemoryRouter>);
    expect(await screen.findByText("Add a second test account")).toBeInTheDocument();
    expect(screen.getByText("The tests need something from you:")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("asks to choose tools first when there is no batch", async () => {
    stub((url) => (url.pathname === "/v1/dashboard/tool-setup" ? empty : undefined));
    render(<MemoryRouter initialEntries={["/tools/publish"]}><App /></MemoryRouter>);
    expect(await screen.findByText("Choose your tools first.")).toBeInTheDocument();
  });
});
