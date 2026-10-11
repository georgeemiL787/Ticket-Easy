import { useEffect } from "react";
import { useApi } from "../api/useApi";
import type { DashboardPassage } from "../api/types/DashboardPassage";
import { useI18n } from "../i18n";
import { Async } from "./Async";

/** The words of a policy passage, opened from a citation in the decision timeline. Escape or the button closes it. */
export function PassageDialog({ tenant, citation, onClose }: { tenant: string; citation: string; onClose: () => void }) {
  const { t } = useI18n();
  const state = useApi<DashboardPassage>("/v1/dashboard/passage", { tenant_id: tenant, citation });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="backdrop" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={`${t("detail.passage")} ${citation}`} onClick={(e) => e.stopPropagation()}>
        <h2>{citation}</h2>
        <Async state={state}>
          {(p) => (
            <>
              <p className="muted small">
                {p.document_id} · {p.version} · {p.section}
              </p>
              <blockquote dir="auto">{p.text}</blockquote>
            </>
          )}
        </Async>
        <p>
          <button type="button" autoFocus onClick={onClose}>
            {t("detail.closePassage")}
          </button>
        </p>
      </div>
    </div>
  );
}
