export type RangeKey = "24h" | "7d" | "30d" | "custom";
export const RANGES: RangeKey[] = ["24h", "7d", "30d", "custom"];
const HOUR = 3_600_000;
const SPAN: Record<Exclude<RangeKey, "custom">, number> = { "24h": 24 * HOUR, "7d": 7 * 24 * HOUR, "30d": 30 * 24 * HOUR };

export function isRange(value: string | null | undefined): value is RangeKey {
  return value === "24h" || value === "7d" || value === "30d" || value === "custom";
}

export interface Window {
  from: string;
  to: string;
}

/** The ISO window of a range. A custom range uses the two dates (inclusive days, UTC); bad dates fall back to 7 days. */
export function windowFor(range: RangeKey, now: Date, from?: string | null, to?: string | null): Window {
  if (range === "custom" && from && to) {
    const start = Date.parse(`${from}T00:00:00Z`);
    const end = Date.parse(`${to}T00:00:00Z`) + 24 * HOUR;
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
      return { from: new Date(start).toISOString(), to: new Date(end).toISOString() };
    }
  }
  const span = range === "custom" ? SPAN["7d"] : SPAN[range];
  return { from: new Date(now.getTime() - span).toISOString(), to: now.toISOString() };
}

/** A bucket that suits the window: hours for a day, days up to a month and a half, else weeks. */
export function bucketFor(window: Window): "hour" | "day" | "week" {
  const span = Date.parse(window.to) - Date.parse(window.from);
  if (span <= 36 * HOUR) return "hour";
  return span <= 45 * 24 * HOUR ? "day" : "week";
}

export function formatDateTime(iso: string, lang: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(lang === "ar" ? "ar-EG" : "en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function formatBucket(iso: string, bucket: string, lang: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const locale = lang === "ar" ? "ar-EG" : "en-GB";
  if (bucket === "hour") return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  return date.toLocaleDateString(locale, { day: "numeric", month: "short" });
}

/** Whole seconds as "5 min", "2 h", "3 d" (English or Arabic digits are left to the browser). */
export function formatAge(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}
