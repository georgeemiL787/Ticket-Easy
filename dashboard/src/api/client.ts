/** A failed call to the Team B service, with the service's own error code and message when it sent them. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let csrfToken: string | null = null;

/** The token the service gave at sign-in; it goes in X-CSRF-Token on every call that changes something. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** A 401 on a protected call means the session is gone: the page goes back to the login. */
function sessionGone(status: number, path: string): void {
  if (status === 401 && !path.startsWith("/v1/auth/")) window.dispatchEvent(new Event("tb-signed-out"));
}

export type Params = Record<string, string | number | undefined | null>;

export function withQuery(path: string, params: Params = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

async function failure(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } };
    return new ApiError(response.status, body.error?.code ?? "ERROR", body.error?.message ?? `HTTP ${response.status}`);
  } catch {
    return new ApiError(response.status, "ERROR", `HTTP ${response.status}`);
  }
}

export async function apiGet<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(withQuery(path, params), { signal, headers: { Accept: "application/json" } });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "NETWORK", "Cannot reach the server. Check the connection and try again.");
  }
  if (!response.ok) {
    sessionGone(response.status, path);
    throw await failure(response);
  }
  return (await response.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "NETWORK", "Cannot reach the server. Check the connection and try again.");
  }
  if (!response.ok) {
    sessionGone(response.status, path);
    throw await failure(response);
  }
  return (await response.json()) as T;
}
