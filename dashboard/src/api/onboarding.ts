import { apiPost, apiPut, withQuery } from "./client";

export const ONBOARDING = "/v1/onboarding";

export const STEP_ORDER = ["profile", "knowledge", "guardrails", "connect", "tools", "review"] as const;
export type Step = (typeof STEP_ORDER)[number];

export interface Profile {
  name: string;
  locales: string[];
  default_locale: string;
  tone: "friendly" | "formal" | "neutral";
  order_id_prefix: string;
  order_id_digits: number;
  contacts: string[];
}

export interface Draft {
  tenant_id: string;
  step: Step;
  steps: Step[];
  profile: Profile;
  verify_tool: string | null;
  live: boolean;
  bundle_version: number | null;
  tools: string[];
}

export interface Readiness {
  tenant_id: string;
  documents: { indexed: number; failed: number; waiting: number };
  rules: { approved: number; pending: number; stale: number };
  tools: string[];
  verify_tool: string | null;
  test_messages: number;
  blockers: { code: string; message: string }[];
  ready: boolean;
  live: boolean;
}

export const createBusiness = (profile: Profile) => apiPost<Draft>(ONBOARDING, profile);

export const saveStep = (id: string, step: Step, verifyTool?: string | null) =>
  apiPut<Draft>(`${ONBOARDING}/${id}/step`, { step, ...(verifyTool ? { verify_tool: verifyTool } : {}) });

export const saveProfile = (id: string, profile: Profile) => apiPut<Draft>(`${ONBOARDING}/${id}/profile`, profile);

export const previewPass = (id: string) => apiPost<{ token: string; chat_url: string; live: boolean }>(`${ONBOARDING}/${id}/preview`, {});
export const goLive = (id: string) => apiPost<{ tenant_id: string; live: boolean; bundle_version: number; tools: string[] }>(`${ONBOARDING}/${id}/go-live`, {});

export { withQuery };
