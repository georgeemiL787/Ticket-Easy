import { useApi } from "../api/useApi";
import type { DashboardTools } from "../api/types/DashboardTools";
import { Async } from "../components/Async";
import { formatValue } from "../components/Kpi";
import { useI18n } from "../i18n";
import { useFilters } from "../state/useFilters";

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
          <table>
            <thead>
              <tr>
                <th>{t("act.tool")}</th>
                <th>{t("act.calls")}</th>
                <th>{t("act.failures")}</th>
                <th>{t("act.failureRate")}</th>
              </tr>
            </thead>
            <tbody>
              {d.tools.map((tool) => (
                <tr key={tool.tool}>
                  <td>{tool.tool}</td>
                  <td>{tool.calls}</td>
                  <td>{tool.failures}</td>
                  <td>{formatValue(tool.rate, "percent", lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
    </>
  );
}
