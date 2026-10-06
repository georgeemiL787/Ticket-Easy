import { useState } from "react";
import type { DecisionTrace } from "../api/types/DashboardConversation";
import { labelFor, useI18n } from "../i18n";
import { SERIES_COLOURS } from "./Charts";

/** The stages that ran, as a bar whose segments are as wide as the time each took (and a list with the numbers). */
export function TimingBar({ steps }: { steps: DecisionTrace["steps"] }) {
  const ran = (steps ?? [])
    .map((s) => ({ stage: s.stage, status: s.status, duration_ms: s.duration_ms ?? 0 }))
    .filter((s) => s.status !== "skipped" && s.duration_ms >= 0.05); // stages under 0.05 ms are not worth a segment
  const total = ran.reduce((sum, s) => sum + s.duration_ms, 0);
  if (ran.length === 0 || total === 0) return null;
  return (
    <div className="timing">
      <div className="timing-bar" role="img" aria-label={ran.map((s) => `${s.stage} ${s.duration_ms.toFixed(1)} ms`).join(", ")}>
        {ran.map((s, i) => (
          <span
            key={s.stage}
            title={`${s.stage}: ${s.duration_ms.toFixed(1)} ms`}
            style={{ inlineSize: `${(s.duration_ms / total) * 100}%`, background: SERIES_COLOURS[i % SERIES_COLOURS.length] }}
          />
        ))}
      </div>
      <ul className="timing-list">
        {ran.map((s, i) => (
          <li key={s.stage}>
            <span className="swatch" style={{ background: SERIES_COLOURS[i % SERIES_COLOURS.length] }} aria-hidden="true" />
            {s.stage} <span className="muted">{s.duration_ms.toFixed(1)} ms</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Citation({ citation, onOpen }: { citation: string; onOpen: (citation: string) => void }) {
  return (
    <button type="button" className="link" onClick={() => onOpen(citation)}>
      {citation}
    </button>
  );
}

/** One customer message and everything that decided the reply: reading, identity, evidence, rules, tools, decision, timing. */
export function TurnCard({ trace, index, onOpenPassage }: { trace: DecisionTrace; index: number; onOpenPassage: (citation: string) => void }) {
  const { t } = useI18n();
  const [json, setJson] = useState(false);
  const identity = trace.identity;
  const entities = Object.entries(trace.entities ?? {});
  return (
    <article className="turn" aria-label={`${t("detail.turn")} ${index + 1}`}>
      <header>
        <h3>
          {t("detail.turn")} {index + 1}
        </h3>
        <span className={`badge decision-${trace.decision}`}>{labelFor(t, "decision", trace.decision)}</span>
        <span className="muted small">{Math.round(trace.latency_ms ?? 0)} ms</span>
      </header>
      <p className="said" dir="auto">
        {trace.customer_message || t("common.noData")}
      </p>

      <section aria-label={t("detail.intents")}>
        <h4>{t("detail.intents")}</h4>
        <p>
          {(trace.intents ?? []).length === 0
            ? t("common.noData")
            : (trace.intents ?? []).map((i) => (
                <span key={i.name} className="chip">
                  {i.name} <span className="muted">{Math.round(i.confidence * 100)}%</span>
                </span>
              ))}
          {trace.language ? <span className="chip">{labelFor(t, "lang.value", trace.language)}</span> : null}
          {trace.nlu_method ? <span className="chip muted">{trace.nlu_method}</span> : null}
        </p>
        {entities.length > 0 ? (
          <dl className="facts">
            {entities.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd dir="auto">{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </section>

      <section aria-label={t("detail.identity")}>
        <h4>{t("detail.identity")}</h4>
        <p>
          {identity?.verified ? `${t("detail.verified")} (${identity.customer_id ?? ""}${identity.method ? `, ${identity.method}` : ""})` : t("detail.notVerified")}
        </p>
      </section>

      <section aria-label={t("detail.evidence")}>
        <h4>{t("detail.evidence")}</h4>
        {(trace.evidence ?? []).length === 0 ? (
          <p className="muted">
            {t("detail.noEvidence")}
            {trace.evidence_empty_reason ? ` (${trace.evidence_empty_reason})` : ""}
          </p>
        ) : (
          <ul className="plain">
            {(trace.evidence ?? []).map((e) => (
              <li key={e.citation}>
                <Citation citation={e.citation} onOpen={onOpenPassage} /> <span className="muted small">score {e.score.toFixed(1)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {(trace.policy ?? []).length > 0 ? (
        <section aria-label={t("detail.policy")}>
          <h4>{t("detail.policy")}</h4>
          <ul className="plain">
            {(trace.policy ?? []).map((p) => (
              <li key={p.request_id}>
                <span className={`badge rule-${p.decision}`}>{p.decision}</span> {p.action} · {t("detail.rule")} <strong>{p.reason_code}</strong>
                {(p.citations ?? []).length > 0 ? (
                  <>
                    {" "}
                    · {t("detail.citations")}:{" "}
                    {(p.citations ?? []).map((c) => (
                      <Citation key={c} citation={c} onOpen={onOpenPassage} />
                    ))}
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {(trace.tool_calls ?? []).length > 0 ? (
        <section aria-label={t("detail.tools")}>
          <h4>{t("detail.tools")}</h4>
          <ul className="plain">
            {(trace.tool_calls ?? []).map((c) => (
              <li key={c.request_id}>
                <span className={`badge tool-${c.status}`}>{c.status}</span> {c.tool} <span className="muted">({c.operation_kind})</span>
                {c.error_code ? <strong> {c.error_code}</strong> : null}
                {c.audit_id ? <span className="muted"> · ref {c.audit_id}</span> : null}
                <span className="muted small"> · {Math.round(c.latency_ms ?? 0)} ms</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-label={t("detail.decision")}>
        <h4>{t("detail.decision")}</h4>
        <p>
          <strong>{labelFor(t, "decision", trace.decision)}</strong>
          {trace.escalation_reason ? (
            <>
              {" · "}
              <span>{labelFor(t, "reason", trace.escalation_reason)}</span>
            </>
          ) : null}
        </p>
        {trace.decision_reason ? <p className="muted">{trace.decision_reason}</p> : null}
        {trace.response_text ? (
          <blockquote dir="auto">{trace.response_text}</blockquote>
        ) : null}
        {(trace.response_citations ?? []).length > 0 ? (
          <p className="small">
            {t("detail.citations")}: {(trace.response_citations ?? []).map((c) => <Citation key={c} citation={c} onOpen={onOpenPassage} />)}
          </p>
        ) : null}
      </section>

      {(trace.errors ?? []).length > 0 ? (
        <section aria-label={t("detail.errors")} className="errors">
          <h4>{t("detail.errors")}</h4>
          <ul className="plain">
            {(trace.errors ?? []).map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-label={t("detail.timing")}>
        <h4>{t("detail.timing")}</h4>
        <TimingBar steps={trace.steps} />
      </section>

      <button type="button" className="link" aria-expanded={json} onClick={() => setJson((v) => !v)}>
        {json ? t("common.hideJson") : t("common.json")}
      </button>
      {json ? <pre className="raw">{JSON.stringify(trace, null, 2)}</pre> : null}
    </article>
  );
}
