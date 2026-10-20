import { useI18n, type Translate } from "../i18n";

interface Value {
  value: number | null;
  previous: number | null;
  change: number | null;
}

export type KpiFormat = "count" | "percent" | "ms";

export function formatValue(value: number | null, format: KpiFormat, lang: string): string {
  if (value === null) return "—";
  const locale = lang === "ar" ? "ar-EG" : "en-GB";
  if (format === "percent") return new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 }).format(value);
  if (format === "ms") return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value)} ms`;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value);
}

/** "up 2.5 points" in words, so the change is not told by colour alone. */
export function describeChange(t: Translate, change: number | null, format: KpiFormat, lang: string): string {
  if (change === null) return t("common.noPrevious");
  if (change === 0) return t("common.unchanged");
  const size = format === "percent" ? `${formatValue(Math.abs(change) * 100, "count", lang)} pts` : formatValue(Math.abs(change), format, lang);
  return `${change > 0 ? t("common.up") : t("common.down")} ${size}`;
}

/** Is the change good news? Rates that should fall (escalation, unverified, slow replies) are flipped. */
export function tone(change: number | null, higherIsBetter: boolean): "good" | "bad" | "flat" {
  if (change === null || change === 0) return "flat";
  return change > 0 === higherIsBetter ? "good" : "bad";
}

export function KpiTile(props: { label: string; kpi: Value; format: KpiFormat; higherIsBetter?: boolean; hint?: string }) {
  const { t, lang } = useI18n();
  const { label, kpi, format, higherIsBetter = true, hint } = props;
  const direction = kpi.change === null || kpi.change === 0 ? "▬" : kpi.change > 0 ? "▲" : "▼";
  return (
    <section className="kpi" aria-label={label}>
      <h3>{label}</h3>
      <p className="kpi-value">{formatValue(kpi.value, format, lang)}</p>
      <p className={`kpi-change ${tone(kpi.change, higherIsBetter)}`}>
        <span aria-hidden="true">{direction}</span> {describeChange(t, kpi.change, format, lang)}
        <span className="muted"> · {t("common.vsPrevious")}</span>
      </p>
      {hint ? <p className="muted small">{hint}</p> : null}
    </section>
  );
}
