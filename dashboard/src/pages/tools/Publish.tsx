import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { activateBatch, getBatch, publishBatch, retryItem, SETUP, type Batch, type SetupState } from "../../api/toolSetup";
import { useApi } from "../../api/useApi";
import { Async, EmptyState } from "../../components/Async";
import { useI18n } from "../../i18n";
import type { TextKey } from "../../i18n/en";
import { sharedQuery, useFilters } from "../../state/useFilters";
import { SetupFrame, usePolling } from "./SetupFrame";

const WORKING = new Set(["queued", "preparing", "approved", "reviewing", "building", "testing"]);

/** Screen 3: each approved tool is built and tested; publish the ones that pass, then switch them on. */
export default function Publish() {
  const { t } = useI18n();
  const { tenant } = useFilters();
  const [params] = useSearchParams();
  const known = useApi<SetupState>(SETUP, { tenant_id: tenant });
  const [batch, setBatch] = useState<Batch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ version: number; tools: string[] } | null>(null);
  const [active, setActive] = useState<string[] | null>(null);

  const wanted = params.get("batch") ?? known.data?.batches[0]?.id ?? null;
  useEffect(() => {
    if (wanted && !batch) void getBatch(tenant, wanted).then(setBatch).catch(() => undefined);
  }, [wanted, batch, tenant]);

  const refresh = useCallback(() => {
    if (batch) void getBatch(tenant, batch.id).then(setBatch).catch(() => undefined);
  }, [batch, tenant]);
  const working = Boolean(batch?.items.some((i) => WORKING.has(i.state)));
  usePolling(working, refresh);

  const ready = batch?.items.filter((i) => i.state === "ready_to_publish").length ?? 0;
  const published = batch?.items.filter((i) => i.state === "published").length ?? 0;

  async function publish() {
    if (!batch) return;
    setBusy(true);
    setError(null);
    try {
      const done = await publishBatch(tenant, batch.id);
      setBatch(done.batch);
      setDraft({ version: done.bundle_version, tools: done.tools });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  async function activate() {
    if (!batch || !draft) return;
    setBusy(true);
    setError(null);
    try {
      const done = await activateBatch(tenant, batch.id, draft.version);
      setActive(done.tools);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SetupFrame title={t("ts.publish.title")} intro={t("ts.publish.intro")}>
      <p className="muted">{t("ts.publish.sandbox")}</p>
      <Async state={known}>
        {() =>
          !batch ? (
            <EmptyState title={t("ts.publish.noBatch")} hint="">
              <Link to={{ pathname: "/tools/review", search: sharedQuery(params) }}>{t("ts.publish.goReview")}</Link>
            </EmptyState>
          ) : (
            <>
              {working ? (
                <p role="status" className="muted">
                  {t("ts.publish.busy")}
                </p>
              ) : null}
              <ul className="ts-results">
                {batch.items.map((item) => (
                  <li key={item.operation_id} className="ts-item">
                    <header>
                      <strong>{item.summary?.name ?? item.title ?? item.path}</strong>
                      <span className={`badge ts-state-${item.state}`}>{t(`ts.state.${item.state}` as TextKey)}</span>
                    </header>
                    {item.message ? <p className="ts-bad">{item.message}</p> : null}
                    {item.needs_owner.length ? (
                      <>
                        <p>{t("ts.publish.needsYou")}</p>
                        <ul>
                          {item.needs_owner.map((ask) => (
                            <li key={ask}>{ask}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {item.state === "needs_attention" ? (
                      <button type="button" onClick={() => void retryItem(tenant, batch.id, item.operation_id).then(setBatch)}>
                        {t("ts.review.retry")}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
              <p>
                <button type="button" className="primary" onClick={() => void publish()} disabled={busy || ready === 0} aria-busy={busy}>
                  {busy && !draft ? t("ts.publish.publishing") : t("ts.publish.publish")}
                </button>
              </p>
              {draft && !active ? (
                <>
                  <p role="status" className="ts-ok">
                    {t("ts.publish.saved", { v: draft.version })}
                  </p>
                  <button type="button" className="primary" onClick={() => void activate()} disabled={busy} aria-busy={busy}>
                    {busy ? t("ts.publish.activating") : t("ts.publish.activate")}
                  </button>
                </>
              ) : null}
              {active ? (
                <p role="status" className="ts-ok">
                  {t("ts.publish.done", { tools: active.join(", ") })}
                </p>
              ) : null}
              {published > 0 && !draft ? <p className="muted">{published}</p> : null}
              {error ? (
                <p role="alert" className="ts-bad">
                  {error}
                </p>
              ) : null}
              <p>
                <Link to={{ pathname: "/tools", search: sharedQuery(params) }}>{t("ts.publish.advanced")}</Link>
              </p>
            </>
          )
        }
      </Async>
    </SetupFrame>
  );
}
