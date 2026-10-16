import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";

const T = "shop_001";
const tenants = { tenants: [{ tenant_id: T, display_name: "Nile Style", default_locale: "en" }] };
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

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

const doc = (extra: Record<string, unknown>) => ({
  document_id: "returns", version: "v1", file: "returns.v1.md", filename: "Returns.md", status: "indexed", error: null, current: true, ...extra,
});

describe("knowledge page", () => {
  it("lists documents with their status, shows why a file failed, and updates the search", async () => {
    let docs = [doc({ status: "uploaded" }), doc({ document_id: "scan", file: "scan.v1.pdf", filename: "scan.pdf", status: "failed", error: "no readable text was found in this file", current: false })];
    const calls = stub((url, init) => {
      if (url.pathname === "/v1/dashboard/knowledge/documents" && !init?.method) return { tenant_id: T, managed: true, documents: docs };
      if (url.pathname === "/v1/dashboard/knowledge/reindex") return { job_id: "job1", status: "running" };
      if (url.pathname === "/v1/dashboard/knowledge/jobs/job1") {
        docs = [doc({})];
        return { job_id: "job1", status: "succeeded", result: { current_passages: 7 }, error: null };
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/knowledge"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "Knowledge", level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement.getAttribute("data-theme")).toBe("aurora-light"));
    expect(await screen.findByText("Uploaded, not searchable yet")).toBeInTheDocument();
    expect(screen.getByText("Could not be read")).toBeInTheDocument();
    expect(screen.getByText("no readable text was found in this file")).toBeInTheDocument();
    expect(screen.getByText("Waiting for the search update: 1.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Update the search" }));
    expect(await screen.findByText("The search is up to date: 7 passages.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Searchable")).toBeInTheDocument());
    expect(calls.some((c) => c.path.endsWith("/reindex") && c.method === "POST")).toBe(true);
  });

  it("uploads chosen files as base64 and removes a document", async () => {
    const calls = stub((url, init) => {
      if (url.pathname.endsWith("/documents") && init?.method === "POST") return doc({ status: "uploaded" });
      if (url.pathname.endsWith("/documents/returns") && init?.method === "DELETE") return { document_id: "returns", versions_removed: 1, reindex_needed: true };
      if (url.pathname.endsWith("/documents")) return { tenant_id: T, managed: true, documents: [doc({})] };
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/knowledge"]}><App /></MemoryRouter>);
    const input = await screen.findByLabelText(/Upload files/);
    await user.upload(input, new File(["# Policy\n\n## 1. A\ntext"], "Policy.md", { type: "text/markdown" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/documents"))).toBe(true));
    const sent = calls.find((c) => c.method === "POST" && c.path.endsWith("/documents"))!.body as { filename: string; content_base64: string };
    expect(sent.filename).toBe("Policy.md");
    expect(atob(sent.content_base64)).toContain("# Policy");
    await user.click(await screen.findByRole("button", { name: "Remove Returns.md" }));
    expect(await screen.findByText("Removed. Update the search to take it out.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("asks for a first upload when the business has no documents", async () => {
    stub((url) => (url.pathname.endsWith("/documents") ? { tenant_id: T, managed: true, documents: [] } : undefined));
    render(<MemoryRouter initialEntries={["/knowledge"]}><App /></MemoryRouter>);
    expect(await screen.findByText("No documents yet.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Upload files/)).toBeInTheDocument();
  });

  it("shows a shipped business as read-only", async () => {
    stub((url) => (url.pathname.endsWith("/documents") ? { tenant_id: T, managed: false, documents: [doc({})] } : undefined));
    render(<MemoryRouter initialEntries={["/knowledge"]}><App /></MemoryRouter>);
    expect(await screen.findByText(/read-only here/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update the search" })).not.toBeInTheDocument();
  });
});

const rule = (extra: Record<string, unknown> = {}) => ({
  rule_id: "R-REFUND-14D", action: "create_refund", approval_status: "proposed", approved_by: null, stale: false, effect: "allow", else_effect: "deny",
  source: { document_id: "returns", version: "v1", citation: "returns@v1#s1", quote: "You may return an item within 14 days." },
  conditions: [], user_message: { ar: "", en: "Returns are accepted within 14 days." }, history: [], ...extra,
});

describe("guardrails page", () => {
  it("shows each rule with its quote and source, and approves it", async () => {
    let rules = [rule()];
    const calls = stub((url, init) => {
      if (url.pathname === "/v1/dashboard/guardrails" && !init?.method) return { tenant_id: T, ruleset: { ruleset_version: "abc", count: rules.filter((r) => r.approval_status === "approved").length, approved_rule_ids: [] }, rules };
      if (url.pathname.endsWith("/approve")) {
        rules = [rule({ approval_status: "approved", approved_by: "Mona", history: [{ at: "2026-10-12T10:00:00Z", event: "approved", by: "Mona" }] })];
        return rules[0];
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/guardrails"]}><App /></MemoryRouter>);
    const card = await screen.findByRole("article", { name: "R-REFUND-14D" });
    expect(within(card).getByText(/You may return an item within 14 days/)).toBeInTheDocument();
    expect(within(card).getByText("returns@v1#s1")).toBeInTheDocument();
    expect(within(card).getByText("Waiting for your decision")).toBeInTheDocument();
    expect(screen.getByText("0 rules are in force")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved by Mona")).toBeInTheDocument();
    expect(screen.getByText("1 rules are in force")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/approve"))?.method).toBe("POST");
  });

  it("marks a rule whose policy changed as not in force, and rejects another", async () => {
    stub((url, init) => {
      if (url.pathname === "/v1/dashboard/guardrails" && !init?.method)
        return { tenant_id: T, ruleset: { ruleset_version: "abc", count: 0, approved_rule_ids: [] }, rules: [rule({ approval_status: "approved", approved_by: "Mona", stale: true })] };
      return undefined;
    });
    render(<MemoryRouter initialEntries={["/guardrails"]}><App /></MemoryRouter>);
    expect(await screen.findByText("Its policy changed: needs a new look. Not in force.")).toBeInTheDocument();
  });

  it("shows the reason when a rule cannot be approved", async () => {
    stub((url, init) => {
      if (url.pathname === "/v1/dashboard/guardrails" && !init?.method) return { tenant_id: T, ruleset: { ruleset_version: "abc", count: 0, approved_rule_ids: [] }, rules: [rule()] };
      if (url.pathname.endsWith("/approve")) return undefined;
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/guardrails"]}><App /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not approved:");
  });

  it("asks the model for suggestions and says how many came back", async () => {
    let jobs = 0;
    stub((url, init) => {
      if (url.pathname === "/v1/dashboard/guardrails" && !init?.method) return { tenant_id: T, ruleset: { ruleset_version: "abc", count: 0, approved_rule_ids: [] }, rules: [] };
      if (url.pathname.endsWith("/extract")) return { job_id: "j2", status: "running" };
      if (url.pathname.endsWith("/jobs/j2")) {
        jobs += 1;
        return { job_id: "j2", status: "succeeded", result: { added: 3 }, error: null };
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/guardrails"]}><App /></MemoryRouter>);
    expect(await screen.findByText("No rules yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Suggest rules from my documents" }));
    await waitFor(() => expect(jobs).toBeGreaterThan(0));
  });
});
