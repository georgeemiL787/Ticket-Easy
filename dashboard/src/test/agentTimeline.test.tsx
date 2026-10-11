import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TimelineTurn } from "../api/types/DashboardConversation";
import { AgentTimeline } from "../components/AgentTimeline";
import { DICTIONARIES, I18nProvider } from "../i18n";

const turn: TimelineTurn = {
  trace_id: "t1",
  turn_index: 0,
  agent_mode: "on",
  plan: [
    { text: "answer the status", status: "done" },
    { text: "ask for the phone", status: "open" },
  ],
  plan_revision: 2,
  plan_changes: ["plan revision 1", "plan revision 2"],
  moves: [
    {
      number: 1,
      kind: "look_up",
      why: "to check",
      tool: "create_refund",
      summary: "refused: it changes something",
      refused: true,
      checks: [
        { name: "tool_is_published", passed: true, reason: "" },
        { name: "tool_only_reads", passed: false, reason: "create_refund changes something" },
      ],
      duration_ms: 1.2,
    },
  ],
  reviewer: { status: "blocked", detail: "says it was refunded" },
  fallback_reason: "two refused moves in a row",
  escalation_reason: "no_evidence",
  escalation_kind: "chosen",
  tried: ["searched the policy twice"],
};

describe("the agent timeline", () => {
  it("shows the plan, a move with pass and fail chips, the reviewer, the fallback and what was tried", () => {
    render(
      <I18nProvider lang="en">
        <AgentTimeline turn={turn} />
      </I18nProvider>,
    );
    expect(screen.getByText(/answer the status/)).toBeTruthy();
    expect(screen.getByText(/✓ tool_is_published/)).toBeTruthy();
    expect(screen.getByText(/✗ tool_only_reads/).className).toContain("check-fail");
    expect(screen.getByText(/refused by code/)).toBeTruthy();
    expect(screen.getByText(/blocked the reply/)).toBeTruthy();
    expect(screen.getByText(/two refused moves in a row/)).toBeTruthy();
    expect(screen.getByText(/chosen after trying/)).toBeTruthy();
    expect(screen.getByText("searched the policy twice")).toBeTruthy();
  });

  it("is written in Arabic too, with the same keys in both languages", () => {
    const keys = (d: Record<string, string>) => Object.keys(d).filter((k) => k.startsWith("agent.") || k.startsWith("move.")).sort();
    expect(keys(DICTIONARIES.ar)).toEqual(keys(DICTIONARIES.en));
    render(
      <I18nProvider lang="ar">
        <AgentTimeline turn={turn} />
      </I18nProvider>,
    );
    expect(screen.getByText(/الكود رفضها/)).toBeTruthy();
  });

  it("renders nothing for a message the agent did not work", () => {
    const { container } = render(
      <I18nProvider lang="en">
        <AgentTimeline turn={{ ...turn, plan: [], moves: [], fallback_reason: null, escalation_kind: null }} />
      </I18nProvider>,
    );
    expect(container.textContent).toBe("");
  });
});
