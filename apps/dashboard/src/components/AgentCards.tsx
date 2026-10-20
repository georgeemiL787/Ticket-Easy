import { useApi } from "../api/useApi";
import type { DashboardAgent } from "../api/types/DashboardAgent";
import { labelFor, useI18n, type Translate } from "../i18n";
import { useFilters } from "../state/useFilters";
import { Async } from "./Async";
import { KpiTile } from "./Kpi";

function kpi(value: number | null, previous: number | null) {
  return { value, previous, change: value !== null && previous !== null ? value - previous : null };
}

/** One row per request type that most often ends with a person: what to automate next. */
export function AutomateNext({ rows }: { rows: { intent: string; handoffs: number; turns: number; rate: number }[] }) {
  const { t, lang } = useI18n();
  if (rows.length === 0) return <p className="muted">{t("agent.noHandoffs")}</p>;
  const pct = new Intl.NumberFormat(lang === "ar" ? "ar-EG" : "en-GB", { style: "percent", maximumFractionDigits: 0 });
  return (
    <table>
      <thead>
        <tr>
          <th>{t("agent.requestType")}</th>
          <th>{t("agent.handoffs")}</th>
          <th>{t("agent.ofMessages")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.intent}>
            <td>{r.intent}</td>
            <td>{r.handoffs}</td>
            <td>
              {r.turns} <span className="muted">({pct.format(r.rate)})</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function reasonLabel(t: Translate, key: string): string {
  const k = `agent.fallbackReason.${key}` as "agent.fallbackReason.other";
  return t(k);
}

/** The agent's own numbers on the overview: fallbacks and why, reviewer blocks, moves per message, who chose to hand over. */
export function AgentCards() {
  const { t } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardAgent>("/v1/dashboard/agent", {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
  });
  return (
    <Async state={state} isEmpty={(d) => d.now.agent_turns === 0 && d.previous.agent_turns === 0}>
      {(d) => (
        <section aria-label={t("agent.overviewTitle")}>
          <h2>{t("agent.overviewTitle")}</h2>
          <div className="kpis">
            <KpiTile label={t("agent.fallbackRate")} kpi={kpi(d.now.fallback_rate, d.previous.fallback_rate)} format="percent" higherIsBetter={false} />
            <KpiTile label={t("agent.movesPerMessage")} kpi={kpi(d.now.moves_per_message, d.previous.moves_per_message)} format="count" higherIsBetter={false} />
            <KpiTile label={t("agent.blockedReplies")} kpi={kpi(d.now.reviewer_blocked_replies, d.previous.reviewer_blocked_replies)} format="count" higherIsBetter={false} />
            <KpiTile label={t("agent.blockedActions")} kpi={kpi(d.now.reviewer_blocked_actions, d.previous.reviewer_blocked_actions)} format="count" higherIsBetter={false} />
            <KpiTile label={t("agent.handoffsForced")} kpi={kpi(d.now.handoffs_forced, d.previous.handoffs_forced)} format="count" higherIsBetter={false} />
            <KpiTile label={t("agent.handoffsChosen")} kpi={kpi(d.now.handoffs_chosen, d.previous.handoffs_chosen)} format="count" higherIsBetter={false} />
          </div>
          {Object.keys(d.now.fallback_reasons).length > 0 ? (
            <>
              <h3>{t("agent.fallbackReasons")}</h3>
              <table>
                <tbody>
                  {Object.entries(d.now.fallback_reasons).map(([reason, count]) => (
                    <tr key={reason}>
                      <td>{reasonLabel(t, reason)}</td>
                      <td>{count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </section>
      )}
    </Async>
  );
}

/** Forced vs chosen handoffs, by reason (escalations page). */
export function ForcedChosenTable({ byReason, forced, chosen }: { byReason: Record<string, Record<string, number>>; forced: number; chosen: number }) {
  const { t } = useI18n();
  if (forced + chosen === 0) return null;
  return (
    <>
      <h2>{t("agent.forcedVsChosen")}</h2>
      <p className="muted">{t("agent.forcedVsChosenExplain")}</p>
      <table>
        <thead>
          <tr>
            <th>{t("esc.reason")}</th>
            <th>{t("agent.forcedShort")}</th>
            <th>{t("agent.chosenShort")}</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(byReason).map(([reason, kinds]) => (
            <tr key={reason}>
              <td>{labelFor(t, "reason", reason)}</td>
              <td>{kinds.forced ?? 0}</td>
              <td>{kinds.chosen ?? 0}</td>
            </tr>
          ))}
          <tr>
            <th scope="row">{t("common.total")}</th>
            <td>{forced}</td>
            <td>{chosen}</td>
          </tr>
        </tbody>
      </table>
    </>
  );
}
