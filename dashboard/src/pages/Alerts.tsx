import { ApiError } from "../api/client";
import { useApi } from "../api/useApi";
import { Async, EmptyState, ErrorState, Loading } from "../components/Async";
import { useI18n } from "../i18n";
import { formatDateTime } from "../lib/dates";
import { useFilters } from "../state/useFilters";

interface AlertRow {
  alert_id: string;
  rule: string;
  severity: string;
  opened_at: string;
  resolved_at: string | null;
  acknowledged_by: string | null;
  details?: Record<string, unknown>;
}

/** The alerts come from the alert engine's endpoint. Until that exists (404) the page says so instead of failing. */
export default function Alerts() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const state = useApi<AlertRow[] | { alerts: AlertRow[] }>("/v1/dashboard/alerts", { tenant_id: filters.tenant });
  const rows = (data: AlertRow[] | { alerts: AlertRow[] }): AlertRow[] => (Array.isArray(data) ? data : data.alerts);

  if (state.loading && state.data === null) return <Loading />;
  const missing = state.error instanceof ApiError && (state.error.status === 404 || state.error.status === 501);
  return (
    <>
      <h1>{t("alerts.title")}</h1>
      {missing ? (
        <EmptyState title={t("alerts.unavailable")} hint={t("alerts.unavailableHint")} />
      ) : state.error ? (
        <ErrorState message={state.error.message} onRetry={state.reload} />
      ) : (
        <Async state={state} isEmpty={(d) => rows(d).length === 0} empty={<EmptyState title={t("alerts.none")} hint="" />}>
          {(d) => (
            <table>
              <thead>
                <tr>
                  <th>{t("alerts.rule")}</th>
                  <th>{t("alerts.severity")}</th>
                  <th>{t("alerts.opened")}</th>
                  <th>{t("alerts.status")}</th>
                  <th>{t("alerts.acknowledgedBy")}</th>
                </tr>
              </thead>
              <tbody>
                {[...rows(d)].sort((a, b) => Number(Boolean(a.resolved_at)) - Number(Boolean(b.resolved_at)) || b.opened_at.localeCompare(a.opened_at)).map((a) => (
                  <tr key={a.alert_id}>
                    <td>{a.rule}</td>
                    <td>
                      <span className={`badge sev-${a.severity}`}>{a.severity}</span>
                    </td>
                    <td>{formatDateTime(a.opened_at, lang)}</td>
                    <td>{a.resolved_at ? t("alerts.resolved") : t("alerts.open")}</td>
                    <td>{a.acknowledged_by ?? t("common.noData")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}
    </>
  );
}
