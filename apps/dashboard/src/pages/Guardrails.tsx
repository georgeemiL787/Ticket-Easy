import { useEffect, useState } from "react";
import { ApiError, apiGet } from "../api/client";
import { decideRule, editRule, GUARDRAILS, KNOWLEDGE, startExtract, type Job, type Rule, type RuleList } from "../api/knowledge";
import { useApi } from "../api/useApi";
import { Async, EmptyState } from "../components/Async";
import { LightPage } from "../components/LightPage";
import { useI18n } from "../i18n";
import type { TextKey } from "../i18n/en";
import { useFilters } from "../state/useFilters";

const effectLabel = (t: (key: TextKey) => string, effect: string) => (`gr.effect.${effect}` as TextKey) in { "gr.effect.allow": 1, "gr.effect.deny": 1, "gr.effect.require_human": 1 } ? t(`gr.effect.${effect}` as TextKey) : effect;

function RuleCard({ rule, tenant, onChanged, onError }: { rule: Rule; tenant: string; onChanged: () => void; onError: (text: string) => void }) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState(rule.user_message.en);
  const [busy, setBusy] = useState(false);

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    try {
      await work();
      setEditing(false);
      onChanged();
    } catch (cause) {
      onError(cause instanceof ApiError ? t("gr.refused", { reason: cause.message }) : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  const status = rule.stale ? "stale" : rule.approval_status;
  return (
    <article className="ts-item" aria-label={rule.rule_id}>
      <header>
        <strong>{rule.action.replaceAll("_", " ")}</strong>
        <span className={`badge ts-state-${rule.stale ? "needs_attention" : rule.approval_status === "approved" ? "published" : rule.approval_status === "rejected" ? "skipped" : "testing"}`}>
          {rule.stale ? t("gr.stale") : t(`gr.status.${rule.approval_status}` as TextKey)}
        </span>
      </header>
      <blockquote>
        <small className="muted">{t("gr.quote")}</small>
        <p>“{rule.source.quote}”</p>
        <small className="muted">
          {t("gr.source")}: <code>{rule.source.citation}</code>
        </small>
      </blockquote>
      <p>{t("gr.then", { effect: effectLabel(t, rule.effect), other: effectLabel(t, rule.else_effect) })}</p>
      <p className="muted">{rule.user_message.en}</p>
      {rule.approval_status === "approved" && rule.approved_by ? <p className="muted">{t("gr.approvedBy", { name: rule.approved_by })}</p> : null}
      {editing ? (
        <div>
          <label>
            <span>{t("gr.message")}</span>
            <textarea value={message} onChange={(e) => setMessage(e.target.value)} />
          </label>
          <button type="button" className="primary" disabled={busy || !message.trim()} onClick={() => void run(() => editRule(tenant, rule.rule_id, { user_message: { ...rule.user_message, en: message } }))}>
            {t("gr.save")}
          </button>
          <button type="button" onClick={() => setEditing(false)}>
            {t("gr.cancel")}
          </button>
        </div>
      ) : (
        <div>
          {rule.approval_status !== "approved" ? (
            <button type="button" className="primary" disabled={busy || status === "rejected"} onClick={() => void run(() => decideRule(tenant, rule.rule_id, "approve"))}>
              {t("gr.approve")}
            </button>
          ) : null}
          {rule.approval_status !== "rejected" ? (
            <button type="button" disabled={busy} onClick={() => void run(() => decideRule(tenant, rule.rule_id, "reject"))}>
              {t("gr.reject")}
            </button>
          ) : null}
          <button type="button" disabled={busy} onClick={() => setEditing(true)}>
            {t("gr.editMessage")}
          </button>
        </div>
      )}
      {rule.history.length ? (
        <details>
          <summary>{t("gr.history")}</summary>
          <ul>
            {rule.history.map((h, i) => (
              <li key={i}>
                {h.at.slice(0, 16).replace("T", " ")} · {h.event}
                {h.by ? ` · ${h.by}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </article>
  );
}

/** The rules taken from the policies: each with its quoted source; the owner approves, edits or rejects. */
export default function Guardrails() {
  const { t } = useI18n();
  const { tenant } = useFilters();
  const list = useApi<RuleList>(GUARDRAILS, { tenant_id: tenant });
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!job) return;
    let stop = false;
    void (async () => {
      for (let i = 0; i < 600 && !stop; i++) {
        const found = await apiGet<Job>(`${KNOWLEDGE}/jobs/${job}`, { tenant_id: tenant });
        if (found.status === "succeeded") {
          setMessage({ ok: true, text: t("gr.suggested", { n: Number(found.result?.added ?? 0) }) });
          break;
        }
        if (found.status === "failed") {
          setMessage({ ok: false, text: t("gr.suggestFailed", { reason: found.error ?? "" }) });
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      setJob(null);
      setBusy(false);
      list.reload();
    })();
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job]);

  async function suggest() {
    setBusy(true);
    setMessage(null);
    try {
      setJob((await startExtract(tenant)).job_id);
    } catch (cause) {
      setBusy(false);
      setMessage({ ok: false, text: cause instanceof ApiError ? cause.message : t("common.error") });
    }
  }

  return (
    <LightPage title={t("gr.title")} intro={t("gr.intro")}>
      <Async state={list} isEmpty={(d) => d.rules.length === 0} empty={<EmptyState title={t("gr.empty")} hint={t("gr.emptyHint")}><Suggest busy={busy} onClick={() => void suggest()} /></EmptyState>}>
        {(data) => (
          <>
            <p>
              <strong>{t("gr.inForce", { n: data.ruleset.count })}</strong> <span className="muted">· {t("gr.version", { v: data.ruleset.ruleset_version })}</span>
            </p>
            <Suggest busy={busy} onClick={() => void suggest()} />
            {message ? (
              <p role={message.ok ? "status" : "alert"} className={message.ok ? "ts-ok" : "ts-bad"}>
                {message.text}
              </p>
            ) : null}
            {data.rules.map((rule) => (
              <RuleCard key={rule.rule_id} rule={rule} tenant={tenant} onChanged={list.reload} onError={(text) => setMessage({ ok: false, text })} />
            ))}
          </>
        )}
      </Async>
    </LightPage>
  );
}

function Suggest({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  const { t } = useI18n();
  return (
    <button type="button" onClick={onClick} disabled={busy} aria-busy={busy}>
      {busy ? t("gr.suggesting") : t("gr.suggest")}
    </button>
  );
}
