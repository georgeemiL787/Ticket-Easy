import { apiGet, apiPost, withQuery } from "./client";

/** The guided tool setup (the agent /v1/dashboard/tool-setup): three screens over one service. */
export const SETUP = "/v1/dashboard/tool-setup";

export type OperationClass = "read" | "write" | "risky" | "restricted";
export type ItemState =
  | "queued"
  | "preparing"
  | "needs_owner"
  | "approved"
  | "reviewing"
  | "building"
  | "testing"
  | "ready_to_publish"
  | "published"
  | "needs_attention"
  | "skipped";

export interface Effects {
  financial: boolean;
  irreversible: boolean;
  external_side_effects: boolean;
  idempotent: boolean;
}

export interface OperationRow {
  id: string;
  method: string;
  path: string;
  title: string;
  summary: string;
  tags: string[];
  class: OperationClass;
  selectable: boolean;
  why: string[];
  reason_unusable: string | null;
  suggested_effects: Effects;
}

export interface OperationPage {
  spec_id: string;
  total: number;
  matching: number;
  offset: number;
  limit: number;
  counts: Record<OperationClass, number>;
  unusable: number;
  converted_from: string | null;
  diagnostics: { message: string }[];
  operations: OperationRow[];
}

export interface ImportResult {
  business_id: string;
  spec_id: string;
  valid: boolean;
  operations: number;
  usable: number;
  converted_from: string | null;
  problems: string[];
}

export interface BatchItem {
  operation_id: string;
  method: string;
  path: string;
  title: string;
  class: OperationClass;
  state: ItemState;
  message: string | null;
  suggested_effects: Effects;
  proposal_id: string | null;
  artifact_id: string | null;
  tool_name: string | null;
  summary: {
    name: string;
    purpose: string;
    asks_for: string[];
    reads: string[];
    changes: string[];
    risk: string;
    limitations: string[];
    why: string[];
  } | null;
  problems: string[];
  needs_owner: string[];
}

export interface Batch {
  id: string;
  business_id: string;
  spec_id: string;
  busy: boolean;
  settings: { accounts: { name: string; scope: string }[] };
  items: BatchItem[];
  counts: Record<string, number>;
}

export interface SpecSummary {
  id: string;
  filename: string;
  operations: number;
  eligible: number;
  document_valid: boolean | null;
  converted_from: string | null;
  problems: string[];
}

export interface SetupState {
  tenant_id: string;
  business_id: string | null;
  specs: SpecSummary[];
  batches: Batch[];
}

export interface Account {
  name: string;
  scope: "end_user";
  token?: string;
  username?: string;
  password?: string;
  context?: Record<string, string>;
}

export interface Connection {
  base_url?: string;
  fixtures?: { base_url: string; key_ref: string };
  accounts: Account[];
}

export const importSpec = (tenant: string, body: { url: string } | { filename: string; content: string }) =>
  apiPost<ImportResult>(withQuery(`${SETUP}/import`, { tenant_id: tenant }), body);

export const getOperations = (tenant: string, specId: string, params: Record<string, string | number | undefined>) =>
  apiGet<OperationPage>(`${SETUP}/operations`, { tenant_id: tenant, spec_id: specId, ...params });

export const checkConnection = (tenant: string, baseUrl: string) =>
  apiPost<{ reachable: boolean; status?: number; detail?: string }>(withQuery(`${SETUP}/connection-check`, { tenant_id: tenant }), { base_url: baseUrl });

export const createBatch = (
  tenant: string,
  body: { spec_id: string; operation_ids: string[]; verify_operation_id?: string; access: { callers: "customer" | "public"; own_records_only: boolean }; connection: Connection },
) => apiPost<Batch>(withQuery(`${SETUP}/batches`, { tenant_id: tenant }), body);

export const getBatch = (tenant: string, id: string) => apiGet<Batch>(`${SETUP}/batches/${id}`, { tenant_id: tenant });

export const approveBatch = (tenant: string, id: string, proposalIds: string[], confirmations: Record<string, Effects>) =>
  apiPost<Batch>(withQuery(`${SETUP}/batches/${id}/approve`, { tenant_id: tenant }), { proposal_ids: proposalIds, confirmations });

export const retryItem = (tenant: string, id: string, operationId: string) =>
  apiPost<Batch>(withQuery(`${SETUP}/batches/${id}/retry/${operationId}`, { tenant_id: tenant }), {});

export const publishBatch = (tenant: string, id: string) =>
  apiPost<{ batch: Batch; bundle_version: number; tools: string[] }>(withQuery(`${SETUP}/batches/${id}/publish`, { tenant_id: tenant }), {});

export const activateBatch = (tenant: string, id: string, bundleVersion: number) =>
  apiPost<{ bundle_version: number; status: string; tools: string[] }>(withQuery(`${SETUP}/batches/${id}/activate`, { tenant_id: tenant }), {
    bundle_version: bundleVersion,
  });
