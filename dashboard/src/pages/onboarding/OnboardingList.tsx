import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { createBusiness, ONBOARDING, type Draft, type Profile } from "../../api/onboarding";
import { useApi } from "../../api/useApi";
import { Async } from "../../components/Async";
import { LightPage } from "../../components/LightPage";
import { useI18n } from "../../i18n";
import { sharedQuery } from "../../state/useFilters";
import { EMPTY_PROFILE, ProfileForm } from "./ProfileForm";

/** Start a new business or pick one up where it was left. */
export default function OnboardingList() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const list = useApi<{ businesses: Draft[] }>(ONBOARDING);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keep = sharedQuery(params).replace(/(^\?|&)tenant=[^&]*/, "$1").replace(/^\?&/, "?");

  async function create(profile: Profile) {
    setBusy(true);
    setError(null);
    try {
      const made = await createBusiness(profile);
      navigate({ pathname: `/onboarding/${made.tenant_id}`, search: keep === "?" ? "" : keep });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t("common.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <LightPage title={t("ob.title")} intro={t("ob.intro")}>
      <Async state={list}>
        {(data) => (
          <>
            {data.businesses.length === 0 ? <p className="muted">{t("ob.none")}</p> : null}
            {data.businesses.map((b) => (
              <article className="ts-item" key={b.tenant_id} aria-label={b.profile.name}>
                <header>
                  <strong>{b.profile.name}</strong>
                  <span className={`badge ts-state-${b.live ? "published" : "testing"}`}>{b.live ? t("ob.live") : t("ob.draft")}</span>
                </header>
                <Link to={{ pathname: `/onboarding/${b.tenant_id}`, search: keep === "?" ? "" : keep }}>{t("ob.resume")}</Link>
              </article>
            ))}
            <h2>{t("ob.new")}</h2>
            <ProfileForm initial={EMPTY_PROFILE} submitLabel={t("ob.profile.create")} busyLabel={t("ob.profile.saving")} busy={busy} onSubmit={(p) => void create(p)} />
            {error ? (
              <p role="alert" className="ts-bad">
                {error}
              </p>
            ) : null}
          </>
        )}
      </Async>
    </LightPage>
  );
}
