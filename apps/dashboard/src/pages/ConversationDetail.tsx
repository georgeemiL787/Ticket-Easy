import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { DashboardConversation } from "../api/types/DashboardConversation";
import { Async } from "../components/Async";
import { PassageDialog } from "../components/PassageDialog";
import { ReplayPanel } from "../components/ReplayPanel";
import { AgentTimeline } from "../components/AgentTimeline";
import { TurnCard } from "../components/TurnCard";
import { labelFor, useI18n } from "../i18n";
import { sharedQuery, useFilters } from "../state/useFilters";

export default function ConversationDetail() {
  const { t } = useI18n();
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const filters = useFilters();
  const [passage, setPassage] = useState<string | null>(null);
  const state = useApi<DashboardConversation>(`/v1/dashboard/conversations/${encodeURIComponent(id)}`, { tenant_id: filters.tenant });
  return (
    <>
      <p>
        <Link to={{ pathname: "/conversations", search: sharedQuery(params) }}>← {t("common.back")}</Link>
      </p>
      <h1>
        {t("detail.title")} <small className="muted">{id}</small>
      </h1>
      <ReplayPanel tenant={filters.tenant} conversationId={id} />
      <Async state={state} isEmpty={(d) => d.transcript.length === 0 && d.traces.length === 0} empty={<p className="muted">{t("detail.noTurns")}</p>}>
        {(d) => (
          <div className="detail">
            <section className="detail-transcript" aria-label={t("detail.transcript")}>
              <h2>{t("detail.transcript")}</h2>
              <ol className="transcript">
                {d.transcript.map((line, index) => (
                  <li key={`${line.trace_id}-${index}`} className={`line ${line.role}`} dir="auto">
                    {line.text}
                  </li>
                ))}
              </ol>
              {d.case ? (
                <p className="case-note">
                  <strong>{t("detail.case")}:</strong> {labelFor(t, "reason", d.case.package.reason)} · {d.case.status} ·{" "}
                  <a href={`/inbox#${encodeURIComponent(d.case.case_id)}`}>{t("esc.openInbox")}</a>
                </p>
              ) : null}
            </section>
            <section className="detail-timeline" aria-label={t("detail.timeline")}>
              <h2>{t("detail.timeline")}</h2>
              {d.traces.map((trace, index) => {
                const agent = (d.timeline ?? []).find((turn) => turn.trace_id === trace.trace_id);
                return (
                  <div key={trace.trace_id}>
                    <TurnCard trace={trace} index={index} onOpenPassage={setPassage} />
                    {agent ? <AgentTimeline turn={agent} /> : null}
                  </div>
                );
              })}
            </section>
          </div>
        )}
      </Async>
      {passage ? <PassageDialog tenant={filters.tenant} citation={passage} onClose={() => setPassage(null)} /> : null}
    </>
  );
}
