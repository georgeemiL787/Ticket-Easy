import { NavLink, Outlet, useSearchParams } from "react-router-dom";
import { useI18n, type Lang } from "../i18n";
import type { TextKey } from "../i18n/en";
import { RANGES, type RangeKey } from "../lib/dates";
import { useApi } from "../api/useApi";
import type { DashboardTenants } from "../api/types/DashboardTenants";
import { sharedQuery, useFilters } from "../state/useFilters";

export const NAV: { to: string; key: TextKey; end?: boolean }[] = [
  { to: "/", key: "nav.overview", end: true },
  { to: "/conversations", key: "nav.conversations" },
  { to: "/escalations", key: "nav.escalations" },
  { to: "/actions", key: "nav.actions" },
  { to: "/unanswered", key: "nav.unanswered" },
  { to: "/alerts", key: "nav.alerts" },
];

function TopBar() {
  const { t } = useI18n();
  const filters = useFilters();
  const tenants = useApi<DashboardTenants>("/v1/dashboard/tenants");
  const choices = tenants.data?.tenants ?? [{ tenant_id: filters.tenant, display_name: filters.tenant, default_locale: "en" }];
  return (
    <header className="topbar">
      <label>
        <span>{t("top.tenant")}</span>
        <select value={filters.tenant} onChange={(e) => filters.update({ tenant: e.target.value })}>
          {choices.map((c) => (
            <option key={c.tenant_id} value={c.tenant_id}>
              {c.display_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("top.range")}</span>
        <select value={filters.range} onChange={(e) => filters.update({ range: e.target.value as RangeKey })}>
          {RANGES.map((r) => (
            <option key={r} value={r}>
              {t(`range.${r}` as TextKey)}
            </option>
          ))}
        </select>
      </label>
      {filters.range === "custom" ? (
        <>
          <label>
            <span>{t("top.from")}</span>
            <input type="date" value={filters.from} onChange={(e) => filters.update({ from: e.target.value })} />
          </label>
          <label>
            <span>{t("top.to")}</span>
            <input type="date" value={filters.to} onChange={(e) => filters.update({ to: e.target.value })} />
          </label>
        </>
      ) : null}
      <label>
        <span>{t("top.language")}</span>
        <select value={filters.lang} onChange={(e) => filters.update({ lang: e.target.value as Lang })}>
          <option value="en">{t("lang.en")}</option>
          <option value="ar">{t("lang.ar")}</option>
        </select>
      </label>
      <button type="button" className="refresh" onClick={filters.refresh} aria-label={t("common.refresh")} title={t("common.refresh")}>
        ⟳
      </button>
    </header>
  );
}

export function Layout() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const keep = sharedQuery(params);
  return (
    <div className="shell">
      <nav className="nav" aria-label={t("nav.label")}>
        <strong className="brand">Ticket-Easy</strong>
        {NAV.map((item) => (
          <NavLink key={item.to} to={{ pathname: item.to, search: keep }} end={item.end}>
            {t(item.key)}
          </NavLink>
        ))}
      </nav>
      <div className="main">
        <TopBar />
        <main id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
