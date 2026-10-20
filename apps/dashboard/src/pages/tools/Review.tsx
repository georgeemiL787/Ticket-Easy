import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import {
  approveBatch,
  createBatch,
  getBatch,
  retryItem,
  SETUP,
  type Batch,
  type BatchItem,
  type Effects,
  type OperationClass,
  type OperationPage,
  type OperationRow,
  type SetupState,
} from "../../api/toolSetup";
import { useApi } from "../../api/useApi";
import { Async, EmptyState, ErrorState, Loading } from "../../components/Async";
import { useI18n } from "../../i18n";
import type { TextKey } from "../../i18n/en";
import { loadChoice } from "../../state/setupStore";
import { sharedQuery, useFilters } from "../../state/useFilters";
import { SetupFrame, usePolling } from "./SetupFrame";

const CLASSES: OperationClass[] = ["read", "write", "risky"];
const EFFECTS: (keyof Effects)[] = ["financial", "irreversible", "external_side_effects", "idempotent"];
const PAGE = 100;

function OperationList({ page, selected, onToggle, unusable }: { page: OperationPage; selected: Set<string>; onToggle: (id: string) => void; unusable: number }) {
  const { t } = useI18n();
  const [tab, setTab] = useState<OperationClass>("read");
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(PAGE);
  const rows = useMemo(
    () => page.operations.filter((r) => r.class === tab && (!query || `${r.method} ${r.path} ${r.title} ${r.summary}`.toLowerCase().includes(query.toLowerCase()))),
    [page, tab, query],
  );
  return (
    <section className="ts-card" aria-label={t("ts.review.title")}>
      <div role="tablist" className="ts-tabs">
        {CLASSES.map((c) => (
          <button key={c} type="button" role="tab" aria-selected={tab === c} className={tab === c ? "ts-tab current" : "ts-tab"} onClick={() => (setTab(c), setShown(PAGE))}>
            {t(`ts.review.class.${c}` as TextKey)} ({page.counts[c]})
          </button>
        ))}
      </div>
      <p className="muted">{t(`ts.review.classHint.${tab}` as TextKey)}</p>
      <input type="search" value={query} placeholder={t("ts.review.search")} aria-label={t("ts.review.search")} onChange={(e) => (setQuery(e.target.value), setShown(PAGE))} />
      {rows.length === 0 ? (
        <p className="muted">{t("ts.review.nothing")}</p>
      ) : (
        <ul className="ts-ops">
          {rows.slice(0, shown).map((row: OperationRow) => (
            <li key={row.id}>
              <label className="ts-inline">
                <input type="checkbox" checked={selected.has(row.id)} disabled={!row.selectable} onChange={() => onToggle(row.id)} />
                <span className="ts-method">{row.method}</span>
                <code>{row.path}</code>
              </label>
              <span className="muted">{row.title}</span>
              {!row.selectable ? <span className="badge problem">{row.reason_unusable ?? t("ts.review.class.restricted")}</span> : null}
            </li>
          ))}
        </ul>
      )}
      {rows.length > shown ? (
        <>
          <p className="muted">{t("ts.review.showing", { shown, total: rows.length })}</p>
          <button type="button" onClick={() => setShown(shown + PAGE)}>
            {t("ts.review.more")}
          </button>
        </>
      ) : null}
      {unusable > 0 ? <p className="muted">{t("ts.review.unusable", { n: unusable })}</p> : null}
    </section>
  );
}

function ItemCard({
  item,
  chosen,
  onChoose,
  confirmed,
  onConfirm,
  effects,
  onEffects,
  onRetry,
}: {
  item: BatchItem;
  chosen: boolean;
  onChoose: (value: boolean) => void;
  confirmed: boolean;
  onConfirm: (value: boolean) => void;
  effects: Effects;
  onEffects: (effects: Effects) => void;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  const waiting = item.state === "needs_owner";
  const writes = item.class !== "read";
  return (
    <article className="ts-item" aria-label={item.summary?.name ?? item.path}>
      <header>
        <strong>{item.summary?.name ?? item.title ?? item.path}</strong>
        <span className={`badge ts-state-${item.state}`}>{t(`ts.state.${item.state}` as TextKey)}</span>
      </header>
      <p className="muted">
        {item.method} <code>{item.path}</code>
      </p>
      {item.summary ? <p>{item.summary.purpose}</p> : null}
      {item.summary?.asks_for.length ? (
        <p>
          <b>{t("ts.review.asksFor")}:</b> {item.summary.asks_for.join(", ")}
        </p>
      ) : null}
      {item.summary?.changes.length ? (
        <p>
          <b>{t("ts.review.changes")}:</b> {item.summary.changes.join(", ")}
        </p>
      ) : item.summary?.reads.length ? (
        <p>
          <b>{t("ts.review.reads")}:</b> {item.summary.reads.join(", ")}
        </p>
      ) : null}
      {item.message ? <p className="ts-bad">{item.message}</p> : null}
      {item.state === "needs_attention" || item.state === "skipped" ? (
        <button type="button" onClick={onRetry} disabled={item.state === "skipped"}>
          {t("ts.review.retry")}
        </button>
      ) : null}
      {waiting ? (
        <>
          <label className="ts-inline">
            <input type="checkbox" checked={chosen} onChange={(e) => onChoose(e.target.checked)} />
            {item.summary?.name ?? item.path}
          </label>
          {writes ? (
            <fieldset className="ts-effects">
              <legend>{t("ts.review.confirmTitle")}</legend>
              {EFFECTS.map((key) => (
                <label className="ts-inline" key={key}>
                  <input type="checkbox" checked={effects[key]} onChange={(e) => onEffects({ ...effects, [key]: e.target.checked })} />
                  {t(`ts.review.effect.${key}` as TextKey)}
                </label>
              ))}
              <label className="ts-inline">
                <input type="checkbox" checked={confirmed} onChange={(e) => onConfirm(e.target.checked)} />
                {t("ts.review.confirmBox")}
              </label>
            </fieldset>
          ) : null}
        </>
      ) : null}
    </article>
  );
}

/** Screen 2: choose operations (grouped read / write / risky), prepare a tool for each, confirm, approve. */
export default function Review() {
  const { t } = useI18n();
  const { tenant } = useFilters();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const choice = useMemo(() => loadChoice(tenant), [tenant]);
  const known = useApi<SetupState>(SETUP, { tenant_id: tenant });
  const specId = choice.specId ?? known.data?.specs[0]?.id ?? null;
  const ops = useApi<OperationPage>(specId ? `${SETUP}/operations` : null, { tenant_id: tenant, spec_id: specId, limit: 500 });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [verify, setVerify] = useState("");
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [effects, setEffects] = useState<Record<string, Effects>>({});
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());

  const batchId = batch?.id ?? params.get("batch") ?? known.data?.batches[0]?.id ?? null;
  useEffect(() => {
    if (batch === null && batchId !== null && params.get("batch")) void getBatch(tenant, batchId).then(setBatch).catch(() => undefined);
  }, [batch, batchId, params, tenant]);

  const refresh = useCallback(() => {
    if (batch) void getBatch(tenant, batch.id).then(setBatch).catch(() => undefined);
  }, [batch, tenant]);
  usePolling(Boolean(batch?.busy), refresh);

  const reads = useMemo(() => (ops.data?.operations ?? []).filter((o) => o.class === "read" && o.selectable), [ops.data]);
  const needsVerify = useMemo(() => [...selected].some((id) => ops.data?.operations.find((o) => o.id === id)?.class !== "read"), [selected, ops.data]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function prepare() {
    if (!specId) return;
    setBusy(true);
    setError(null);
    try {
      const made = await createBatch(tenant, {
        spec_id: specId,
        operation_ids: [...selected],
        verify_operation_id: needsVerify && verify ? verify : undefined,
        access: { callers: choice.callers, own_records_only: choice.ownRecordsOnly },
        connection: { base_url: choice.baseUrl || undefined, fixtures: choice.fixtures?.base_url ? choice.fixtures : undefined, accounts: choice.accounts.filter((a) => a.name) },
      });
      setBatch(made);
      setChosen(new Set());
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  const waiting = batch?.items.filter((i) => i.state === "needs_owner") ?? [];
  const ready = waiting.filter((i) => chosen.has(i.proposal_id ?? "") && (i.class === "read" || confirmed.has(i.proposal_id ?? "")));

  async function approve() {
    if (!batch) return;
    setBusy(true);
    setError(null);
    try {
      const confirmations: Record<string, Effects> = {};
      for (const item of ready) if (item.class !== "read" && item.proposal_id) confirmations[item.proposal_id] = effects[item.proposal_id] ?? item.suggested_effects;
      await approveBatch(tenant, batch.id, ready.map((i) => i.proposal_id!), confirmations);
      navigate({ pathname: "/tools/publish", search: `${sharedQuery(params)}${sharedQuery(params) ? "&" : "?"}batch=${batch.id}` });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SetupFrame title={t("ts.review.title")} intro={t("ts.review.intro")}>
      <Async state={known}>
        {() =>
          !specId ? (
            <EmptyState title={t("ts.review.noSpec")} hint="">
              <Link to={{ pathname: "/tools/connect", search: sharedQuery(params) }}>{t("ts.review.goConnect")}</Link>
            </EmptyState>
          ) : (
            <>
              {ops.loading && !ops.data ? <Loading /> : ops.error ? <ErrorState message={ops.error.message} onRetry={ops.reload} /> : null}
              {ops.data && !batch ? (
                <>
                  <OperationList page={ops.data} selected={selected} onToggle={toggle} unusable={ops.data.unusable} />
                  {needsVerify ? (
                    <label>
                      <span>{t("ts.review.verify")}</span>
                      <select value={verify} onChange={(e) => setVerify(e.target.value)}>
                        <option value="">{t("ts.review.verifyNone")}</option>
                        {reads.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.method} {r.path}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  <p>
                    <span className="muted">{t("ts.review.selected", { n: selected.size })} </span>
                    <button type="button" className="primary" onClick={() => void prepare()} disabled={busy || selected.size === 0} aria-busy={busy}>
                      {busy ? t("ts.review.preparing") : t("ts.review.prepare")}
                    </button>
                  </p>
                </>
              ) : null}
              {batch ? (
                <>
                  {batch.busy ? <p role="status">{t("ts.review.preparing")}</p> : null}
                  {batch.items.map((item) => {
                    const id = item.proposal_id ?? item.operation_id;
                    return (
                      <ItemCard
                        key={item.operation_id}
                        item={item}
                        chosen={chosen.has(id)}
                        onChoose={(on) => setChosen((prev) => (on ? new Set(prev).add(id) : new Set([...prev].filter((x) => x !== id))))}
                        confirmed={confirmed.has(id)}
                        onConfirm={(on) => setConfirmed((prev) => (on ? new Set(prev).add(id) : new Set([...prev].filter((x) => x !== id))))}
                        effects={effects[id] ?? item.suggested_effects}
                        onEffects={(next) => setEffects((prev) => ({ ...prev, [id]: next }))}
                        onRetry={() => void retryItem(tenant, batch.id, item.operation_id).then(setBatch)}
                      />
                    );
                  })}
                  <p>
                    <button type="button" className="primary" disabled={busy || ready.length === 0} aria-busy={busy} onClick={() => void approve()}>
                      {busy ? t("ts.review.approving") : t("ts.review.approve")}
                    </button>
                  </p>
                </>
              ) : null}
              {error ? (
                <p role="alert" className="ts-bad">
                  {error}
                </p>
              ) : null}
            </>
          )
        }
      </Async>
    </SetupFrame>
  );
}
