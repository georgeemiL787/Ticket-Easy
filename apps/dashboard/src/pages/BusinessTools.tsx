import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, apiPost, withQuery } from "../api/client";
import type { DashboardBusinessTools, TeamCConnector, TeamCPromotion, TeamCProposal, TeamCPublication } from "../api/types/DashboardBusinessTools";
import { useAuth } from "../auth";
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

/** SANDBOX, LIVE DEMO or PRODUCTION, exactly as Team C reports the environment. */
function EnvBadge({ environment }: { environment: string | null | undefined }) {
  const { t } = useI18n();
  if (environment === "production") return <span className="badge env-production">{t("bt.env.production")}</span>;
  if (environment === "live_demo") return <span className="badge env-live">{t("bt.env.live_demo")}</span>;
  if (environment === "sandbox") return <span className="badge env-sandbox">{t("bt.env.sandbox")}</span>;
  return null;
}

/** One Team C connector: its environment, host, the steps of its last real check, and a button to check it again. */
function ConnectorCard({ tenant, connector, onDone }: { tenant: string; connector: TeamCConnector; onDone: () => void }) {
  const { t, lang } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    setBusy(true);
    setError(null);
    try {
      await apiPost<DashboardBusinessTools>(withQuery(`${BASE}/connectors/${connector.id}/check`, { tenant_id: tenant }), {});
      onDone();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="connector">
      <div className="actions-row">
        <EnvBadge environment={connector.environment} /> <code>{connector.host}</code>
        <span className="muted small">
          {t("bt.identities")}: {connector.identities}
        </span>
        <button type="button" onClick={() => void check()} disabled={busy}>
          {busy ? t("common.loading") : t("bt.checkConnection")}
        </button>
        {error ? (
          <span role="alert" className="badge problem">
            {error}
          </span>
        ) : null}
      </div>
      {connector.checked_at ? (
        <>
          <p className="muted small">
            {t("bt.checkedAt")} {formatDateTime(connector.checked_at, lang)} ·{" "}
            {connector.ready ? t("bt.checkReady") : t("bt.checkNotReady")}
          </p>
          <ul className="plain checks">
            {(connector.checks ?? []).map((c) => (
              <li key={c.check}>
                <span className={`badge ${c.status === "ok" ? "ok" : c.status === "failed" ? "problem" : "decision-handoff"}`}>
                  {label(t, "bt.checkStatus", c.status)}
                </span>{" "}
                <strong>{label(t, "bt.check", c.check)}</strong> <span className="small">{c.detail}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="muted small">{t("bt.notChecked")}</p>
      )}
    </li>
  );
}

/** Production decisions are an admin's, by name: the service refuses anyone else, so the controls are shown to admins only. */
function useIsAdmin(): boolean {
  const auth = useAuth();
  return Boolean(auth?.me.auth_required && auth.me.user?.role === "admin");
}

/** Promote a tested tool to production: a note, a rollback contact and (for writes) the idempotency acceptance. Team C may refuse; its words are shown. */
function Promote({ tenant, proposal, reload }: { tenant: string; proposal: TeamCProposal; reload: () => void }) {
  const { t } = useI18n();
  const [note, setNote] = useState("");
  const [contact, setContact] = useState("");
  const [accept, setAccept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPost<DashboardBusinessTools>(withQuery(`${BASE}/artifacts/${proposal.artifact_id}/promote`, { tenant_id: tenant }), {
        note,
        rollback_contact: contact,
        accept_no_idempotency: accept,
      });
      reload();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="promote">
      <summary>{t("bt.promote")}</summary>
      <form onSubmit={submit} aria-label={`${t("bt.promote")} ${proposal.name}`}>
        <p className="muted small">{t("bt.promoteHint")}</p>
        <input required minLength={3} maxLength={500} value={note} placeholder={t("bt.promoteNote")} onChange={(e) => setNote(e.target.value)} aria-label={t("bt.promoteNote")} />
        <input required minLength={3} maxLength={200} value={contact} placeholder={t("bt.promoteContact")} onChange={(e) => setContact(e.target.value)} aria-label={t("bt.promoteContact")} />
        <label className="small">
          <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} /> {t("bt.promoteNoIdempotency")}
        </label>
        <button type="submit" disabled={busy}>
          {busy ? t("common.loading") : t("bt.promoteConfirm")}
        </button>
        {error ? (
          <p role="alert" className="notice">
            {error}
          </p>
        ) : null}
      </form>
    </details>
  );
}

/** One promotion: who decided it and why, its checklist with the evidence of each item, and (when active) the rollback. */
function PromotionCard({ tenant, promotion, admin, reload }: { tenant: string; promotion: TeamCPromotion; admin: boolean; reload: () => void }) {
  const { t, lang } = useI18n();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function rollback(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await apiPost<DashboardBusinessTools>(withQuery(`${BASE}/promotions/${promotion.id}/rollback`, { tenant_id: tenant }), { note });
      reload();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="connector">
      <div className="actions-row">
        <span className="badge env-production">{t("bt.env.production")}</span>
        <span className={`badge ${promotion.status === "active" ? "ok" : "problem"}`}>{label(t, "bt.promotion", promotion.status)}</span>
        <span className="muted small">
          {t("bt.promotedBy")} {promotion.actor}
          {promotion.created_at ? ` · ${formatDateTime(promotion.created_at, lang)}` : ""} · {promotion.rollback_contact}
        </span>
      </div>
      <p className="small">{promotion.note}</p>
      {promotion.status === "rolled_back" ? (
        <p className="muted small">
          {t("bt.rolledBackBy")} {promotion.rolled_back_by}: {promotion.rollback_note}
        </p>
      ) : null}
      <ul className="plain checks">
        {(promotion.checklist ?? []).map((c) => (
          <li key={c.name}>
            <span className={`badge ${c.ok ? "ok" : "problem"}`}>{c.ok ? "ok" : "no"}</span> <strong>{c.name.replace(/_/g, " ")}</strong> <span className="muted small">{c.detail}</span>
          </li>
        ))}
      </ul>
      {admin && promotion.status === "active" ? (
        <form className="inline-form" onSubmit={rollback} aria-label={`${t("bt.rollback")} ${promotion.id}`}>
          <input required minLength={3} maxLength={500} value={note} placeholder={t("bt.rollbackNote")} onChange={(e) => setNote(e.target.value)} aria-label={t("bt.rollbackNote")} />
          <button type="submit" disabled={busy}>
            {busy ? t("common.loading") : t("bt.rollback")}
          </button>
          {error ? (
            <span role="alert" className="badge problem">
              {error}
            </span>
          ) : null}
        </form>
      ) : null}
    </li>
  );
}

const READY_BADGE: Record<string, string> = { ready_to_publish: "ok", published: "ok", needs_owner: "problem", testing: "decision-handoff" };

/** The state of a built tool and the button that starts Team C's automatic tests; polls while a run is going. */
function AutoTest({ tenant, proposal, reload }: { tenant: string; proposal: TeamCProposal; reload: () => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const testing = proposal.readiness_state === "testing";

  useEffect(() => {
    if (!testing) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [testing, reload]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await apiPost<DashboardBusinessTools>(withQuery(`${BASE}/artifacts/${proposal.artifact_id}/auto-test`, { tenant_id: tenant }), {});
      reload();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  if (!proposal.artifact_id) return null;
  const state = proposal.readiness_state ?? "not_tested";
  return (
    <div className="actions-row">
      <span className={`badge ${READY_BADGE[state] ?? ""}`}>{label(t, "bt.ready", state)}</span>
      {state === "needs_owner" && proposal.auto_test_question ? <span className="muted small">{proposal.auto_test_question}</span> : null}
      {state !== "published" ? (
        <button type="button" onClick={() => void start()} disabled={busy || testing} title={t("bt.autoTestHint")}>
          {busy || testing ? t("common.loading") : t("bt.autoTest")}
        </button>
      ) : null}
      {error ? (
        <span role="alert" className="badge problem">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export default function BusinessTools() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const isAdmin = useIsAdmin();
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
      <p>
        <Link to={{ pathname: "/tools/connect", search: window.location.search }}>{t("bt.guided")}</Link>
      </p>
      <Async state={state}>
        {(d) => (
          <>
            {d.production_tools ? (
              <p className="notice production" role="note">
                <strong>{t("bt.productionNotice", { n: d.production_tools })}</strong> {t("bt.productionNoticeHint")}
              </p>
            ) : null}
            {d.live_tools ? (
              <p className="notice live" role="note">
                <strong>{t("bt.liveNotice", { n: d.live_tools })}</strong> {t("bt.liveNoticeHint")}
              </p>
            ) : null}
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
                        <span className="chip">{t(tool.source === "team_c" ? "bt.sourceTeamC" : "bt.sourceStandin")}</span>{" "}
                        <EnvBadge environment={tool.source === "team_c" ? tool.environment : null} />
                        {tool.api_host ? <div className="muted small">{t("bt.callsApi", { host: tool.api_host })}</div> : null}
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
                            <td>{p.published_at ? formatDateTime(p.published_at, lang) : t("common.noData")}</td>
                            <td>{p.status === "published" ? <DisableForm tenant={filters.tenant} publication={p} onDone={state.reload} /> : p.disabled_at ? `${t("bt.disabledAt")} ${formatDateTime(p.disabled_at, lang)}` : ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <p className="muted small">{t("bt.disableHint")}</p>

                <h2>{t("bt.connectors")}</h2>
                {(d.connectors ?? []).length === 0 ? (
                  <p className="muted">{t("bt.noConnectors")}</p>
                ) : (
                  <ul className="plain connectors">
                    {(d.connectors ?? []).map((c) => (
                      <ConnectorCard key={c.id} tenant={filters.tenant} connector={c} onDone={state.reload} />
                    ))}
                  </ul>
                )}
                <p className="muted small">{t("bt.checkHint")}</p>

                <h2>{t("bt.promotions")}</h2>
                {(d.promotions ?? []).length === 0 ? (
                  <p className="muted">{t("bt.noPromotions")}</p>
                ) : (
                  <ul className="plain connectors">
                    {(d.promotions ?? []).map((p) => (
                      <PromotionCard key={p.id} tenant={filters.tenant} promotion={p} admin={isAdmin} reload={state.reload} />
                    ))}
                  </ul>
                )}

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
                        <AutoTest tenant={filters.tenant} proposal={p} reload={state.reload} />
                        {isAdmin && p.promotable ? <Promote tenant={filters.tenant} proposal={p} reload={state.reload} /> : null}
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
