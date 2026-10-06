import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { availableActions, formatRemaining } from "../lib/caseActions";

const T = "shop_001";
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const error = (code: string, message: string, status: number) => respond({ error: { code, message } }, status);

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

function install(routes: Record<string, (call: Call) => Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://test");
      const call: Call = { method: init?.method ?? "GET", path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : null };
      calls.push(call);
      const handler = routes[`${call.method} ${call.path}`];
      return handler ? handler(call) : error("NOT_FOUND", "not found", 404);
    }),
  );
  return calls;
}

const tenants = { tenants: [{ tenant_id: T, display_name: "Nile Style", default_locale: "en" }] };
const row = (id: string, over: Record<string, unknown> = {}) => ({
  case_id: id, conversation_id: `conv-${id}`, status: "open", claimed_by: null, reason: "approval_required", priority: "high",
  language: "en", summary: "refund above the limit", has_pending_approval: true, created_at: "2026-09-28T11:00:00Z", age_seconds: 600,
  sla_seconds: 3600, remaining_seconds: 3000, overdue: false, ...over,
});
const queue = (rows: unknown[]) => ({ tenant_id: T, now: "2026-09-28T12:00:00Z", overdue: rows.filter((r) => (r as { overdue: boolean }).overdue).length, rows });
const summary = { tenant_id: T, period_start: "", period_end: "", conversations: 4, by_reason_total: { approval_required: 2, policy_denied: 1 }, by_reason: [], open_cases: 2, claimed_cases: 0, top_rules: [{ rule: "create_refund:R-REFUND-LIMIT", decision: "require_human", count: 2 }] };
const fullCase = (over: Record<string, unknown> = {}) => ({
  case_id: "k1", tenant_id: T, conversation_id: "conv-k1", status: "open", claimed_by: null, events: [], created_at: "2026-09-28T11:00:00Z", updated_at: "2026-09-28T11:00:00Z",
  pending_approval: { proposal_id: "p1", tool: "create_refund", capability: "create_refund", arguments: { amount: 3450, order_id: "NS-20934" }, reason: "above the limit" },
  package: { summary: "Refund of 3450 needs approval", summary_source: "template", reason: "approval_required", priority: "high", suggested_next_step: "decide", rule_answers: [{ request_id: "r", action: "create_refund", decision: "require_human", reason_code: "R-REFUND-LIMIT", citations: [] }] },
  ...over,
});
const base = {
  "GET /v1/dashboard/tenants": () => respond(tenants),
  "GET /v1/dashboard/escalations": () => respond(summary),
  "GET /v1/dashboard/timeseries": () => respond({ tenant_id: T, metric: "escalations", bucket: "day", period_start: "", period_end: "", points: [{ bucket_start: "2026-09-27T00:00:00Z", conversations: 4, by_reason: { approval_required: 2 }, rates: {} }] }),
};
const app = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("case action rules", () => {
  it("offers only what the service would accept", () => {
    expect(availableActions({ status: "open" }, "sara")).toMatchObject({ claim: true, decide: false, resolve: false });
    expect(availableActions({ status: "open" }, "")).toMatchObject({ claim: false });
    const mine = { status: "claimed", claimed_by: "sara", pending_approval: { x: 1 } };
    expect(availableActions(mine, "sara")).toEqual({ claim: false, release: true, resolve: true, returnToAgent: true, decide: true });
    expect(availableActions(mine, "omar")).toEqual({ claim: false, release: false, resolve: false, returnToAgent: false, decide: false });
    expect(availableActions({ status: "resolved", claimed_by: "sara" }, "sara").resolve).toBe(false);
  });
  it("words the time left", () => {
    expect(formatRemaining(3000)).toEqual({ text: "50 min", overdue: false });
    expect(formatRemaining(-300)).toEqual({ text: "5 min", overdue: true });
    expect(formatRemaining(45)).toEqual({ text: "45 s", overdue: false });
    expect(formatRemaining(7200).text).toBe("2 h");
  });
});

describe("the escalations page", () => {
  it("shows the queue with overdue cases marked, the reasons and the rules behind them", async () => {
    install({ ...base, "GET /v1/dashboard/queue": () => respond(queue([row("late", { overdue: true, remaining_seconds: -300, priority: "urgent", reason: "mandatory_risk" }), row("ok")])) });
    app("/escalations");
    const late = (await screen.findByText("Risky topic (fraud, legal, safety)")).closest("tr")!;
    expect(late).toHaveClass("overdue");
    expect(within(late).getByText("Overdue: 5 min")).toBeInTheDocument();
    const fine = screen.getAllByRole("row")[2]!; // overdue cases come first
    expect(fine).not.toHaveClass("overdue");
    expect(within(fine).getByText("50 min")).toBeInTheDocument();
    expect(await screen.findByText("create_refund:R-REFUND-LIMIT")).toBeInTheDocument();
    expect(screen.getAllByText("Policy said no").length).toBeGreaterThan(0);
  });

  it("an empty queue says so, and a failing queue offers a retry", async () => {
    install({ ...base, "GET /v1/dashboard/queue": () => respond(queue([])) });
    app("/escalations");
    expect(await screen.findByText("No cases in the queue.")).toBeInTheDocument();
    install({ ...base, "GET /v1/dashboard/queue": () => error("UPSTREAM_UNAVAILABLE", "the database is unavailable", 503) });
    app("/escalations");
    expect((await screen.findAllByRole("alert")).some((a) => a.textContent?.includes("the database is unavailable"))).toBe(true);
  });

  it("a manager reassigns a case, and the service's refusal is shown", async () => {
    let refuse = false;
    const calls = install({
      ...base,
      "GET /v1/dashboard/queue": () => respond(queue([row("k1")])),
      "POST /v1/handoff/cases/k1/assign": () => (refuse ? error("FORBIDDEN", "omar is not a manager of shop_001", 403) : respond(fullCase({ status: "claimed", claimed_by: "sara" }))),
    });
    app("/escalations");
    await screen.findByText("Needs a person's approval", { selector: "button" });
    await userEvent.type(screen.getByLabelText("Your name (manager)"), "mona");
    await userEvent.type(screen.getByLabelText("Assign to k1"), "sara");
    await userEvent.click(screen.getByRole("button", { name: "Reassign" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/assign"))?.body).toEqual({ agent: "mona", assignee: "sara" }));
    expect(localStorage.getItem("dashboard.name")).toBe("mona");

    refuse = true;
    await userEvent.click(screen.getByRole("button", { name: "Reassign" }));
    expect(await screen.findByText("omar is not a manager of shop_001")).toBeInTheDocument();
  });

  it("reassign needs a name and an assignee", async () => {
    const calls = install({ ...base, "GET /v1/dashboard/queue": () => respond(queue([row("k1")])) });
    app("/escalations");
    await screen.findByText("Needs a person's approval", { selector: "button" });
    await userEvent.click(screen.getByRole("button", { name: "Reassign" }));
    expect(await screen.findByText("Type your name first.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("a large refund is approved from the page: claim it, then approve, and the queue reloads", async () => {
    let state = fullCase();
    const calls = install({
      ...base,
      "GET /v1/dashboard/queue": () => respond(queue([row("k1")])),
      "GET /v1/handoff/cases/k1": () => respond(state),
      "POST /v1/handoff/cases/k1/claim": () => {
        state = fullCase({ status: "claimed", claimed_by: "sara" });
        return respond(state);
      },
      "POST /v1/handoff/cases/k1/decision": () => {
        state = fullCase({ status: "claimed", claimed_by: "sara", pending_approval: null });
        return respond(state);
      },
    });
    app("/escalations");
    await userEvent.type(await screen.findByLabelText("Your name (manager)"), "sara");
    await userEvent.click(await screen.findByText("Needs a person's approval", { selector: "button" }));
    const panel = await screen.findByRole("complementary");
    expect(await within(panel).findByText("create_refund")).toBeInTheDocument();
    expect(within(panel).getByText("3450")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument(); // not claimed yet
    await userEvent.click(within(panel).getByRole("button", { name: "Claim" }));
    const approve = await within(panel).findByRole("button", { name: "Approve" });
    await userEvent.type(within(panel).getByLabelText("Note (optional)"), "checked with the customer");
    const before = calls.filter((c) => c.path === "/v1/dashboard/queue").length;
    await userEvent.click(approve);
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/decision"))?.body).toEqual({ agent: "sara", approve: true, note: "checked with the customer" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/v1/dashboard/queue").length).toBeGreaterThan(before));
    await waitFor(() => expect(within(panel).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument());
  });

  it("a refused action shows the service's message in the panel", async () => {
    install({
      ...base,
      "GET /v1/dashboard/queue": () => respond(queue([row("k1")])),
      "GET /v1/handoff/cases/k1": () => respond(fullCase()),
      "POST /v1/handoff/cases/k1/claim": () => error("INVALID_STATE", "the case is claimed by omar", 409),
    });
    app("/escalations");
    await userEvent.type(await screen.findByLabelText("Your name (manager)"), "sara");
    await userEvent.click(await screen.findByText("Needs a person's approval", { selector: "button" }));
    await userEvent.click(await screen.findByRole("button", { name: "Claim" }));
    expect(await screen.findByText("the case is claimed by omar")).toBeInTheDocument();
  });
});

describe("the actions, unanswered and alerts pages", () => {
  it("shows per tool calls, failures, top error codes and speed", async () => {
    install({
      ...base,
      "GET /v1/dashboard/tools": () => respond({ tenant_id: T, period_start: "", period_end: "", tools: [
        { tool: "create_return", calls: 10, failures: 6, rate: 0.6, errors: { TIMEOUT: 4, BAD_INPUT: 2 }, p50_ms: 20.4, p95_ms: 90.2 },
        { tool: "get_order", calls: 30, failures: 0, rate: 0, errors: {}, p50_ms: 5, p95_ms: 12 },
      ] }),
    });
    app("/actions");
    const bad = (await screen.findByText("create_return")).closest("tr")!;
    expect(bad).toHaveClass("overdue");
    expect(within(bad).getByText("TIMEOUT ×4, BAD_INPUT ×2")).toBeInTheDocument();
    expect(within(bad).getByText("60%")).toBeInTheDocument();
    expect(within(bad).getByText("90")).toBeInTheDocument();
    const fine = screen.getByText("get_order").closest("tr")!;
    expect(fine).not.toHaveClass("overdue");
  });

  it("shows the grouped unanswered questions with count, examples and last seen", async () => {
    install({
      ...base,
      "GET /v1/dashboard/knowledge-gaps": () => respond({ tenant_id: T, period_start: "", period_end: "", questions_without_answer: 3, groups: [
        { question: "Do you sell furniture?", count: 2, examples: ["Do you sell furniture?", "furniture please"], last_seen: "2026-09-27T10:00:00Z" },
        { question: "هل عندكم تقسيط؟", count: 1, examples: ["هل عندكم تقسيط؟"], last_seen: "2026-09-26T10:00:00Z" },
      ] }),
    });
    app("/unanswered");
    expect(await screen.findByText(/Questions without an answer: 3/)).toBeInTheDocument();
    const group = screen.getByText("furniture please").closest("tr")!;
    expect(within(group).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("هل عندكم تقسيط؟", { selector: "td" })).toHaveAttribute("dir", "auto");
  });

  it("lists alerts, open ones first, from the alerts endpoint", async () => {
    install({
      ...base,
      "GET /v1/dashboard/alerts": () => respond([
        { alert_id: "a1", tenant_id: T, rule: "service_down", severity: "critical", opened_at: "2026-09-27T09:00:00Z", resolved_at: "2026-09-27T10:00:00Z", acknowledged_by: "sara", details: {} },
        { alert_id: "a2", tenant_id: T, rule: "action_failing", severity: "warning", opened_at: "2026-09-26T09:00:00Z", resolved_at: null, acknowledged_by: null, details: {} },
      ]),
    });
    app("/alerts");
    const rows = await screen.findAllByRole("row");
    expect(within(rows[1]!).getByText("action_failing")).toBeInTheDocument(); // the open one is first
    expect(within(rows[2]!).getByText("sara")).toBeInTheDocument();
  });

  it("an alerts endpoint that does not exist yet gives an empty state, and no alerts says all is well", async () => {
    install(base);
    app("/alerts");
    expect(await screen.findByText("Alerts are not available yet.")).toBeInTheDocument();
    install({ ...base, "GET /v1/dashboard/alerts": () => respond([]) });
    app("/alerts");
    expect(await screen.findByText("No alerts. Everything looks normal.")).toBeInTheDocument();
  });
});
