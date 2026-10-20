import { useApi } from "../api/useApi";
import type { DashboardTools } from "../api/types/DashboardTools";
import { Async } from "../components/Async";
import { formatValue } from "../components/Kpi";
import { useI18n } from "../i18n";
import { useFilters } from "../state/useFilters";

const TOP_ERRORS = 3;

export default function Actions() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardTools>("/v1/dashboard/tools", {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
  });
  return (
    <>
      <h1>{t("act.title")}</h1>
      <Async state={state} isEmpty={(d) => d.tools.length === 0}>
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("act.tool")}</th>
                  <th>{t("act.calls")}</th>
                  <th>{t("act.failures")}</th>
                  <th>{t("act.failureRate")}</th>
                  <th>{t("act.topErrors")}</th>
                  <th>{t("act.p50")}</th>
                  <th>{t("act.p95")}</th>
                </tr>
              </thead>
              <tbody>
                {d.tools.map((tool) => {
                  const errors = Object.entries(tool.errors).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, TOP_ERRORS);
                  return (
                    <tr key={tool.tool} className={tool.rate >= 0.5 && tool.failures > 0 ? "overdue" : undefined}>
                      <td>{tool.tool}</td>
                      <td>{tool.calls}</td>
                      <td>{tool.failures}</td>
                      <td>{formatValue(tool.rate, "percent", lang)}</td>
                      <td>{errors.length ? errors.map(([code, n]) => `${code} ×${n}`).join(", ") : t("common.noData")}</td>
                      <td>{tool.p50_ms === null ? t("common.noData") : Math.round(tool.p50_ms)}</td>
                      <td>{tool.p95_ms === null ? t("common.noData") : Math.round(tool.p95_ms)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Async>
    </>
  );
}
