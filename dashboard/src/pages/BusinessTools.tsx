import { useState, type FormEvent } from "react";
import { ApiError, apiPost, withQuery } from "../api/client";
import type { DashboardBusinessTools, TeamCPublication } from "../api/types/DashboardBusinessTools";
import { useApi } from "../api/useApi";
import { Async } from "../components/Async";
import { useI18n } from "../i18n";
import en, { type TextKey } from "../i18n/en";
import { formatDateTime } from "../lib/dates";
import { useFilters } from "../state/useFilters";

const BASE = "/v1/dashboard/business-tools";
const STATUS_BADGE: Record<string, string> = { available: "ok", missing: "problem", blocked: "problem" };
const OPEN_STATES = new Set(["needs_clarification", "needs_reconciliation", "ready_for_review", "changes_requested"]);

/** Turn a code from the service into a translated label, falling back to the code itself. */
function label(t: (key: TextKey) => string, prefix: string, code: string | null | undefined): string {
  if (!code) return "";
  const key = `${prefix}.${code}`;
  return key in en ? t(key as TextKey) : code;
}

/** One publication's disable control: a required note, then the real call; the page reloads from the service after. */
function DisableForm({ tenant, publication, onDone }: { tenant: string; publication: TeamCPublication; onDone: () => void }) {
  const { t } = useI18n();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPost<DashboardBusinessTools>(withQuery(`${BASE}/publications/${publication.id}/disable`, { tenant_id: tenant }), { note });
      onDone();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={submit} aria-label={`${t("bt.disable")} ${publication.tool_name}`}>
      <input required minLength={3} maxLength={500} value={note} placeholder={t("bt.disableNote")} onChange={(e) => setNote(e.target.value)} aria-label={t("bt.disableNote")} />
      <button type="submit" disabled={busy}>
        {busy ? t("common.loading") : t("bt.disable")}
      </button>
      {error ? (
        <span role="alert" className="badge problem">
          {error}
        </span>
      ) : null}
    </form>
  );
}

export default function BusinessTools() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardBusinessTools>(BASE, { tenant_id: filters.tenant });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  async function refresh() {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await apiPost<DashboardBusinessTools>(withQuery(`${BASE}/refresh`, { tenant_id: filters.tenant }), {});
      state.reload(); // show what the service now holds, never what the button assumed
    } catch (cause) {
      setRefreshError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <>
      <h1>{t("bt.title")}</h1>
      <p className="notice" role="note">
        <strong>{t("bt.testOnly")}</strong> {t("bt.testOnlyHint")}
      </p>
      <Async state={state}>
        {(d) => (
          <>
            <section className="chart-card" aria-labelledby="bt-connection">
              <h2 id="bt-connection">{t("bt.connection")}</h2>
              {d.configured ? (
                <dl className="facts">
                  <dt>{t("bt.business")}</dt>
                  <dd>
                    <code>{d.business_id}</code>
                  </dd>
                  <dt>{t("bt.listedAt")}</dt>
                  <dd>{d.listed_at ? formatDateTime(d.listed_at, lang) : t("common.noData")}</dd>
                  <dt>{t("bt.mapped")}</dt>
                  <dd>{d.mapped_customers}</dd>
                  {d.team_c_error ? (
                    <>
                      <dt>{t("bt.problem")}</dt>
                      <dd className="badge problem">{d.team_c_error}</dd>
                    </>
                  ) : null}
                </dl>
              ) : (
                <p className="muted">{t("bt.notConfigured")}</p>
              )}
              <div className="actions-row">
                <button type="button" onClick={() => void refresh()} disabled={refreshing}>
                  {refreshing ? t("common.loading") : t("bt.refresh")}
                </button>
                {d.builder_url ? (
                  <a className="button-link" href={d.builder_url} target="_blank" rel="noopener noreferrer">
                    {t("bt.openBuilder")}
                  </a>
                ) : null}
                {refreshError ? (
                  <span role="alert" className="badge problem">
                    {refreshError}
                  </span>
                ) : null}
              </div>
              <p className="muted small">{t("bt.refreshHint")}</p>
            </section>

            <h2>{t("bt.agentTools")}</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("bt.tool")}</th>
                    <th>{t("bt.usedBy")}</th>
                    <th>{t("bt.source")}</th>
                    <th>{t("bt.status")}</th>
                    <th>{t("bt.detail")}</th>
                    <th>{t("bt.teamCTool")}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.tools.map((tool) => (
                    <tr key={tool.name}>
                      <td>
                        <code>{tool.name}</code>
                      </td>
                      <td>{tool.used_by.join(", ") || t("common.noData")}</td>
                      <td>
                        <span className="chip">{t(tool.source === "team_c" ? "bt.sourceTeamC" : "bt.sourceStandin")}</span>
                      </td>
                      <td>
                        <span className={`badge ${STATUS_BADGE[tool.status] ?? ""}`}>{label(t, "bt.status", tool.status)}</span>
                      </td>
                      <td>{tool.reason ? `${label(t, "bt.reason", tool.reason)}${tool.detail ? ` (${tool.detail})` : ""}` : tool.operation_kind ?? ""}</td>
                      <td>
                        {tool.team_c_tool ? (
                          <>
                            <code>{tool.team_c_tool}</code>
                            {tool.proposal_version ? <span className="muted"> v{tool.proposal_version}</span> : null}
                          </>
                        ) : (
                          t("common.noData")
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {d.configured ? (
              <>
                <h2>{t("bt.publications")}</h2>
                {d.publications_error ? <p className="badge problem">{d.publications_error}</p> : null}
                {(d.publications ?? []).length === 0 && !d.publications_error ? <p className="muted">{t("bt.noPublications")}</p> : null}
                {(d.publications ?? []).length > 0 ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>{t("bt.teamCTool")}</th>
                          <th>{t("bt.boundTo")}</th>
                          <th>{t("bt.status")}</th>
                          <th>{t("bt.environment")}</th>
                          <th>{t("bt.publishedAt")}</th>
                          <th>{t("bt.actions")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(d.publications ?? []).map((p) => (
                          <tr key={p.id}>
                            <td>
                              <code>{p.tool_name}</code> <span className="muted">v{p.version}</span>
                            </td>
                            <td>{p.bound_to ? <code>{p.bound_to}</code> : t("common.noData")}</td>
                            <td>
                              <span className={`badge ${p.effective_status === "published" ? "ok" : "problem"}`}>{label(t, "bt.pub", p.effective_status)}</span>
                              {p.problem ? <div className="muted small">{p.problem}</div> : null}
                            </td>
                            <td>
                              {p.environment} · {p.production_ready ? t("bt.productionReady") : t("bt.notProduction")}
                            </td>
                            <td>{p.published_at ? formatDateTime(p.published_at, lang) : t("common.noData")}</td>
                            <td>{p.status === "published" ? <DisableForm tenant={filters.tenant} publication={p} onDone={state.reload} /> : p.disabled_at ? `${t("bt.disabledAt")} ${formatDateTime(p.disabled_at, lang)}` : ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <p className="muted small">{t("bt.disableHint")}</p>

                <h2>{t("bt.proposals")}</h2>
                {(d.proposals ?? []).length === 0 ? (
                  <p className="muted">{t("bt.noProposals")}</p>
                ) : (
                  <ul className="plain">
                    {(d.proposals ?? []).map((p) => (
                      <li key={p.id}>
                        {d.builder_base ? (
                          <a href={`${d.builder_base}/proposals/${p.id}`} target="_blank" rel="noopener noreferrer">
                            {p.name || p.id}
                          </a>
                        ) : (
                          p.name || p.id
                        )}{" "}
                        <span className="muted">v{p.version}</span>{" "}
                        <span className={`badge ${OPEN_STATES.has(p.state) ? "decision-handoff" : p.state === "approved_to_build" ? "ok" : ""}`}>{label(t, "bt.state", p.state)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : null}
          </>
        )}
      </Async>
    </>
  );
}
