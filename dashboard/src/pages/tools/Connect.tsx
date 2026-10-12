import { useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { checkConnection, importSpec, SETUP, type Account, type ImportResult, type SetupState } from "../../api/toolSetup";
import { useApi } from "../../api/useApi";
import { Async } from "../../components/Async";
import { useI18n } from "../../i18n";
import { loadChoice, saveChoice, type SetupChoice } from "../../state/setupStore";
import { sharedQuery, useFilters } from "../../state/useFilters";
import { SetupFrame } from "./SetupFrame";

const emptyAccount = (): Account => ({ name: "", scope: "end_user", token: "", context: {} });

/** Screen 1: the API description, where the API lives, who uses the tools, and (optionally) test accounts. */
export default function Connect() {
  const { t } = useI18n();
  const { tenant } = useFilters();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const state = useApi<SetupState>(SETUP, { tenant_id: tenant });
  const [choice, setChoice] = useState<SetupChoice>(() => loadChoice(tenant));
  const [url, setUrl] = useState("");
  const [fileName, setFileName] = useState("");
  const file = useRef<string | null>(null);
  const [reading, setReading] = useState(false);
  const [read, setRead] = useState<ImportResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [reach, setReach] = useState<{ ok: boolean; detail: string } | null>(null);

  const update = (changes: Partial<SetupChoice>) => {
    const next = { ...choice, ...changes };
    setChoice(next);
    saveChoice(tenant, next);
  };

  async function readDescription(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    if (!url.trim() && file.current === null) {
      setProblem(t("ts.connect.chooseFirst"));
      return;
    }
    setReading(true);
    try {
      const result = await importSpec(tenant, file.current !== null ? { filename: fileName || "api.json", content: file.current } : { url: url.trim() });
      setRead(result);
      if (result.valid) update({ specId: result.spec_id });
      else setProblem(`${t("ts.connect.readBad")} ${result.problems.join(" ")}`);
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setReading(false);
    }
  }

  async function check() {
    setReach(null);
    if (!choice.baseUrl.trim()) {
      setProblem(t("ts.connect.needUrl"));
      return;
    }
    setChecking(true);
    try {
      const result = await checkConnection(tenant, choice.baseUrl.trim());
      setReach({ ok: result.reachable, detail: result.detail ?? "" });
    } catch (cause) {
      setReach({ ok: false, detail: cause instanceof ApiError ? cause.message : t("common.error") });
    } finally {
      setChecking(false);
    }
  }

  function setAccount(index: number, changes: Partial<Account>) {
    update({ accounts: choice.accounts.map((a, i) => (i === index ? { ...a, ...changes } : a)) });
  }

  function proceed() {
    if (!choice.specId) {
      setProblem(t("ts.connect.needSpec"));
      return;
    }
    if (!choice.baseUrl.trim()) {
      setProblem(t("ts.connect.needUrl"));
      return;
    }
    navigate({ pathname: "/tools/review", search: sharedQuery(params) });
  }

  return (
    <SetupFrame title={t("ts.connect.title")} intro={t("ts.connect.intro")}>
      <Async state={state}>
        {(known) => (
          <>
            <form className="ts-card" onSubmit={readDescription} aria-label={t("ts.connect.upload")}>
              <label>
                <span>{t("ts.connect.upload")}</span>
                <input
                  type="file"
                  accept=".json,.yaml,.yml,application/json"
                  onChange={async (e) => {
                    const picked = e.target.files?.[0];
                    file.current = picked ? await picked.text() : null;
                    setFileName(picked?.name ?? "");
                  }}
                />
                <small className="muted">{t("ts.connect.fileHint")}</small>
              </label>
              <label>
                <span>{t("ts.connect.url")}</span>
                <input type="url" value={url} placeholder={t("ts.connect.urlPlaceholder")} onChange={(e) => setUrl(e.target.value)} />
              </label>
              <button type="submit" className="primary" disabled={reading} aria-busy={reading}>
                {reading ? t("ts.connect.reading") : t("ts.connect.read")}
              </button>
              {read?.valid ? (
                <p role="status" className="ts-ok">
                  {t("ts.connect.readOk", { usable: read.usable, total: read.operations })}
                  {read.converted_from ? ` ${t("ts.connect.converted")}` : ""}
                </p>
              ) : choice.specId && !read ? (
                <p role="status" className="ts-ok">
                  {t("ts.connect.readOk", { usable: known.specs.find((s) => s.id === choice.specId)?.eligible ?? 0, total: known.specs.find((s) => s.id === choice.specId)?.operations ?? 0 })}
                </p>
              ) : null}
            </form>

            <section className="ts-card" aria-label={t("ts.connect.connection")}>
              <h2>{t("ts.connect.connection")}</h2>
              <label>
                <span>{t("ts.connect.baseUrl")}</span>
                <input type="url" value={choice.baseUrl} onChange={(e) => update({ baseUrl: e.target.value })} placeholder="https://" />
                <small className="muted">{t("ts.connect.baseUrlHint")}</small>
              </label>
              <button type="button" onClick={() => void check()} disabled={checking} aria-busy={checking}>
                {checking ? t("ts.connect.checking") : t("ts.connect.check")}
              </button>
              {reach ? (
                <p role="status" className={reach.ok ? "ts-ok" : "ts-bad"}>
                  {reach.ok ? t("ts.connect.reachable") : t("ts.connect.unreachable", { detail: reach.detail })}
                </p>
              ) : null}
              <fieldset>
                <legend>{t("ts.connect.who")}</legend>
                <label className="ts-inline">
                  <input type="radio" name="callers" checked={choice.callers === "customer"} onChange={() => update({ callers: "customer" })} />
                  {t("ts.connect.customer")}
                </label>
                <label className="ts-inline">
                  <input type="radio" name="callers" checked={choice.callers === "public"} onChange={() => update({ callers: "public" })} />
                  {t("ts.connect.public")}
                </label>
                {choice.callers === "customer" ? (
                  <label className="ts-inline">
                    <input type="checkbox" checked={choice.ownRecordsOnly} onChange={(e) => update({ ownRecordsOnly: e.target.checked })} />
                    {t("ts.connect.own")}
                  </label>
                ) : null}
              </fieldset>
              <details className="ts-details">
                <summary>{t("ts.connect.details")}</summary>
                <p className="muted">{t("ts.connect.detailsHint")}</p>
                {choice.accounts.map((account, index) => (
                  <div className="ts-account" key={index}>
                    <label>
                      <span>{t("ts.connect.accountName")}</span>
                      <input value={account.name} onChange={(e) => setAccount(index, { name: e.target.value })} />
                    </label>
                    <label>
                      <span>{t("ts.connect.credential")}</span>
                      <input type="password" autoComplete="off" value={account.token ?? ""} onChange={(e) => setAccount(index, { token: e.target.value })} />
                    </label>
                    <label>
                      <span>{t("ts.connect.customerId")}</span>
                      <input
                        value={Object.values(account.context ?? {})[0] ?? ""}
                        onChange={(e) => setAccount(index, { context: e.target.value ? { customer_id: e.target.value } : {} })}
                      />
                    </label>
                    <button type="button" className="link" onClick={() => update({ accounts: choice.accounts.filter((_, i) => i !== index) })}>
                      {t("ts.connect.removeAccount")}
                    </button>
                  </div>
                ))}
                <button type="button" onClick={() => update({ accounts: [...choice.accounts, emptyAccount()] })}>
                  {t("ts.connect.addAccount")}
                </button>
                <label>
                  <span>{t("ts.connect.fixturesUrl")}</span>
                  <input type="url" value={choice.fixtures?.base_url ?? ""} onChange={(e) => update({ fixtures: { base_url: e.target.value, key_ref: choice.fixtures?.key_ref ?? "" } })} />
                </label>
                <label>
                  <span>{t("ts.connect.fixturesKey")}</span>
                  <input value={choice.fixtures?.key_ref ?? ""} onChange={(e) => update({ fixtures: { base_url: choice.fixtures?.base_url ?? "", key_ref: e.target.value } })} />
                </label>
              </details>
            </section>
            {problem ? (
              <p role="alert" className="ts-bad">
                {problem}
              </p>
            ) : null}
            <p>
              <button type="button" className="primary" onClick={proceed}>
                {t("ts.connect.continue")}
              </button>
            </p>
          </>
        )}
      </Async>
    </SetupFrame>
  );
}
