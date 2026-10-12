import { useCallback, useEffect, useState } from "react";
import { ApiError, apiDelete, apiGet, withQuery } from "../api/client";
import { KNOWLEDGE, startReindex, uploadDocument, type DocumentList, type Job } from "../api/knowledge";
import { useApi } from "../api/useApi";
import { Async, EmptyState } from "../components/Async";
import { LightPage } from "../components/LightPage";
import { useI18n } from "../i18n";
import type { TextKey } from "../i18n/en";
import { useFilters } from "../state/useFilters";

/** The documents of a business: upload, see which can be searched, remove, and update the search. */
export default function Knowledge() {
  const { t } = useI18n();
  const { tenant } = useFilters();
  const list = useApi<DocumentList>(`${KNOWLEDGE}/documents`, { tenant_id: tenant });
  const [busy, setBusy] = useState<"upload" | "update" | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [job, setJob] = useState<string | null>(null);

  const watch = useCallback(
    async (id: string) => {
      for (let i = 0; i < 600; i++) {
        const found = await apiGet<Job>(`${KNOWLEDGE}/jobs/${id}`, { tenant_id: tenant });
        if (found.status === "succeeded") {
          setMessage({ ok: true, text: t("kn.updated", { n: Number(found.result?.current_passages ?? 0) }) });
          return;
        }
        if (found.status === "failed") {
          setMessage({ ok: false, text: t("kn.updateFailed", { reason: found.error ?? "" }) });
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    },
    [tenant, t],
  );

  useEffect(() => {
    if (!job) return;
    void watch(job).finally(() => {
      setJob(null);
      setBusy(null);
      list.reload();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy("upload");
    setMessage(null);
    try {
      for (const file of Array.from(files)) await uploadDocument(tenant, file);
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof ApiError ? cause.message : t("common.error") });
    } finally {
      setBusy(null);
      list.reload();
    }
  }

  async function update() {
    setBusy("update");
    setMessage(null);
    try {
      setJob((await startReindex(tenant)).job_id);
    } catch (cause) {
      setBusy(null);
      setMessage({ ok: false, text: cause instanceof ApiError ? cause.message : t("common.error") });
    }
  }

  async function remove(documentId: string) {
    setMessage(null);
    try {
      await apiDelete(withQuery(`${KNOWLEDGE}/documents/${documentId}`, { tenant_id: tenant }));
      setMessage({ ok: true, text: t("kn.removed") });
    } catch (cause) {
      setMessage({ ok: false, text: cause instanceof ApiError ? cause.message : t("common.error") });
    } finally {
      list.reload();
    }
  }

  return (
    <LightPage title={t("kn.title")} intro={t("kn.intro")}>
      <Async state={list} isEmpty={(d) => d.documents.length === 0 && d.managed} empty={<UploadFirst onFiles={upload} busy={busy === "upload"} />}>
        {(data) => {
          const waiting = data.documents.filter((d) => d.status === "uploaded" && d.current).length;
          return (
            <>
              {!data.managed ? <p className="muted">{t("kn.shipped")}</p> : null}
              {data.managed ? (
                <div className="ts-card">
                  <label>
                    <span>{t("kn.upload")}</span>
                    <input type="file" multiple accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.html,.htm" disabled={busy !== null} onChange={(e) => void upload(e.target.files)} />
                    <small className="muted">{t("kn.uploadHint")}</small>
                  </label>
                  {waiting > 0 ? <p role="status">{t("kn.waiting", { n: waiting })}</p> : null}
                  <button type="button" className="primary" onClick={() => void update()} disabled={busy !== null} aria-busy={busy === "update"}>
                    {busy === "update" ? t("kn.updating") : t("kn.update")}
                  </button>
                </div>
              ) : null}
              {message ? (
                <p role={message.ok ? "status" : "alert"} className={message.ok ? "ts-ok" : "ts-bad"}>
                  {message.text}
                </p>
              ) : null}
              <table>
                <thead>
                  <tr>
                    <th>{t("kn.col.file")}</th>
                    <th>{t("kn.col.version")}</th>
                    <th>{t("kn.col.status")}</th>
                    {data.managed ? <th /> : null}
                  </tr>
                </thead>
                <tbody>
                  {data.documents.map((d) => (
                    <tr key={d.file}>
                      <td>{d.filename ?? d.file}</td>
                      <td>
                        {d.version}
                        {!d.current && d.status !== "removed" ? <span className="muted"> · {t("kn.notCurrent")}</span> : null}
                      </td>
                      <td>
                        <span className={`badge ts-state-${d.status === "indexed" ? "published" : d.status === "failed" ? "needs_attention" : "testing"}`}>
                          {t(`kn.status.${d.status}` as TextKey)}
                        </span>
                        {d.error ? <div className="ts-bad">{d.error}</div> : null}
                      </td>
                      {data.managed ? (
                        <td>
                          {d.current && d.status !== "removed" ? (
                            <button type="button" onClick={() => void remove(d.document_id)} aria-label={`${t("kn.remove")} ${d.filename ?? d.file}`}>
                              {t("kn.remove")}
                            </button>
                          ) : null}
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          );
        }}
      </Async>
    </LightPage>
  );
}

function UploadFirst({ onFiles, busy }: { onFiles: (files: FileList | null) => void; busy: boolean }) {
  const { t } = useI18n();
  return (
    <EmptyState title={t("kn.empty")} hint={t("kn.emptyHint")}>
      <label>
        <span>{t("kn.upload")}</span>
        <input type="file" multiple accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.html,.htm" disabled={busy} onChange={(e) => onFiles(e.target.files)} />
      </label>
    </EmptyState>
  );
}
