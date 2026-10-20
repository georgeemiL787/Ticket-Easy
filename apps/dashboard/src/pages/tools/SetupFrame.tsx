import { useEffect, type ReactNode } from "react";
import { useLightTheme } from "../../components/LightPage";
import { Link, NavLink, useSearchParams } from "react-router-dom";
import { useI18n } from "../../i18n";
import type { TextKey } from "../../i18n/en";
import { sharedQuery } from "../../state/useFilters";

const STEPS: { to: string; key: TextKey }[] = [
  { to: "/tools/connect", key: "ts.steps.connect" },
  { to: "/tools/review", key: "ts.steps.review" },
  { to: "/tools/publish", key: "ts.steps.publish" },
];

/** The frame of the three tool-setup screens: light theme while they are open, and the three steps as a navigation. */
export function SetupFrame({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const keep = sharedQuery(params);
  useLightTheme();
  return (
    <section className="ts-frame">
      {params.get("wizard") ? (
        <p>
          <Link to={`/onboarding/${params.get("wizard")}?tenant=${params.get("wizard")}`}>{"← " + t("ob.back")}</Link>
        </p>
      ) : null}
      <h1>{title}</h1>
      <nav className="ts-steps" aria-label={t("ts.steps.label")}>
        {STEPS.map((step) => (
          <NavLink key={step.to} to={{ pathname: step.to, search: keep }} className={({ isActive }) => (isActive ? "ts-step current" : "ts-step")}>
            {t(step.key)}
          </NavLink>
        ))}
      </nav>
      <p className="muted ts-intro">{intro}</p>
      {children}
    </section>
  );
}

/** The batch is polled while the tools service is working on it. */
export function usePolling(active: boolean, tick: () => void, everyMs = 2000): void {
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(tick, everyMs);
    return () => window.clearInterval(id);
  }, [active, tick, everyMs]);
}
