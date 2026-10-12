import { useEffect, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useI18n } from "../i18n";

/** The light "aurora" theme (team_b/web/shared/aurora-light.css) while a restyled page is open. */
export function useLightTheme(): void {
  useEffect(() => {
    const root = document.documentElement;
    const before = root.getAttribute("data-theme");
    root.setAttribute("data-theme", "aurora-light");
    return () => {
      if (before) root.setAttribute("data-theme", before);
      else root.removeAttribute("data-theme");
    };
  }, []);
}

/** A restyled page: light theme, a title, one line of introduction, then the content. */
export function LightPage({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  useLightTheme();
  const { t } = useI18n();
  const [params] = useSearchParams();
  const wizard = params.get("wizard");
  return (
    <section className="ts-frame">
      {wizard ? (
        <p>
          <Link to={`/onboarding/${wizard}?tenant=${wizard}`}>{"← " + t("ob.back")}</Link>
        </p>
      ) : null}
      <h1>{title}</h1>
      {intro ? <p className="muted ts-intro">{intro}</p> : null}
      {children}
    </section>
  );
}
