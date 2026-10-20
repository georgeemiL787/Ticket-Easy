/** Turn the service's points into rows a chart (and the data table next to it) can use. Pure functions, tested. */
import { formatBucket } from "./dates";

export type Point = Record<string, unknown>;
export const OTHER = "other";
export const MAX_SERIES = 6; // distinct colours before the rest are grouped as "other"

export interface Row {
  bucket: string;
  label: string;
  [series: string]: string | number | null;
}

const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const asBucket = (point: Point): string => String(point.bucket_start ?? "");

/** One value per bucket, read from `field` (a number or null). */
export function valueRows(points: Point[], field: string, bucket: string, lang: string): Row[] {
  return points.map((p) => ({
    bucket: asBucket(p),
    label: formatBucket(asBucket(p), bucket, lang),
    value: num(p[field]),
  }));
}

/** A rate point has numerator, denominator and rate: the chart shows the rate as a percentage. */
export function rateRows(points: Point[], bucket: string, lang: string): Row[] {
  return points.map((p) => {
    const rate = num(p.rate);
    return { bucket: asBucket(p), label: formatBucket(asBucket(p), bucket, lang), value: rate === null ? null : rate * 100 };
  });
}

/** p95 latency per bucket, from latency points ({overall: {p95}}). */
export function latencyRows(points: Point[], bucket: string, lang: string): Row[] {
  return points.map((p) => {
    const overall = (p.overall ?? {}) as Point;
    return { bucket: asBucket(p), label: formatBucket(asBucket(p), bucket, lang), value: num(overall.p95) };
  });
}

/**
 * Escalation points ({by_reason: {reason: n}}) as stacked rows. The reasons with the most conversations keep their own
 * series (at most MAX_SERIES); the rest are added together as "other", so the colours stay told apart.
 */
export function stackedReasonRows(points: Point[], bucket: string, lang: string): { rows: Row[]; series: string[] } {
  const totals = new Map<string, number>();
  for (const p of points) {
    for (const [reason, count] of Object.entries((p.by_reason ?? {}) as Record<string, number>)) {
      totals.set(reason, (totals.get(reason) ?? 0) + count);
    }
  }
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([reason]) => reason);
  const kept = ranked.slice(0, ranked.length > MAX_SERIES ? MAX_SERIES - 1 : MAX_SERIES);
  const series = ranked.length > MAX_SERIES ? [...kept, OTHER] : kept;
  const rows = points.map((p) => {
    const row: Row = { bucket: asBucket(p), label: formatBucket(asBucket(p), bucket, lang) };
    for (const name of series) row[name] = 0;
    for (const [reason, count] of Object.entries((p.by_reason ?? {}) as Record<string, number>)) {
      const name = kept.includes(reason) ? reason : OTHER;
      row[name] = (row[name] as number) + count;
    }
    return row;
  });
  return { rows, series };
}

/** Language mix points ({counts: {en: n}}) added up over the whole window, biggest first. */
export function mixTotals(points: Point[]): { name: string; value: number }[] {
  const totals = new Map<string, number>();
  for (const p of points) {
    for (const [name, count] of Object.entries((p.counts ?? {}) as Record<string, number>)) {
      totals.set(name, (totals.get(name) ?? 0) + count);
    }
  }
  return [...totals.entries()].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value || a.name.localeCompare(b.name));
}

export const hasData = (rows: Row[]): boolean => rows.some((r) => r.value !== null && r.value !== undefined);
