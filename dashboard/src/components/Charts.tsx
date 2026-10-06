import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useI18n } from "../i18n";
import type { Row } from "../lib/series";

export const SERIES_COLOURS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)"];
const HEIGHT = 240;

/** A chart with a title and, behind a disclosure, the same numbers as a table (for screen readers and for exactness). */
export function ChartCard(props: { title: string; table: ReactNode; children: ReactNode }) {
  const { t } = useI18n();
  return (
    <figure className="chart-card">
      <figcaption>{props.title}</figcaption>
      <div className="chart" role="img" aria-label={props.title}>
        {props.children}
      </div>
      <details>
        <summary>{t("common.table")}</summary>
        {props.table}
      </details>
    </figure>
  );
}

export function RowsTable(props: { rows: Row[]; series: { key: string; label: string }[]; format?: (value: number) => string }) {
  const { t } = useI18n();
  const show = props.format ?? ((v: number) => String(Math.round(v * 10) / 10));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{t("conv.time")}</th>
            {props.series.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.rows.map((row) => (
            <tr key={row.bucket}>
              <td>{row.label}</td>
              {props.series.map((s) => {
                const value = row[s.key];
                return <td key={s.key}>{typeof value === "number" ? show(value) : t("common.noData")}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One line over time. */
export function TimeLine(props: { rows: Row[]; name: string; unit?: string }) {
  const { dir } = useI18n();
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <LineChart data={props.rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
        <XAxis dataKey="label" stroke="var(--muted)" tick={{ fill: "var(--muted)", fontSize: 12 }} reversed={dir === "rtl"} />
        <YAxis stroke="var(--muted)" tick={{ fill: "var(--muted)", fontSize: 12 }} width={44} orientation={dir === "rtl" ? "right" : "left"} />
        <Tooltip
          contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }}
          formatter={(value) => [`${Math.round(Number(value) * 10) / 10}${props.unit ?? ""}`, props.name]}
        />
        <Line isAnimationActive={false} type="monotone" dataKey="value" name={props.name} stroke="var(--c1)" strokeWidth={2.5} dot={{ r: 3 }} connectNulls={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Stacked bars over time, one series per reason, each also told apart by its label in the legend. */
export function StackedBars(props: { rows: Row[]; series: { key: string; label: string }[] }) {
  const { dir } = useI18n();
  return (
    <ResponsiveContainer width="100%" height={HEIGHT + 40}>
      <BarChart data={props.rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
        <XAxis dataKey="label" stroke="var(--muted)" tick={{ fill: "var(--muted)", fontSize: 12 }} reversed={dir === "rtl"} />
        <YAxis allowDecimals={false} stroke="var(--muted)" tick={{ fill: "var(--muted)", fontSize: 12 }} width={32} orientation={dir === "rtl" ? "right" : "left"} />
        <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
        <Legend />
        {props.series.map((s, index) => (
          <Bar key={s.key} isAnimationActive={false} dataKey={s.key} name={s.label} stackId="reasons" fill={SERIES_COLOURS[index % SERIES_COLOURS.length]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** A bar per category (the language mix), biggest first. */
export function CategoryBars(props: { data: { name: string; label: string; value: number }[] }) {
  const { dir } = useI18n();
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <BarChart data={props.data} layout="vertical" margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" allowDecimals={false} stroke="var(--muted)" tick={{ fill: "var(--muted)", fontSize: 12 }} reversed={dir === "rtl"} />
        <YAxis type="category" dataKey="label" width={90} stroke="var(--muted)" tick={{ fill: "var(--text)", fontSize: 13 }} orientation={dir === "rtl" ? "right" : "left"} />
        <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--border)", color: "var(--text)" }} />
        <Bar isAnimationActive={false} dataKey="value" fill="var(--c3)" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
