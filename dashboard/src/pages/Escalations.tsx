import { useApi } from "../api/useApi";
import type { DashboardEscalations } from "../api/types/DashboardEscalations";
import { Async } from "../components/Async";
import { labelFor, useI18n } from "../i18n";
import { useFilters } from "../state/useFilters";

export default function Escalations() {
  const { t } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardEscalations>("/v1/dashboard/escalations", {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
    bucket: filters.bucket,
  });
  return (
    <>
      <h1>{t("esc.title")}</h1>
      <Async state={state} isEmpty={(d) => d.conversations === 0 && d.open_cases === 0}>
        {(d) => (
          <>
            <h2>{t("esc.byReason")}</h2>
            <table>
              <thead>
                <tr>
                  <th>{t("esc.reason")}</th>
                  <th>{t("common.count")}</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(d.by_reason_total).map(([reason, count]) => (
                  <tr key={reason}>
                    <td>{labelFor(t, "reason", reason)}</td>
                    <td>{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p>
              {t("esc.open")}: {d.open_cases} · {t("esc.claimed")}: {d.claimed_cases}
            </p>
          </>
        )}
      </Async>
    </>
  );
}
