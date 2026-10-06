import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { latencyRows, mixTotals, OTHER, rateRows, stackedReasonRows, valueRows } from "../lib/series";

const T = "shop_001";
const tenants = { tenants: [{ tenant_id: T, display_name: "Nile Style", default_locale: "en" }] };
const overview = {
  tenant_id: T, period_start: "2026-09-21T00:00:00Z", period_end: "2026-09-28T00:00:00Z",
  previous_start: "2026-09-14T00:00:00Z", previous_end: "2026-09-21T00:00:00Z",
  conversations: { value: 12, previous: 10, change: 2 },
  automation_rate: { value: 0.75, previous: 0.7, change: 0.05 },
  escalation_rate: { value: 0.25, previous: 0.3, change: -0.05 },
  unverified_results: { value: 1, previous: 0, change: 1 },
  p95_latency_ms: { value: 320, previous: 300, change: 20 },
  open_cases: 2,
};
const day = (n: number) => `2026-09-2${n}T00:00:00Z`;
const series: Record<string, unknown[]> = {
  conversations: [{ bucket_start: day(1), value: 3 }, { bucket_start: day(2), value: 9 }],
  automation_rate: [{ bucket_start: day(1), numerator: 2, denominator: 3, rate: 0.6667 }, { bucket_start: day(2), numerator: 0, denominator: 0, rate: null }],
  escalations: [
    { bucket_start: day(1), conversations: 3, by_reason: { policy_denied: 1 }, rates: {} },
    { bucket_start: day(2), conversations: 9, by_reason: { no_evidence: 2, policy_denied: 1 }, rates: {} },
  ],
  language_mix: [{ bucket_start: day(1), counts: { en: 2, ar: 1 } }, { bucket_start: day(2), counts: { en: 5, arabizi: 4 } }],
  latency: [{ bucket_start: day(1), overall: { count: 3, p50: 100, p95: 200 }, stages: {} }, { bucket_start: day(2), overall: { count: 0, p50: null, p95: null }, stages: {} }],
};

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function install(handlers: Record<string, (url: URL) => Response>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test");
    const handler = handlers[url.pathname];
    return handler ? handler(url) : respond({ error: { code: "NOT_FOUND", message: "not found" } }, 404);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const base = {
  "/v1/dashboard/tenants": () => respond(tenants),
  "/v1/dashboard/overview": () => respond(overview),
  "/v1/dashboard/timeseries": (url: URL) => respond({ tenant_id: T, metric: url.searchParams.get("metric"), bucket: "day", period_start: "", period_end: "", points: series[url.searchParams.get("metric") ?? ""] ?? [] }),
};

const app = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
afterEach(() => vi.unstubAllGlobals());

describe("series helpers", () => {
  const points = series.escalations as Record<string, unknown>[];
  it("turns points into rows", () => {
    expect(valueRows(series.conversations as never, "value", "day", "en").map((r) => r.value)).toEqual([3, 9]);
    expect(rateRows(series.automation_rate as never, "day", "en").map((r) => r.value)).toEqual([66.67, null]);
    expect(latencyRows(series.latency as never, "day", "en").map((r) => r.value)).toEqual([200, null]);
  });
  it("stacks the reasons, biggest first, and groups the rest as other", () => {
    const { rows, series: names } = stackedReasonRows(points, "day", "en");
    expect(names).toEqual(["no_evidence", "policy_denied"]); // 2 and 2: ties are broken by name
    expect(rows.map((r) => [r.no_evidence, r.policy_denied])).toEqual([[0, 1], [2, 1]]);
    const many = [{ bucket_start: day(1), by_reason: Object.fromEntries("abcdefghij".split("").map((k, i) => [k, 10 - i])) }];
    const grouped = stackedReasonRows(many, "day", "en");
    expect(grouped.series).toHaveLength(6);
    expect(grouped.series.at(-1)).toBe(OTHER);
    expect(grouped.rows[0]![OTHER]).toBe(5 + 4 + 3 + 2 + 1); // f..j
  });
  it("adds up the language mix, biggest first", () => {
    expect(mixTotals(series.language_mix as never)).toEqual([{ name: "en", value: 7 }, { name: "arabizi", value: 4 }, { name: "ar", value: 1 }]);
  });
});

describe("overview", () => {
  it("shows the tiles with the change and every chart with its numbers", async () => {
    install(base);
    app("/");
    expect(await screen.findByLabelText("Unverified results")).toHaveTextContent("up 1");
    expect(screen.getByLabelText("Slowest 5% of replies (p95)")).toHaveTextContent("320 ms");
    for (const title of ["Conversations over time", "Automation rate over time", "Escalations by reason", "Language mix", "Reply time over time (p95, ms)"]) {
      expect(await screen.findByRole("img", { name: title })).toBeInTheDocument();
    }
    const stacked = (await screen.findByText("Escalations by reason")).closest("figure")!;
    expect(within(stacked).getByText("Policy said no")).toBeInTheDocument(); // series are named in words, not only colours
    expect(within(stacked).getByText("No policy answer found")).toBeInTheDocument();
    const mix = screen.getByText("Language mix").closest("figure")!;
    expect(within(mix).getByText("Arabizi")).toBeInTheDocument();
  });
});

const row = (id: string, over: Record<string, unknown> = {}) => ({
  conversation_id: id, first_at: "2026-09-27T10:00:00Z", last_at: "2026-09-27T10:05:00Z", turns: 2, language: "en",
  intents: ["refund_request"], outcome: "handoff", escalation_reason: "policy_denied", has_case: true, case_id: "case-1",
  case_status: "open", actions: 0, avg_latency_ms: 120.4, problems: ["handoff: policy_denied"], ...over,
});

describe("conversations", () => {
  it("lists them with the problem badge and passes the filters from the address", async () => {
    const calls: URL[] = [];
    install({
      ...base,
      "/v1/dashboard/conversations": (url) => {
        calls.push(url);
        return respond({ items: [row("c1"), row("c2", { problems: [], outcome: "answer", escalation_reason: null, has_case: false })], next_cursor: null });
      },
    });
    app("/conversations?status=escalated&reason=policy_denied&language=en&q=refund");
    expect(await screen.findAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("1 ⚠", { exact: false })).toHaveAttribute("title", "handoff: policy_denied");
    expect(screen.getByText("OK")).toBeInTheDocument();
    const last = calls.at(-1)!;
    expect(Object.fromEntries(["status", "reason", "language", "q"].map((k) => [k, last.searchParams.get(k)]))).toEqual({
      status: "escalated", reason: "policy_denied", language: "en", q: "refund",
    });
    expect(screen.getByLabelText("Status")).toHaveValue("escalated");
  });

  it("searching and choosing a filter ask the service again", async () => {
    const calls: URL[] = [];
    install({ ...base, "/v1/dashboard/conversations": (url) => { calls.push(url); return respond({ items: [row("c1")], next_cursor: null }); } });
    app("/conversations");
    await screen.findAllByRole("row");
    await userEvent.type(screen.getByRole("searchbox"), "warranty");
    await waitFor(() => expect(calls.some((u) => u.searchParams.get("q") === "warranty")).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "automated");
    await waitFor(() => expect(calls.some((u) => u.searchParams.get("status") === "automated")).toBe(true));
  });

  it("loads the next page and shows an error if that fails", async () => {
    let n = 0;
    install({
      ...base,
      "/v1/dashboard/conversations": (url) => {
        if (!url.searchParams.get("cursor")) return respond({ items: [row("c1")], next_cursor: "next" });
        n += 1;
        return n === 1 ? respond({ error: { code: "UPSTREAM_UNAVAILABLE", message: "busy" } }, 503) : respond({ items: [row("c2")], next_cursor: null });
      },
    });
    app("/conversations");
    await screen.findAllByRole("row");
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("busy");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(3));
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });
});

const trace = (over: Record<string, unknown>) => ({
  trace_id: "t1", request_id: "r1", tenant_id: T, conversation_id: "c1", turn_index: 0, kind: "customer_turn",
  customer_message: "I want a refund for NS-20512", language: "en", intents: [{ name: "refund_request", confidence: 0.8 }],
  entities: { order_id: "NS-20512" }, nlu_method: "rules", identity: { verified: true, customer_id: "C-1001", method: "order+phone", attempts: 0 },
  evidence: [], policy: [], tool_calls: [], decision: "answer", decision_reason: "", response_text: "", response_citations: [], errors: [],
  steps: [{ stage: "understand", status: "ok", duration_ms: 2 }, { stage: "handler", status: "ok", duration_ms: 6 }, { stage: "risk_screen", status: "skipped", duration_ms: 0 }],
  latency_ms: 8.4, ...over,
});

function conversation(traces: unknown[], extra: Record<string, unknown> = {}) {
  return { tenant_id: T, conversation_id: "c1", transcript: [{ role: "customer", text: "I want a refund for NS-20512", trace_id: "t1", turn_index: 0 }, { role: "agent", text: "Sorry, that is outside the window.", trace_id: "t1", turn_index: 0 }], traces, case: null, ...extra };
}

describe("the decision view", () => {
  it("explains a refused refund: the rule, its policy passage, and why it ended with a person", async () => {
    const denied = trace({
      decision: "handoff", escalation_reason: "policy_denied", decision_reason: "the refund window is 14 days and this order was delivered 20 days ago",
      policy: [{ request_id: "p1", action: "create_refund", decision: "deny", reason_code: "R-REFUND-14D", citations: ["refund_policy@v1#s1"] }],
      evidence: [{ citation: "refund_policy@v1#s1", document_id: "refund_policy", version: "v1", score: 9.5 }],
      response_text: "I'm sorry, refunds are possible within 14 days.", response_citations: ["refund_policy@v1#s1"], handoff_case_id: "case-1",
    });
    install({
      ...base,
      "/v1/dashboard/conversations/c1": () => respond(conversation([denied], { case: { case_id: "case-1", status: "open", package: { reason: "policy_denied" } } })),
      "/v1/dashboard/passage": () => respond({ citation: "refund_policy@v1#s1", document_id: "refund_policy", version: "v1", section: "1. Window", language: "en", text: "Refunds are accepted within 14 days of delivery." }),
    });
    app("/conversations/c1");
    const card = await screen.findByRole("article", { name: "Message 1" });
    expect(within(card).getByText("refund_request", { exact: false })).toBeInTheDocument();
    expect(within(card).getByText("NS-20512", { selector: "dd" })).toBeInTheDocument();
    expect(within(card).getByText(/verified \(C-1001/)).toBeInTheDocument();
    expect(within(card).getByText("deny")).toBeInTheDocument();
    expect(within(card).getByText("R-REFUND-14D")).toBeInTheDocument();
    expect(within(card).getByText(/the refund window is 14 days/)).toBeInTheDocument();
    expect(within(card).getAllByText("Policy said no").length).toBeGreaterThan(0);
    expect(screen.getByText("Handoff case:")).toBeInTheDocument();
    await userEvent.click(within(card).getAllByRole("button", { name: "refund_policy@v1#s1" })[0]!);
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Refunds are accepted within 14 days of delivery.")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("explains an approval: a rule that needs a person, the pending tool call, and the handoff", async () => {
    const approval = trace({
      customer_message: "refund 3450 EGP for NS-20934", decision: "handoff", escalation_reason: "approval_required",
      decision_reason: "the amount is above the limit, so a person must approve it",
      policy: [{ request_id: "p2", action: "create_refund", decision: "require_human", reason_code: "R-REFUND-LIMIT", citations: [] }],
      tool_calls: [{ request_id: "k1", tool: "get_order", operation_kind: "read", status: "success", audit_id: "aud-1", latency_ms: 12 }],
      errors: ["policy search failed (BACKEND_UNAVAILABLE), attempt 1"],
    });
    install({ ...base, "/v1/dashboard/conversations/c1": () => respond(conversation([approval])) });
    app("/conversations/c1");
    const card = await screen.findByRole("article", { name: "Message 1" });
    expect(within(card).getByText("require_human")).toBeInTheDocument();
    expect(within(card).getByText("R-REFUND-LIMIT")).toBeInTheDocument();
    expect(within(card).getByText("Needs a person's approval")).toBeInTheDocument();
    expect(within(card).getByText(/ref aud-1/)).toBeInTheDocument();
    expect(within(card).getByText("policy search failed (BACKEND_UNAVAILABLE), attempt 1")).toBeInTheDocument();
    expect(within(card).getByText("no policy passage used")).toBeInTheDocument();
  });

  it("shows the stage timing bar (stages that did not run are left out) and the raw JSON on request", async () => {
    install({ ...base, "/v1/dashboard/conversations/c1": () => respond(conversation([trace({})])) });
    app("/conversations/c1");
    const bar = await screen.findByRole("img", { name: /understand 2\.0 ms, handler 6\.0 ms/ });
    expect(bar.children).toHaveLength(2);
    expect(screen.queryByText(/risk_screen/)).not.toBeInTheDocument();
    expect(screen.queryByText(/"trace_id": "t1"/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Raw JSON" }));
    expect(screen.getByText(/"trace_id": "t1"/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide JSON" }));
    expect(screen.queryByText(/"trace_id": "t1"/)).not.toBeInTheDocument();
  });

  it("says so when the conversation does not exist", async () => {
    install({ ...base, "/v1/dashboard/conversations/c1": () => respond({ error: { code: "NOT_FOUND", message: "conversation not found" } }, 404) });
    app("/conversations/c1");
    expect(await screen.findByRole("alert")).toHaveTextContent("conversation not found");
  });
});
