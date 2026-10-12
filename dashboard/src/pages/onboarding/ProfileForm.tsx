import { useState, type FormEvent } from "react";
import type { Profile } from "../../api/onboarding";
import { useI18n } from "../../i18n";
import type { TextKey } from "../../i18n/en";

export const EMPTY_PROFILE: Profile = { name: "", locales: ["en"], default_locale: "en", tone: "friendly", order_id_prefix: "", order_id_digits: 6, contacts: [] };
const LOCALES = ["en", "ar", "arabizi"] as const;
const TONES = ["friendly", "formal", "neutral"] as const;

/** The first screen: name, languages, tone, how order numbers look, and who takes over difficult chats. */
export function ProfileForm({ initial, submitLabel, busyLabel, busy, onSubmit }: { initial: Profile; submitLabel: string; busyLabel: string; busy: boolean; onSubmit: (profile: Profile) => void }) {
  const { t } = useI18n();
  const [profile, setProfile] = useState<Profile>(initial);
  const [contacts, setContacts] = useState(initial.contacts.join(", "));

  function toggle(locale: string) {
    const next = profile.locales.includes(locale) ? profile.locales.filter((l) => l !== locale) : [...profile.locales, locale];
    if (next.length === 0) return;
    setProfile({ ...profile, locales: next, default_locale: next.includes(profile.default_locale) ? profile.default_locale : next[0]! });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit({ ...profile, name: profile.name.trim(), contacts: contacts.split(",").map((c) => c.trim()).filter(Boolean) });
  }

  return (
    <form className="ts-card" onSubmit={submit}>
      <label>
        <span>{t("ob.profile.name")}</span>
        <input required minLength={2} maxLength={80} value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} />
      </label>
      <fieldset>
        <legend>{t("ob.profile.locales")}</legend>
        {LOCALES.map((locale) => (
          <label className="ts-inline" key={locale}>
            <input type="checkbox" checked={profile.locales.includes(locale)} onChange={() => toggle(locale)} />
            {t(`ob.locale.${locale}` as TextKey)}
          </label>
        ))}
      </fieldset>
      <label>
        <span>{t("ob.profile.default")}</span>
        <select value={profile.default_locale} onChange={(e) => setProfile({ ...profile, default_locale: e.target.value })}>
          {profile.locales.map((locale) => (
            <option key={locale} value={locale}>
              {t(`ob.locale.${locale}` as TextKey)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("ob.profile.tone")}</span>
        <select value={profile.tone} onChange={(e) => setProfile({ ...profile, tone: e.target.value as Profile["tone"] })}>
          {TONES.map((tone) => (
            <option key={tone} value={tone}>
              {t(`ob.tone.${tone}` as TextKey)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("ob.profile.prefix")}</span>
        <input pattern="[A-Za-z0-9\-]*" maxLength={12} value={profile.order_id_prefix} onChange={(e) => setProfile({ ...profile, order_id_prefix: e.target.value })} />
      </label>
      <label>
        <span>{t("ob.profile.digits")}</span>
        <input type="number" min={3} max={12} value={profile.order_id_digits} onChange={(e) => setProfile({ ...profile, order_id_digits: Number(e.target.value) })} />
      </label>
      <label>
        <span>{t("ob.profile.contacts")}</span>
        <input value={contacts} onChange={(e) => setContacts(e.target.value)} />
      </label>
      <button type="submit" className="primary" disabled={busy || profile.name.trim().length < 2} aria-busy={busy}>
        {busy ? busyLabel : submitLabel}
      </button>
    </form>
  );
}
