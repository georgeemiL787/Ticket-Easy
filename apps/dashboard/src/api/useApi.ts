import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiGet, type Params } from "./client";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Load `path` with `params` whenever they change. Pass null to wait (for example until a business is chosen). */
export function useApi<T>(path: string | null, params: Params = {}): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  const key = JSON.stringify([path, params, tick]);
  const latest = useRef(key);

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    latest.current = key;
    setLoading(true);
    setError(null);
    apiGet<T>(path, params, controller.signal)
      .then((result) => {
        if (latest.current === key) setData(result);
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        if (latest.current === key) {
          setData(null);
          setError(cause instanceof ApiError ? cause : new ApiError(0, "ERROR", String(cause)));
        }
      })
      .finally(() => {
        if (latest.current === key) setLoading(false);
      });
    return () => controller.abort();
    // `key` stands for path + params + reload counter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}
