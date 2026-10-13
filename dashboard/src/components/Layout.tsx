import { useEffect, useState } from "react";
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

type NavGroup = "monitor" | "configure";

export const NAV: { to: string; key: TextKey; icon: keyof typeof ICONS; end?: boolean; group: NavGroup }[] = [
  { to: "/", key: "nav.overview", icon: "overview", end: true, group: "monitor" },
  { to: "/conversations", key: "nav.conversations", icon: "conversations", group: "monitor" },
  { to: "/escalations", key: "nav.escalations", icon: "escalations", group: "monitor" },
  { to: "/actions", key: "nav.actions", icon: "actions", group: "monitor" },
  { to: "/unanswered", key: "nav.unanswered", icon: "unanswered", group: "monitor" },
  { to: "/alerts", key: "nav.alerts", icon: "alerts", group: "monitor" },
  { to: "/onboarding", key: "nav.onboarding", icon: "onboarding", group: "configure" },
  { to: "/knowledge", key: "nav.knowledge", icon: "knowledge", group: "configure" },
  { to: "/guardrails", key: "nav.guardrails", icon: "guardrails", group: "configure" },
  { to: "/tools", key: "nav.businessTools", icon: "tools", group: "configure" },
];

const GROUPS: { id: NavGroup; label: TextKey }[] = [
  { id: "monitor", label: "nav.monitor" },
  { id: "configure", label: "nav.configure" },
];

/** The Ticket-Easy mark: a ticket-shaped speech bubble with a check. */
export function Logo() {
  return (
    <svg className="logo" viewBox="56 96 400 344" aria-hidden="true" focusable="false">
      <defs>
        <mask id="te-notch">
          <rect x="0" y="0" width="512" height="512" fill="#fff" />
          <circle cx="176" cy="96" r="32" />
          <circle cx="176" cy="376" r="32" />
        </mask>
      </defs>
      <g mask="url(#te-notch)">
        <rect x="56" y="96" width="400" height="280" rx="56" fill="#106f73" />
      </g>
      <path d="M322 376 414 440 396 376Z" fill="#106f73" />
      <g fill="#8fc3c5">
        {Array.from({ length: 9 }, (_, i) => (
          <circle key={i} cx="177" cy={137 + i * 23.5} r="5.5" />
        ))}
      </g>
      <path d="M116 192Q122 230 158 236Q122 242 116 280Q110 242 75 236Q110 230 116 192Z" fill="#ff7456" />
      <path d="M250 238 298 286 392 190" fill="none" stroke="#fff" strokeWidth="34" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Icon({ name }: { name: string }) {
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={ICONS[name]} />
    </svg>
  );
}

function TopBar({ onMenu, menuOpen }: { onMenu: () => void; menuOpen: boolean }) {
  const { t } = useI18n();
  const filters = useFilters();
  const tenants = useApi<DashboardTenants>("/v1/dashboard/tenants");
  const auth = useAuth();
  const choices = tenants.data?.tenants ?? [{ tenant_id: filters.tenant, display_name: filters.tenant, default_locale: "en" }];
  return (
    <header className="topbar">
      <button type="button" className="menu-button" aria-expanded={menuOpen} aria-label={t("nav.menu")} onClick={onMenu}>
        <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>
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
        <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M20 12a8 8 0 1 1-2.4-5.7M20 4v5h-5" />
        </svg>
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

function readCollapsed(): boolean {
  try {
    return localStorage.getItem("te.nav.collapsed") === "1";
  } catch {
    return false;
  }
}

export function Layout() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const filters = useFilters();
  const keep = sharedQuery(params);
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleCollapsed = () => {
    setCollapsed((value) => {
      try {
        localStorage.setItem("te.nav.collapsed", value ? "0" : "1");
      } catch {
        /* private mode: the choice just is not remembered */
      }
      return !value;
    });
  };
  // the number of open alerts, so a problem is seen from any page; a failed call just shows no number
  const alerts = useApi<{ open_count: number }>("/v1/dashboard/alerts", { tenant_id: filters.tenant, status: "open", limit: 1 });
  const openAlerts = alerts.data?.open_count ?? 0;
  // on a narrow screen a table is shown as a list of cards: each cell carries its column's title
  useEffect(() => {
    const root = document.getElementById("content");
    if (!root) return;
    let frame = 0;
    const label = () => {
      frame = 0;
      root.querySelectorAll("table").forEach((table) => {
        const heads = Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent ?? "");
        if (heads.length === 0) return;
        table.querySelectorAll("tbody tr").forEach((row) => {
          Array.from(row.children).forEach((cell, i) => {
            if (!cell.hasAttribute("data-label") && heads[i]) cell.setAttribute("data-label", heads[i]);
          });
        });
      });
    };
    const watch = new MutationObserver(() => {
      if (!frame) frame = requestAnimationFrame(label);
    });
    watch.observe(root, { childList: true, subtree: true });
    label();
    return () => {
      watch.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <div className={collapsed ? "shell collapsed" : "shell"}>
      <nav className={open ? "nav open" : "nav"} aria-label={t("nav.label")}>
        <div className="nav-head">
          <strong className="brand">
            <Logo />
            <span className="brand-name">Ticket-Easy</span>
          </strong>
        </div>
        <div className="nav-scroll">
          {GROUPS.map((group) => (
            <div className="nav-group" key={group.id}>
              <p className="nav-label">{t(group.label)}</p>
              {NAV.filter((item) => item.group === group.id).map((item) => (
                <NavLink
                  key={item.to}
                  to={{ pathname: item.to, search: keep }}
                  end={item.end}
                  title={collapsed ? t(item.key) : undefined}
                  onClick={() => setOpen(false)}
                >
                  <Icon name={item.icon} />
                  <span className="nav-text">{t(item.key)}</span>
                  {item.icon === "alerts" && openAlerts > 0 ? (
                    <span className="nav-badge" aria-label={`${openAlerts}`}>
                      {openAlerts}
                    </span>
                  ) : null}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
        <button
          type="button"
          className="nav-collapse"
          aria-pressed={collapsed}
          aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
          title={collapsed ? t("nav.expand") : t("nav.collapse")}
          onClick={toggleCollapsed}
        >
          <svg className="nav-icon collapse-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
      </nav>
      {open ? <div className="scrim" aria-hidden="true" onClick={() => setOpen(false)} /> : null}
      <div className="main">
        <TopBar onMenu={() => setOpen(!open)} menuOpen={open} />
        <main id="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
