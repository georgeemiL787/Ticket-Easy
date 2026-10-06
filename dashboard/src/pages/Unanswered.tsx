import { useApi } from "../api/useApi";
import type { DashboardKnowledgeGaps } from "../api/types/DashboardKnowledgeGaps";
import { Async } from "../components/Async";
import { useI18n } from "../i18n";
import { formatDateTime } from "../lib/dates";
import { useFilters } from "../state/useFilters";

export default function Unanswered() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const state = useApi<DashboardKnowledgeGaps>("/v1/dashboard/knowledge-gaps", {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
  });
  return (
    <>
      <h1>{t("gap.title")}</h1>
      <Async state={state} isEmpty={(d) => d.groups.length === 0}>
        {(d) => (
          <>
            <p className="muted">
              {t("gap.total")}: {d.questions_without_answer}. {t("gap.hint")}
            </p>
            <table>
              <thead>
                <tr>
                  <th>{t("gap.question")}</th>
                  <th>{t("gap.count")}</th>
                  <th>{t("gap.lastSeen")}</th>
                </tr>
              </thead>
              <tbody>
                {d.groups.map((group) => (
                  <tr key={group.question}>
                    <td dir="auto">{group.question}</td>
                    <td>{group.count}</td>
                    <td>{formatDateTime(group.last_seen, lang)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Async>
    </>
  );
}
