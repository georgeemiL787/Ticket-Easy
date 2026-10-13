import type { ReactNode } from "react";
import type { ApiState } from "../api/useApi";
import { useI18n } from "../i18n";

export function Loading() {
  const { t } = useI18n();
  return (
    <div className="state loading" role="status" aria-live="polite">
      <p className="visually-hidden">{t("common.loading")}</p>
      <div className="skeleton-rows" aria-hidden="true">
        <span className="skeleton wide" />
        <span className="skeleton" />
        <span className="skeleton narrow" />
      </div>
    </div>
  );
}

export function EmptyState({ title, hint, children }: { title?: string; hint?: string; children?: ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="state empty" role="status">
      <p className="state-title">{title ?? t("common.empty")}</p>
      {hint === "" ? null : <p className="muted">{hint ?? t("common.emptyHint")}</p>}
      {children}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useI18n();
  return (
    <div className="state error" role="alert">
      <p className="state-title">{t("common.error")}</p>
      <p>{message}</p>
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          {t("common.retry")}
        </button>
      ) : null}
    </div>
  );
}

interface AsyncProps<T> {
  state: ApiState<T>;
  /** True when the data is there but has nothing to show (a period with no conversations). */
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}

/** Loading, error, empty and loaded views of one request: every page uses it, so none can forget a state. */
export function Async<T>({ state, isEmpty, empty, children }: AsyncProps<T>) {
  if (state.loading && state.data === null) return <Loading />;
  if (state.error) return <ErrorState message={state.error.message} onRetry={state.reload} />;
  if (state.data === null) return <Loading />;
  if (isEmpty?.(state.data)) return <>{empty ?? <EmptyState />}</>;
  return <>{children(state.data)}</>;
}
