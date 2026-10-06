import { Link, useParams, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { DashboardConversation } from "../api/types/DashboardConversation";
import { Async } from "../components/Async";
import { useI18n } from "../i18n";
import { sharedQuery, useFilters } from "../state/useFilters";

export default function ConversationDetail() {
  const { t } = useI18n();
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const filters = useFilters();
  const state = useApi<DashboardConversation>(`/v1/dashboard/conversations/${encodeURIComponent(id)}`, { tenant_id: filters.tenant });
  return (
    <>
      <p>
        <Link to={{ pathname: "/conversations", search: sharedQuery(params) }}>← {t("common.back")}</Link>
      </p>
      <h1>
        {t("detail.title")} <small className="muted">{id}</small>
      </h1>
      <Async state={state} isEmpty={(d) => d.transcript.length === 0} empty={<p className="muted">{t("detail.noTurns")}</p>}>
        {(d) => (
          <ol className="transcript" aria-label={t("detail.transcript")}>
            {d.transcript.map((line, index) => (
              <li key={`${line.trace_id}-${index}`} className={`line ${line.role}`} dir="auto">
                {line.text}
              </li>
            ))}
          </ol>
        )}
      </Async>
    </>
  );
}
