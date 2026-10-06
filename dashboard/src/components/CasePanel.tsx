import { useState } from "react";
import { ApiError, apiPost } from "../api/client";
import type { HandoffCase } from "../api/types/DashboardConversation";
import { useApi } from "../api/useApi";
import { labelFor, useI18n } from "../i18n";
import { availableActions } from "../lib/caseActions";
import { Async } from "./Async";

/** One case with the same actions as /inbox (claim, approve or reject a waiting action, release, give back, resolve). */
export function CasePanel(props: { caseId: string; agent: string; onChanged: () => void; onClose: () => void }) {
  const { t } = useI18n();
  const state = useApi<HandoffCase>(`/v1/handoff/cases/${encodeURIComponent(props.caseId)}`);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(path: string, extra: Record<string, unknown> = {}) {
    if (!props.agent.trim()) {
      setProblem(t("esc.needName"));
      return;
    }
    setBusy(true);
    try {
      await apiPost(`/v1/handoff/cases/${encodeURIComponent(props.caseId)}/${path}`, { agent: props.agent.trim(), ...extra });
      setProblem(null);
      setNote("");
      props.onChanged();
      state.reload();
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : String(cause));
      state.reload(); // the case may have changed under us: show what it is now
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="case-panel" aria-label={t("esc.cases")}>
      <div className="case-panel-head">
        <h2>{props.caseId}</h2>
        <button type="button" onClick={props.onClose}>
          {t("detail.closePassage")}
        </button>
      </div>
      <Async state={state}>
        {(c) => {
          const allowed = availableActions(c, props.agent);
          const pending = c.pending_approval;
          return (
            <>
              <p dir="auto">{c.package.summary_source === "ai" && c.package.ai_summary ? c.package.ai_summary : c.package.summary}</p>
              <p className="muted">
                {labelFor(t, "reason", c.package.reason)} · {c.package.priority} · {c.status}
                {c.claimed_by ? ` · ${c.claimed_by}` : ""}
              </p>
              <p>
                <strong>{t("detail.rule")}:</strong> {(c.package.rule_answers ?? []).map((r) => `${r.action} ${r.decision} (${r.reason_code})`).join(", ") || t("common.noData")}
              </p>
              {pending ? (
                <div className="pending">
                  <strong>{pending.capability}</strong>
                  <dl className="facts">
                    {Object.entries(pending.arguments ?? {}).map(([key, value]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd dir="auto">{String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="muted small">{pending.reason}</p>
                </div>
              ) : null}
              <div className="actions">
                {allowed.claim ? (
                  <button type="button" disabled={busy} onClick={() => act("claim")}>
                    {t("esc.claim")}
                  </button>
                ) : null}
                {allowed.decide ? (
                  <>
                    <input type="text" dir="auto" value={note} placeholder={t("esc.note")} aria-label={t("esc.note")} onChange={(e) => setNote(e.target.value)} />
                    <button type="button" className="primary" disabled={busy} onClick={() => act("decision", { approve: true, note: note || null })}>
                      {t("esc.approve")}
                    </button>
                    <button type="button" disabled={busy} onClick={() => act("decision", { approve: false, note: note || null })}>
                      {t("esc.reject")}
                    </button>
                  </>
                ) : null}
                {allowed.release ? (
                  <button type="button" disabled={busy} onClick={() => act("release")}>
                    {t("esc.release")}
                  </button>
                ) : null}
                {allowed.returnToAgent ? (
                  <button type="button" disabled={busy} onClick={() => act("return-to-agent")}>
                    {t("esc.giveBack")}
                  </button>
                ) : null}
                {allowed.resolve ? (
                  <button type="button" disabled={busy} onClick={() => act("resolve")}>
                    {t("esc.resolve")}
                  </button>
                ) : null}
                <a href={`/inbox#${encodeURIComponent(c.case_id)}`}>{t("esc.openInbox")}</a>
              </div>
              {problem ? (
                <p className="state error" role="alert">
                  {problem}
                </p>
              ) : null}
            </>
          );
        }}
      </Async>
    </aside>
  );
}
