import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AutomateNext, ForcedChosenTable } from "../components/AgentCards";
import { DICTIONARIES, I18nProvider } from "../i18n";

describe("the agent cards", () => {
  it("ranks the request types that end with a person", () => {
    render(
      <I18nProvider lang="en">
        <AutomateNext rows={[{ intent: "refund_request", handoffs: 3, turns: 6, rate: 0.5 }]} />
      </I18nProvider>,
    );
    expect(screen.getByText("refund_request")).toBeTruthy();
    expect(screen.getByText(/50%/)).toBeTruthy();
  });

  it("says so when nothing was handed over", () => {
    render(
      <I18nProvider lang="en">
        <AutomateNext rows={[]} />
      </I18nProvider>,
    );
    expect(screen.getByText("No handoffs in this period.")).toBeTruthy();
  });

  it("splits handoffs into forced and chosen by reason, in English and Arabic", () => {
    render(
      <I18nProvider lang="ar">
        <ForcedChosenTable byReason={{ no_evidence: { forced: 0, chosen: 2 }, mandatory_risk: { forced: 1, chosen: 0 } }} forced={1} chosen={2} />
      </I18nProvider>,
    );
    expect(screen.getByText(DICTIONARIES.ar["agent.forcedVsChosen"])).toBeTruthy();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
  });

  it("shows nothing when there were no handoffs", () => {
    const { container } = render(
      <I18nProvider lang="en">
        <ForcedChosenTable byReason={{}} forced={0} chosen={0} />
      </I18nProvider>,
    );
    expect(container.textContent).toBe("");
  });

  it("has every agent text in both languages", () => {
    const keys = (d: Record<string, string>) => Object.keys(d).filter((k) => k.startsWith("agent.")).sort();
    expect(keys(DICTIONARIES.ar)).toEqual(keys(DICTIONARIES.en));
  });
});
