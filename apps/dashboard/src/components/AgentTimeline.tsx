import type { TimelineTurn } from "../api/types/DashboardConversation";
import { labelFor, useI18n } from "../i18n";

/** What the agent did on one message: the plan, then one card per move with the checks code made as pass/fail chips. */
export function AgentTimeline({ turn }: { turn: TimelineTurn }) {
  const { t } = useI18n();
  const nothing = turn.moves.length === 0 && turn.plan.length === 0 && !turn.fallback_reason && !turn.escalation_kind;
  if (nothing) return null;
  return (
    <section className="agent-timeline" aria-label={t("agent.title")}>
      <h4>{t("agent.title")}</h4>
      <p className="small">
        {turn.agent_mode ? (
          <span className="chip">
            {t("agent.mode")}: {turn.agent_mode}
          </span>
        ) : null}
        {turn.escalation_kind ? (
          <span className={`chip kind-${turn.escalation_kind}`}>{t(turn.escalation_kind === "forced" ? "agent.forced" : "agent.chosen")}</span>
        ) : null}
        {turn.escalation_reason ? <span className="chip">{labelFor(t, "reason", turn.escalation_reason)}</span> : null}
        {turn.fallback_reason ? (
          <span className="chip check-fail">
            {t("agent.fallback")}: {turn.fallback_reason}
          </span>
        ) : null}
        {turn.reviewer ? (
          <span className={`chip ${turn.reviewer.status === "ok" ? "check-pass" : "check-fail"}`}>
            {t("agent.reviewer")}: {t(`agent.review.${turn.reviewer.status}` as "agent.review.ok")}
            {turn.reviewer.status !== "ok" && turn.reviewer.detail ? ` (${turn.reviewer.detail})` : ""}
          </span>
        ) : null}
      </p>

      {turn.plan.length > 0 ? (
        <div className="agent-plan">
          <strong>{t("agent.plan")}</strong>
          <ol>
            {turn.plan.map((s, i) => (
              <li key={i} className={`step-${s.status}`} dir="auto">
                <span className="badge">{t(`agent.step.${s.status}` as "agent.step.open")}</span> {s.text}
              </li>
            ))}
          </ol>
          {turn.plan_changes.length > 0 ? (
            <p className="muted small">
              {t("agent.planChanges")}: {turn.plan_changes.join(" → ")}
            </p>
          ) : null}
        </div>
      ) : null}

      <ol className="agent-moves">
        {turn.moves.map((m) => (
          <li key={m.number} className={`agent-move${m.refused ? " refused" : ""}`}>
            <header>
              <strong>
                {m.number}. {labelFor(t, "move", m.kind)}
              </strong>
              {m.tool ? <span className="chip">{m.tool}</span> : null}
              <span className={`badge ${m.refused ? "problem" : "ok"}`}>{m.refused ? t("agent.refused") : t("agent.worked")}</span>
              <span className="muted small">{Math.round(m.duration_ms)} ms</span>
            </header>
            {m.why ? (
              <p className="muted" dir="auto">
                {m.why}
              </p>
            ) : null}
            {m.summary ? (
              <p className="small" dir="auto">
                {m.summary}
              </p>
            ) : null}
            {m.checks.length > 0 ? (
              <p className="checks">
                {m.checks.map((c) => (
                  <span key={c.name} className={`chip ${c.passed ? "check-pass" : "check-fail"}`} title={c.reason || undefined}>
                    {c.passed ? "✓" : "✗"} {c.name}
                    {!c.passed && c.reason ? `: ${c.reason}` : ""}
                  </span>
                ))}
              </p>
            ) : null}
          </li>
        ))}
      </ol>

      {turn.tried.length > 0 ? (
        <div className="agent-tried">
          <strong>{t("agent.tried")}</strong>
          <ul className="plain">
            {turn.tried.map((x, i) => (
              <li key={i} dir="auto">
                {x}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
