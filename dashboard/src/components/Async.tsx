import type { ReactNode } from "react";
import type { ApiState } from "../api/useApi";
import { useI18n } from "../i18n";

export function Loading() {
  const { t } = useI18n();
  return (
    <p className="state" role="status" aria-live="polite">
      {t("common.loading")}
    </p>
  );
}

export function EmptyState({ title, hint }: { title?: string; hint?: string }) {
  const { t } = useI18n();
  return (
    <div className="state empty" role="status">
      <p className="state-title">{title ?? t("common.empty")}</p>
      <p className="muted">{hint ?? t("common.emptyHint")}</p>
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
