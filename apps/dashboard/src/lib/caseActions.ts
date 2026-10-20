/** Which case buttons make sense for this person: nothing is offered that the service would refuse (same rules as /inbox). */
export interface CaseLike {
  status?: string;
  claimed_by?: string | null;
  pending_approval?: unknown;
}

export interface Allowed {
  claim: boolean;
  release: boolean;
  resolve: boolean;
  returnToAgent: boolean;
  decide: boolean;
}

export function availableActions(c: CaseLike, agent: string): Allowed {
  const me = agent.trim();
  const status = c.status ?? "open";
  const mine = status === "claimed" && me !== "" && c.claimed_by === me;
  return {
    claim: status === "open" && me !== "",
    release: mine,
    resolve: mine,
    returnToAgent: mine,
    decide: mine && Boolean(c.pending_approval),
  };
}

/** "in 5 min" / "5 min overdue", from the seconds left (negative once overdue). */
export function formatRemaining(seconds: number): { text: string; overdue: boolean } {
  const overdue = seconds < 0;
  const size = Math.abs(seconds);
  const text = size < 90 ? `${size} s` : size < 5400 ? `${Math.round(size / 60)} min` : size < 129600 ? `${Math.round(size / 3600)} h` : `${Math.round(size / 86400)} d`;
  return { text, overdue };
}

/** The name the person types is kept in the browser, so they type it once. */
export function rememberedName(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function rememberName(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private window: the name just is not kept */
  }
}
