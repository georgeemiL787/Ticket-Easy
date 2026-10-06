import { useState } from "react";
import { ApiError, apiPost } from "../api/client";
import type { DashboardEscalations } from "../api/types/DashboardEscalations";
import type { DashboardQueue } from "../api/types/DashboardQueue";
import type { DashboardTimeseries } from "../api/types/DashboardTimeseries";
import { useApi } from "../api/useApi";
import { Async, ErrorState } from "../components/Async";
import { CasePanel } from "../components/CasePanel";
import { ChartCard, RowsTable, StackedBars } from "../components/Charts";
import { labelFor, useI18n } from "../i18n";
import { formatRemaining, rememberName, rememberedName } from "../lib/caseActions";
import { OTHER, stackedReasonRows } from "../lib/series";
import { useFilters } from "../state/useFilters";

const NAME_KEY = "dashboard.name";

function ReasonsOverTime() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardTimeseries>("/v1/dashboard/timeseries", {
    tenant_id: filters.tenant, metric: "escalations", bucket: filters.bucket, from: filters.window.from, to: filters.window.to,
  }); // prettier-ignore
  return (
    <Async state={state}>
      {(d) => {
        const { rows, series } = stackedReasonRows(d.points, filters.bucket, lang);
        const named = series.map((key) => ({ key, label: key === OTHER ? t("common.other") : labelFor(t, "reason", key) }));
        return (
          <ChartCard title={t("esc.overTime")} table={<RowsTable rows={rows} series={named} />}>
            {series.length > 0 ? <StackedBars rows={rows} series={named} /> : <p className="muted">{t("common.noData")}</p>}
          </ChartCard>
        );
      }}
    </Async>
  );
}

export default function Escalations() {
  const { t } = useI18n();
  const filters = useFilters();
  const [name, setName] = useState(() => rememberedName(NAME_KEY));
  const [selected, setSelected] = useState<string | null>(null);
  const [assignee, setAssignee] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const queue = useApi<DashboardQueue>("/v1/dashboard/queue", { tenant_id: filters.tenant });
  const summary = useApi<DashboardEscalations>("/v1/dashboard/escalations", {
    tenant_id: filters.tenant, from: filters.window.from, to: filters.window.to, bucket: filters.bucket,
  }); // prettier-ignore

  async function reassign(caseId: string) {
    const to = (assignee[caseId] ?? "").trim();
    if (!name.trim() || !to) {
      setProblem(t("esc.needName"));
      return;
    }
    try {
      await apiPost(`/v1/handoff/cases/${encodeURIComponent(caseId)}/assign`, { agent: name.trim(), assignee: to });
      setProblem(null);
      queue.reload();
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : String(cause));
    }
  }

  return (
    <>
      <h1>{t("esc.title")}</h1>
      <label className="name-field">
        <span>{t("esc.manager")}</span>
        <input
          type="text"
          maxLength={80}
          value={name}
          dir="auto"
          onChange={(e) => {
            setName(e.target.value);
            rememberName(NAME_KEY, e.target.value.trim());
          }}
        />
      </label>

      <h2>{t("esc.queue")}</h2>
      {problem ? <ErrorState message={problem} /> : null}
      <Async state={queue} isEmpty={(d) => d.rows.length === 0} empty={<p className="muted">{t("esc.noCases")}</p>}>
        {(d) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("esc.priority")}</th>
                  <th>{t("esc.reason")}</th>
                  <th>{t("esc.age")}</th>
                  <th>{t("esc.sla")}</th>
                  <th>{t("esc.assignedTo")}</th>
                  <th>{t("esc.reassign")}</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((row) => {
                  const left = formatRemaining(row.remaining_seconds);
                  return (
                    <tr key={row.case_id} className={row.overdue ? "overdue" : undefined} aria-selected={selected === row.case_id}>
                      <td>
                        <span className={`badge prio-${row.priority}`}>{row.priority}</span>
                      </td>
                      <td>
                        <button type="button" className="link" onClick={() => setSelected(row.case_id)}>
                          {labelFor(t, "reason", row.reason)}
                        </button>
                        {row.has_pending_approval ? <span className="chip">{t("reason.approval_required")}</span> : null}
                      </td>
                      <td>{formatRemaining(row.age_seconds).text}</td>
                      <td className={left.overdue ? "late" : undefined}>{left.overdue ? `${t("esc.overdue")}: ${left.text}` : left.text}</td>
                      <td>{row.claimed_by ?? t("common.noData")}</td>
                      <td>
                        <span className="inline-form">
                          <input
                            type="text"
                            maxLength={80}
                            dir="auto"
                            aria-label={`${t("esc.assignTo")} ${row.case_id}`}
                            placeholder={t("esc.assignTo")}
                            value={assignee[row.case_id] ?? ""}
                            onChange={(e) => setAssignee((all) => ({ ...all, [row.case_id]: e.target.value }))}
                          />
                          <button type="button" onClick={() => reassign(row.case_id)}>
                            {t("esc.reassign")}
                          </button>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Async>
      {selected ? (
        <CasePanel
          caseId={selected}
          agent={name}
          onChanged={queue.reload}
          onClose={() => setSelected(null)}
        />
      ) : null}

      <Async state={summary} isEmpty={(d) => d.conversations === 0 && Object.keys(d.by_reason_total).length === 0}>
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
            <ReasonsOverTime />
            {d.top_rules.length > 0 ? (
              <>
                <h2>{t("esc.topRules")}</h2>
                <table>
                  <thead>
                    <tr>
                      <th>{t("esc.rule")}</th>
                      <th>{t("esc.decision")}</th>
                      <th>{t("common.count")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.top_rules.map((r) => (
                      <tr key={`${r.rule}-${r.decision}`}>
                        <td>{r.rule}</td>
                        <td>{r.decision}</td>
                        <td>{r.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : null}
          </>
        )}
      </Async>
    </>
  );
}
