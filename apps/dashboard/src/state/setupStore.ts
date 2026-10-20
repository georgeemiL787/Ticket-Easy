import type { Connection } from "../api/toolSetup";

/**
 * What the owner chose on the connect screen, kept for the next screens. Test credentials live in memory only: a reload
 * asks for them again. Everything else (the description, base URL, who uses the tools) also goes to the session storage
 * so a reload keeps the place.
 */
export interface SetupChoice {
  specId: string | null;
  baseUrl: string;
  callers: "customer" | "public";
  ownRecordsOnly: boolean;
  fixtures: { base_url: string; key_ref: string } | null;
  accounts: Connection["accounts"];
}

const EMPTY: SetupChoice = { specId: null, baseUrl: "", callers: "customer", ownRecordsOnly: true, fixtures: null, accounts: [] };
const memory = new Map<string, SetupChoice>();
const key = (tenant: string) => `te.toolsetup.${tenant}`;

export function loadChoice(tenant: string): SetupChoice {
  const live = memory.get(tenant);
  if (live) return live;
  try {
    const stored = window.sessionStorage.getItem(key(tenant));
    if (stored) return { ...EMPTY, ...(JSON.parse(stored) as Partial<SetupChoice>), accounts: [] };
  } catch {
    /* storage can be unavailable: the choice then lives in memory only */
  }
  return { ...EMPTY };
}

export function saveChoice(tenant: string, choice: SetupChoice): void {
  memory.set(tenant, choice);
  try {
    const { accounts: _secrets, ...safe } = choice;
    void _secrets;
    window.sessionStorage.setItem(key(tenant), JSON.stringify(safe));
  } catch {
    /* ignore */
  }
}

export function clearChoice(tenant: string): void {
  memory.delete(tenant);
  try {
    window.sessionStorage.removeItem(key(tenant));
  } catch {
    /* ignore */
  }
}
