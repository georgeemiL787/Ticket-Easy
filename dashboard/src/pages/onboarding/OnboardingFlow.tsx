import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { goLive, ONBOARDING, previewPass, saveProfile, saveStep, STEP_ORDER, type Draft, type Profile, type Readiness, type Step } from "../../api/onboarding";
import { useApi } from "../../api/useApi";
import { Async } from "../../components/Async";
import { LightPage } from "../../components/LightPage";
import { useI18n } from "../../i18n";
import type { TextKey } from "../../i18n/en";
import { ProfileForm } from "./ProfileForm";

const LINKS: Partial<Record<Step, string>> = { knowledge: "/knowledge", guardrails: "/guardrails", connect: "/tools/connect", tools: "/tools/review" };

/** One business, step by step. The steps that already have their own screens link to them; the wizard keeps the place. */
export default function OnboardingFlow() {
  const { t } = useI18n();
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const draft = useApi<Draft>(`${ONBOARDING}/${id}`);
  const review = useApi<Readiness>(`${ONBOARDING}/${id}/review`);
  const [step, setStep] = useState<Step | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chat, setChat] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const current: Step = step ?? (params.get("step") as Step | null) ?? draft.data?.step ?? "profile";

  useEffect(() => {
    if (draft.data && step === null) setStep((params.get("step") as Step | null) ?? draft.data.step);
  }, [draft.data, step, params]);

  async function go(next: Step) {
    setStep(next);
    setParams((p) => ({ ...Object.fromEntries(p), step: next }), { replace: true });
    try {
      await saveStep(id, next);
    } catch {
      /* the place is also kept in the address */
    }
    if (next === "review") review.reload();
  }

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <LightPage title={draft.data?.profile.name ?? t("ob.title")} intro={t("ob.intro")}>
      <Async state={draft}>
        {(data) => {
          const index = STEP_ORDER.indexOf(current);
          const ready = review.data;
          const withTenant = (path: string) => `${path}?tenant=${id}&wizard=${id}`;
          return (
            <>
              <nav className="ts-steps" aria-label={t("ob.step.label")}>
                {STEP_ORDER.map((s) => (
                  <button key={s} type="button" className={s === current ? "ts-step current" : "ts-step"} aria-current={s === current ? "step" : undefined} onClick={() => void go(s)}>
                    {t(`ob.step.${s}` as TextKey)}
                  </button>
                ))}
              </nav>
              <p className="muted">{t("ob.stepOf", { n: index + 1, total: STEP_ORDER.length })}</p>

              {current === "profile" ? (
                <ProfileForm
                  key={data.bundle_version ?? 0}
                  initial={data.profile}
                  submitLabel={t("ob.profile.save")}
                  busyLabel={t("ob.profile.saving")}
                  busy={busy}
                  onSubmit={(profile: Profile) =>
                    void run(async () => {
                      await saveProfile(id, profile);
                      draft.reload();
                      await go("knowledge");
                    })
                  }
                />
              ) : null}

              {current !== "profile" && current !== "review" ? (
                <section className="ts-card">
                  <p>{t(`ob.${current}.text` as TextKey)}</p>
                  {current === "knowledge" && ready ? <p className="muted">{t("ob.status.documents", { n: ready.documents.indexed })}</p> : null}
                  {current === "guardrails" && ready ? <p className="muted">{t("ob.status.rules", { n: ready.rules.approved })}</p> : null}
                  {current === "tools" && ready ? <p className="muted">{t("ob.status.tools", { n: ready.tools.length })}</p> : null}
                  <p>
                    <Link className="primary" to={withTenant(LINKS[current]!)}>
                      {t("ob.open")}
                    </Link>{" "}
                    <button type="button" onClick={() => void go(STEP_ORDER[index + 1]!)}>
                      {t("ob.next")}
                    </button>
                  </p>
                </section>
              ) : null}

              {current === "review" ? (
                <section className="ts-card">
                  {ready?.tools.length ? (
                    <label>
                      <span>{t("ob.verify")}</span>
                      <select
                        value={ready.verify_tool ?? ""}
                        onChange={(e) =>
                          void run(async () => {
                            await saveStep(id, "review", e.target.value);
                            review.reload();
                          })
                        }
                      >
                        <option value="">{t("ob.verifyNone")}</option>
                        {ready.tools.map((tool) => (
                          <option key={tool} value={tool}>
                            {tool}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  <p className="muted">{t("ob.review.try")}</p>
                  {chat ? <iframe title={t("ob.review.try")} src={chat} className="ts-chat" /> : null}
                  <button
                    type="button"
                    onClick={() =>
                      void run(async () => {
                        const pass = await previewPass(id);
                        setChat(pass.chat_url);
                      })
                    }
                  >
                    {t("ob.review.try")}
                  </button>{" "}
                  <button type="button" onClick={() => review.reload()}>
                    {t("ob.review.refresh")}
                  </button>
                  {ready && ready.blockers.length > 0 ? (
                    <>
                      <p>{t("ob.review.blocked")}</p>
                      <ul>
                        {ready.blockers.map((b) => (
                          <li key={b.code}>{(`ob.block.${b.code}` as TextKey) in { "ob.block.no_documents": 1, "ob.block.documents_waiting": 1, "ob.block.no_approved_rules": 1, "ob.block.no_tools": 1, "ob.block.no_verify_tool": 1, "ob.block.no_test_chat": 1, "ob.block.knowledge_unavailable": 1 } ? t(`ob.block.${b.code}` as TextKey) : b.message}</li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                  {ready?.ready && !done && !data.live ? <p className="ts-ok">{t("ob.review.ready")}</p> : null}
                  {done || data.live ? (
                    <p role="status" className="ts-ok">
                      {t("ob.review.done")} <a href={`/chat?tenant_id=${id}`}>{t("ob.review.openChat")}</a>
                    </p>
                  ) : (
                    <button
                      type="button"
                      className="primary"
                      disabled={busy || !ready?.ready}
                      aria-busy={busy}
                      onClick={() =>
                        void run(async () => {
                          await goLive(id);
                          setDone(true);
                          draft.reload();
                        })
                      }
                    >
                      {busy ? t("ob.review.going") : t("ob.review.goLive")}
                    </button>
                  )}
                </section>
              ) : null}
              {error ? (
                <p role="alert" className="ts-bad">
                  {error}
                </p>
              ) : null}
            </>
          );
        }}
      </Async>
    </LightPage>
  );
}
