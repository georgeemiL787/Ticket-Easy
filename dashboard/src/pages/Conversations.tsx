import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { DashboardConversationPage } from "../api/types/DashboardConversationPage";
import { Async } from "../components/Async";
import { labelFor, useI18n } from "../i18n";
import { formatDateTime } from "../lib/dates";
import { sharedQuery, useFilters } from "../state/useFilters";

export default function Conversations() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const [params] = useSearchParams();
  const state = useApi<DashboardConversationPage>("/v1/dashboard/conversations", {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
    limit: 50,
  });
  return (
    <>
      <h1>{t("conv.title")}</h1>
      <Async state={state} isEmpty={(d) => d.items.length === 0}>
        {(page) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("conv.time")}</th>
                  <th>{t("conv.language")}</th>
                  <th>{t("conv.intents")}</th>
                  <th>{t("conv.outcome")}</th>
                  <th>{t("conv.reason")}</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((row) => (
                  <tr key={row.conversation_id}>
                    <td>
                      <Link to={{ pathname: `/conversations/${row.conversation_id}`, search: sharedQuery(params) }}>
                        {formatDateTime(row.last_at, lang)}
                      </Link>
                    </td>
                    <td>{labelFor(t, "lang.value", row.language ?? "unknown")}</td>
                    <td>{row.intents.join(", ") || t("common.noData")}</td>
                    <td>{labelFor(t, "decision", row.outcome)}</td>
                    <td>{row.escalation_reason ? labelFor(t, "reason", row.escalation_reason) : t("common.noData")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Async>
    </>
  );
}
