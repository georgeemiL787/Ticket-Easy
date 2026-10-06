import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import ar from "./ar";
import en, { type TextKey } from "./en";

export type Lang = "en" | "ar";
export const LANGS: Lang[] = ["en", "ar"];
export const DICTIONARIES: Record<Lang, Record<TextKey, string>> = { en, ar };

export function isLang(value: string | null | undefined): value is Lang {
  return value === "en" || value === "ar";
}

export type Translate = (key: TextKey, vars?: Record<string, string | number>) => string;

/** Look a text up and fill its {placeholders}. */
export function translate(lang: Lang, key: TextKey, vars?: Record<string, string | number>): string {
  const text = DICTIONARIES[lang][key] ?? en[key];
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}

/** Arabic reads right to left; everything else left to right. */
export function directionOf(lang: Lang): "rtl" | "ltr" {
  return lang === "ar" ? "rtl" : "ltr";
}

interface I18n {
  lang: Lang;
  dir: "rtl" | "ltr";
  t: Translate;
}

const I18nContext = createContext<I18n>({ lang: "en", dir: "ltr", t: (key, vars) => translate("en", key, vars) });

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const value = useMemo<I18n>(
    () => ({ lang, dir: directionOf(lang), t: (key, vars) => translate(lang, key, vars) }),
    [lang],
  );
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = value.dir;
    document.title = translate(lang, "app.title");
  }, [lang, value.dir]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  return useContext(I18nContext);
}

/** The label of an escalation reason, decision or language value; unknown values are shown as they are. */
export function labelFor(t: Translate, prefix: "reason" | "decision" | "lang.value", value: string | null | undefined): string {
  if (!value) return t("common.noData");
  const key = `${prefix}.${value}` as TextKey;
  return key in en ? t(key) : value.replaceAll("_", " ");
}
