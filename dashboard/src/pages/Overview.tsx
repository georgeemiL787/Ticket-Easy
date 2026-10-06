import { useApi } from "../api/useApi";
import type { DashboardOverview } from "../api/types/DashboardOverview";
import type { DashboardTimeseries } from "../api/types/DashboardTimeseries";
import { Async } from "../components/Async";
import { CategoryBars, ChartCard, RowsTable, StackedBars, TimeLine } from "../components/Charts";
import { KpiTile } from "../components/Kpi";
import { labelFor, useI18n } from "../i18n";
import { hasData, latencyRows, mixTotals, rateRows, stackedReasonRows, valueRows, OTHER } from "../lib/series";
import { useFilters } from "../state/useFilters";

function useSeries(metric: string) {
  const filters = useFilters();
  return useApi<DashboardTimeseries>("/v1/dashboard/timeseries", {
    tenant_id: filters.tenant,
    metric,
    bucket: filters.bucket,
    from: filters.window.from,
    to: filters.window.to,
  });
}

function LineSection(props: { title: string; metric: string; kind: "count" | "rate" | "latency"; unit?: string }) {
  const { lang } = useI18n();
  const { bucket } = useFilters();
  const state = useSeries(props.metric);
  return (
    <Async state={state}>
      {(d) => {
        const rows =
          props.kind === "rate"
            ? rateRows(d.points, bucket, lang)
            : props.kind === "latency"
              ? latencyRows(d.points, bucket, lang)
              : valueRows(d.points, "value", bucket, lang);
        return (
          <ChartCard
            title={props.title}
            table={<RowsTable rows={rows} series={[{ key: "value", label: props.title }]} />}
          >
            {hasData(rows) ? <TimeLine rows={rows} name={props.title} unit={props.unit} /> : <p className="muted">—</p>}
          </ChartCard>
        );
      }}
    </Async>
  );
}

function EscalationsSection({ title }: { title: string }) {
  const { t, lang } = useI18n();
  const { bucket } = useFilters();
  const state = useSeries("escalations");
  return (
    <Async state={state}>
      {(d) => {
        const { rows, series } = stackedReasonRows(d.points, bucket, lang);
        const named = series.map((key) => ({ key, label: key === OTHER ? t("common.other") : labelFor(t, "reason", key) }));
        return (
          <ChartCard title={title} table={<RowsTable rows={rows} series={named} />}>
            {series.length > 0 ? <StackedBars rows={rows} series={named} /> : <p className="muted">{t("esc.noCases")}</p>}
          </ChartCard>
        );
      }}
    </Async>
  );
}

function LanguageSection({ title }: { title: string }) {
  const { t } = useI18n();
  const state = useSeries("language_mix");
  return (
    <Async state={state}>
      {(d) => {
        const data = mixTotals(d.points).map((m) => ({ ...m, label: labelFor(t, "lang.value", m.name) }));
        const rows = data.map((m) => ({ bucket: m.name, label: m.label, value: m.value }));
        return (
          <ChartCard title={title} table={<RowsTable rows={rows} series={[{ key: "value", label: t("common.count") }]} />}>
            {data.length > 0 ? <CategoryBars data={data} /> : <p className="muted">—</p>}
          </ChartCard>
        );
      }}
    </Async>
  );
}

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
          <>
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
            <div className="charts">
              <LineSection title={t("overview.conversationsOverTime")} metric="conversations" kind="count" />
              <LineSection title={t("overview.automationOverTime")} metric="automation_rate" kind="rate" unit="%" />
              <EscalationsSection title={t("overview.escalationsByReason")} />
              <LanguageSection title={t("overview.languageMix")} />
              <LineSection title={t("overview.latencyOverTime")} metric="latency" kind="latency" unit=" ms" />
            </div>
          </>
        )}
      </Async>
    </>
  );
}
