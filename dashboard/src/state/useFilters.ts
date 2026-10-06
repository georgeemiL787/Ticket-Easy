import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { isLang, type Lang } from "../i18n";
import { bucketFor, isRange, windowFor, type RangeKey, type Window } from "../lib/dates";

export const DEFAULT_TENANT = "shop_001";

export interface Filters {
  tenant: string;
  range: RangeKey;
  from: string;
  to: string;
  lang: Lang;
  /** The ISO window the range stands for, worked out when the filters are read. */
  window: Window;
  bucket: "hour" | "day" | "week";
  /** Change some filters; `null` removes one. Everything lives in the URL, so a link shares the view. */
  update: (changes: Record<string, string | null>) => void;
  /** Move the window's end to now and load everything again. */
  refresh: () => void;
}

/** The page-wide filters: business, period and display language. They are kept in the address (?tenant=&range=&lang=). */
export function useFilters(): Filters {
  const [params, setParams] = useSearchParams();
  const tenant = params.get("tenant") || DEFAULT_TENANT;
  const rangeParam = params.get("range");
  const range: RangeKey = isRange(rangeParam) ? rangeParam : "7d";
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const langParam = params.get("lang");
  const lang: Lang = isLang(langParam) ? langParam : "en";

  const refreshed = params.get("r") ?? "";
  // the window is fixed until a filter changes or Refresh is pressed, so a page does not reload on every render
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const window = useMemo(() => windowFor(range, new Date(), from, to), [range, from, to, refreshed]);
  const update = useCallback(
    (changes: Record<string, string | null>) => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          for (const [key, value] of Object.entries(changes)) {
            if (value === null || value === "") next.delete(key);
            else next.set(key, value);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  const refresh = useCallback(() => update({ r: String(Date.now()) }), [update]);
  return { tenant, range, from, to, lang, window, bucket: bucketFor(window), update, refresh };
}

/** The filters that must follow the user from page to page (the nav links carry them). */
export const SHARED_KEYS = ["tenant", "range", "from", "to", "lang"] as const; // not "r": a link is not a refresh

export function sharedQuery(params: URLSearchParams): string {
  const kept = new URLSearchParams();
  for (const key of SHARED_KEYS) {
    const value = params.get(key);
    if (value) kept.set(key, value);
  }
  const text = kept.toString();
  return text ? `?${text}` : "";
}
