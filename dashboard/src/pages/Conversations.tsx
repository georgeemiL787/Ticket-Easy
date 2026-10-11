import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiGet, ApiError } from "../api/client";
import type { DashboardConversationPage, DashboardConversationRow } from "../api/types/DashboardConversationPage";
import { useApi } from "../api/useApi";
import { Async, ErrorState } from "../components/Async";
import { labelFor, useI18n } from "../i18n";
import type { TextKey } from "../i18n/en";
import { formatDateTime } from "../lib/dates";
import { sharedQuery, useFilters } from "../state/useFilters";

const PAGE = 50;
const REASONS = [
  "customer_request", "mandatory_risk", "policy_denied", "approval_required", "repeated_tool_failure", "unverified_result",
  "no_evidence", "dependency_unavailable", "low_confidence", "identity_failed", "ownership_mismatch", "high_frustration",
  "capability_missing", "unsupported",
]; // prettier-ignore
const LANGUAGES = ["en", "ar", "mixed", "arabizi"];

/** The badge of a conversation: OK, or how many things a manager may want to look at (the list is in the tooltip). */
export function ProblemBadge({ problems }: { problems: string[] }) {
  const { t } = useI18n();
  if (problems.length === 0) return <span className="badge ok">{t("conv.noProblems")}</span>;
  return (
    <span className="badge problem" title={problems.join("\n")}>
      {problems.length} ⚠ <span className="visually-hidden">{problems.join(", ")}</span>
    </span>
  );
}

function SearchBox() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get("q") ?? "");
  useEffect(() => {
    const timer = setTimeout(() => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (text.trim()) next.set("q", text.trim());
          else next.delete("q");
          return next;
        },
        { replace: true },
      );
    }, 300);
    return () => clearTimeout(timer);
  }, [text, setParams]);
  return (
    <label className="grow">
      <span>{t("common.search")}</span>
      <input type="search" dir="auto" value={text} placeholder={t("conv.searchPlaceholder")} onChange={(e) => setText(e.target.value)} />
    </label>
  );
}

function Select(props: { label: string; param: string; options: { value: string; label: string }[] }) {
  const { t } = useI18n();
  const filters = useFilters();
  const [params] = useSearchParams();
  return (
    <label>
      <span>{props.label}</span>
      <select value={params.get(props.param) ?? ""} onChange={(e) => filters.update({ [props.param]: e.target.value || null })}>
        <option value="">{t("common.all")}</option>
        {props.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function Conversations() {
  const { t, lang } = useI18n();
  const filters = useFilters();
  const [params] = useSearchParams();
  const query = {
    tenant_id: filters.tenant,
    from: filters.window.from,
    to: filters.window.to,
    status: params.get("status") ?? undefined,
    reason: params.get("reason") ?? undefined,
    language: params.get("language") ?? undefined,
    q: params.get("q") ?? undefined,
    limit: PAGE,
  };
  const first = useApi<DashboardConversationPage>("/v1/dashboard/conversations", query);
  // rows loaded after the first page, and where the next page starts; dropped whenever the first page is loaded again
  const [tail, setTail] = useState<{ rows: DashboardConversationRow[]; cursor: string | null } | null>(null);
  const more = tail?.rows ?? [];
  const cursor = tail ? tail.cursor : (first.data?.next_cursor ?? null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    setTail(null);
    setMoreError(null);
  }, [first.data]);

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await apiGet<DashboardConversationPage>("/v1/dashboard/conversations", { ...query, cursor });
      setTail({ rows: [...more, ...page.items], cursor: page.next_cursor ?? null });
      setMoreError(null);
    } catch (cause) {
      setMoreError(cause instanceof ApiError ? cause.message : String(cause));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <>
      <h1>{t("conv.title")}</h1>
      <div className="filters" role="search">
        <SearchBox />
        <Select
          label={t("conv.status")}
          param="status"
          options={[
            { value: "automated", label: t("conv.status.automated") },
            { value: "escalated", label: t("conv.status.escalated") },
          ]}
        />
        <Select label={t("conv.reason")} param="reason" options={REASONS.map((r) => ({ value: r, label: labelFor(t, "reason", r) }))} />
        <Select label={t("conv.language")} param="language" options={LANGUAGES.map((l) => ({ value: l, label: labelFor(t, "lang.value", l) }))} />
      </div>
      <Async state={first} isEmpty={(d) => d.items.length === 0}>
        {(page) => (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {(["conv.time", "conv.language", "conv.intents", "conv.outcome", "conv.reason", "conv.actions", "conv.latency", "conv.problems"] as TextKey[]).map((key) => (
                      <th key={key}>{t(key)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...page.items, ...more].map((row) => (
                    <tr key={row.conversation_id}>
                      <td>
                        <Link to={{ pathname: `/conversations/${encodeURIComponent(row.conversation_id)}`, search: sharedQuery(params) }}>
                          {formatDateTime(row.last_at, lang)}
                        </Link>
                      </td>
                      <td>{labelFor(t, "lang.value", row.language ?? "unknown")}</td>
                      <td>{row.intents.join(", ") || t("common.noData")}</td>
                      <td>{labelFor(t, "decision", row.outcome)}</td>
                      <td>{row.escalation_reason ? labelFor(t, "reason", row.escalation_reason) : t("common.noData")}</td>
                      <td>{row.actions}</td>
                      <td>{Math.round(row.avg_latency_ms)}</td>
                      <td>
                        <ProblemBadge problems={row.problems} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {moreError ? <ErrorState message={moreError} onRetry={loadMore} /> : null}
            {cursor ? (
              <p>
                <button type="button" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? t("common.loading") : t("common.more")}
                </button>
              </p>
            ) : null}
          </>
        )}
      </Async>
    </>
  );
}
