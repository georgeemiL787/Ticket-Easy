import { useApi } from "../api/useApi";
import type { DashboardOverview } from "../api/types/DashboardOverview";
import { Async } from "../components/Async";
import { KpiTile } from "../components/Kpi";
import { useI18n } from "../i18n";
import { useFilters } from "../state/useFilters";

export default function Overview() {
  const { t } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardOverview>("/v1/dashboard/overview", {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
  });
  return (
    <>
      <h1>{t("overview.title")}</h1>
      <Async state={state} isEmpty={(d) => d.conversations.value === 0 && d.conversations.previous === 0}>
        {(d) => (
          <div className="kpis">
            <KpiTile label={t("overview.conversations")} kpi={d.conversations} format="count" />
            <KpiTile label={t("overview.automation")} kpi={d.automation_rate} format="percent" hint={t("overview.automatedExplain")} />
            <KpiTile label={t("overview.escalation")} kpi={d.escalation_rate} format="percent" higherIsBetter={false} />
            <KpiTile label={t("overview.unverified")} kpi={d.unverified_results} format="count" higherIsBetter={false} />
            <KpiTile label={t("overview.p95")} kpi={d.p95_latency_ms} format="ms" higherIsBetter={false} />
            <section className="kpi" aria-label={t("overview.openCases")}>
              <h3>{t("overview.openCases")}</h3>
              <p className="kpi-value">{d.open_cases}</p>
            </section>
          </div>
        )}
      </Async>
    </>
  );
}
