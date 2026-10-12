import { useState } from "react";
import { NavLink, Outlet, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { useI18n, type Lang } from "../i18n";
import type { TextKey } from "../i18n/en";
import { RANGES, type RangeKey } from "../lib/dates";
import { useApi } from "../api/useApi";
import type { DashboardTenants } from "../api/types/DashboardTenants";
import { sharedQuery, useFilters } from "../state/useFilters";

// 24x24 stroke icons (one path each), drawn for this app; the label next to each icon says what it is.
const ICONS: Record<string, string> = {
  overview: "M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z",
  conversations: "M4 5h16v11H9l-5 4z",
  escalations: "M6 3v18M6 4h11l-2 4 2 4H6",
  actions: "M13 2 4 14h7l-1 8 9-12h-7z",
  unanswered: "M9 9a3 3 0 1 1 4 2.8c-.7.4-1 .9-1 1.7M12 17.5v.5M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z",
  alerts: "M6 17V11a6 6 0 1 1 12 0v6l2 2H4zM10 21h4",
  onboarding: "M12 3c4 1 6 4 6 8l-2 3H8l-2-3c0-4 2-7 6-8zM9 17l-2 4M15 17l2 4M12 8v.01",
  knowledge: "M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h10",
  guardrails: "M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6zM9 12l2 2 4-4",
  tools: "M14 6a4 4 0 0 0-5 5l-6 6 3 3 6-6a4 4 0 0 0 5-5l-3 3-2-2z",
};

export const NAV: { to: string; key: TextKey; icon: keyof typeof ICONS; end?: boolean }[] = [
  { to: "/", key: "nav.overview", icon: "overview", end: true },
  { to: "/conversations", key: "nav.conversations", icon: "conversations" },
  { to: "/escalations", key: "nav.escalations", icon: "escalations" },
  { to: "/actions", key: "nav.actions", icon: "actions" },
  { to: "/unanswered", key: "nav.unanswered", icon: "unanswered" },
  { to: "/alerts", key: "nav.alerts", icon: "alerts" },
  { to: "/onboarding", key: "nav.onboarding", icon: "onboarding" },
  { to: "/knowledge", key: "nav.knowledge", icon: "knowledge" },
  { to: "/guardrails", key: "nav.guardrails", icon: "guardrails" },
  { to: "/tools", key: "nav.businessTools", icon: "tools" },
];

function Icon({ name }: { name: string }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={ICONS[name]} />
    </svg>
  );
}

function TopBar() {
  const { t } = useI18n();
  const filters = useFilters();
  const tenants = useApi<DashboardTenants>("/v1/dashboard/tenants");
  const auth = useAuth();
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
      {auth?.me.user ? (
        <span className="whoami">
          {auth.me.user.display_name} ({auth.me.user.role}){" "}
          <button type="button" onClick={() => void auth.signOut()}>
            {t("login.signOut")}
          </button>
        </span>
      ) : null}
    </header>
  );
}

export function Layout() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const filters = useFilters();
  const keep = sharedQuery(params);
  const [open, setOpen] = useState(false);
  // the number of open alerts, so a problem is seen from any page; a failed call just shows no number
  const alerts = useApi<{ open_count: number }>("/v1/dashboard/alerts", { tenant_id: filters.tenant, status: "open", limit: 1 });
  const openAlerts = alerts.data?.open_count ?? 0;
  return (
    <div className="shell">
      <nav className={open ? "nav open" : "nav"} aria-label={t("nav.label")}>
        <strong className="brand">
          <span className="brand-mark" aria-hidden="true">
            T
          </span>
          Ticket-Easy
        </strong>
        <button type="button" className="nav-toggle" aria-expanded={open} aria-label={t("nav.label")} onClick={() => setOpen(!open)}>
          ☰
        </button>
        {NAV.map((item) => (
          <NavLink key={item.to} to={{ pathname: item.to, search: keep }} end={item.end} onClick={() => setOpen(false)}>
            <Icon name={item.icon} />
            <span>{t(item.key)}</span>
            {item.icon === "alerts" && openAlerts > 0 ? (
              <span className="nav-badge" aria-label={`${openAlerts}`}>
                {openAlerts}
              </span>
            ) : null}
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
