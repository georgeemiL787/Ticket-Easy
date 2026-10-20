import { apiPost, withQuery } from "./client";

export const KNOWLEDGE = "/v1/dashboard/knowledge";
export const GUARDRAILS = "/v1/dashboard/guardrails";

export type DocumentStatus = "uploaded" | "parsing" | "indexed" | "failed" | "removed";

export interface KnowledgeDocument {
  document_id: string;
  version: string;
  file: string;
  filename?: string;
  status: DocumentStatus;
  error: string | null;
  current: boolean;
  sections?: number;
  uploaded_at?: string;
}

export interface DocumentList {
  tenant_id: string;
  managed: boolean;
  documents: KnowledgeDocument[];
}

export interface Job {
  job_id: string;
  kind: string;
  status: "running" | "succeeded" | "failed";
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface Rule {
  rule_id: string;
  action: string;
  approval_status: "proposed" | "approved" | "rejected";
  approved_by: string | null;
  stale: boolean;
  source: { document_id: string; version: string; citation: string; quote: string };
  effect: string;
  else_effect: string;
  conditions: { field: string; op: string; value: unknown }[];
  user_message: { ar: string; en: string };
  history: { at: string; event: string; by: string | null }[];
}

export interface RuleList {
  tenant_id: string;
  ruleset: { ruleset_version: string; count: number; approved_rule_ids: string[] };
  rules: Rule[];
}

function readBytes(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

/** Read a chosen file and send it as base64 (the service takes JSON, not multipart). */
export async function uploadDocument(tenant: string, file: File): Promise<KnowledgeDocument> {
  const bytes = new Uint8Array(await readBytes(file));
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return apiPost<KnowledgeDocument>(withQuery(`${KNOWLEDGE}/documents`, { tenant_id: tenant }), { filename: file.name, content_base64: btoa(binary) });
}

export const startReindex = (tenant: string) => apiPost<Job>(withQuery(`${KNOWLEDGE}/reindex`, { tenant_id: tenant }), {});
export const startExtract = (tenant: string) => apiPost<Job>(withQuery(`${GUARDRAILS}/extract`, { tenant_id: tenant }), {});
export const decideRule = (tenant: string, ruleId: string, decision: "approve" | "reject") =>
  apiPost<Rule>(withQuery(`${GUARDRAILS}/${ruleId}/${decision}`, { tenant_id: tenant }), {});
export const editRule = (tenant: string, ruleId: string, changes: Record<string, unknown>) =>
  apiPost<Rule>(withQuery(`${GUARDRAILS}/${ruleId}/edit`, { tenant_id: tenant }), { changes });
