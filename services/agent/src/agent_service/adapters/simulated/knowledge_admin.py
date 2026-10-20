"""A simulated for the knowledge service's onboarding API, in memory: documents with statuses, a re-index that always
succeeds at
once, and rules that are proposed, approved or rejected by a named reviewer. Tests and the simulated mode use it, so
nothing needs the knowledge service.

It cannot read pdf, docx or xlsx files for real: it only checks their signature. It has no model, so `extract_rules`
proposes nothing; tests add proposals with `propose`.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from agent_service.contracts.errors import UpstreamError

SERVICE = "knowledge"
TEXT = {".md", ".markdown", ".txt", ".html", ".htm"}
SIGNATURES = {".pdf": b"%PDF", ".docx": b"PK", ".xlsx": b"PK"}


def _refused(message: str, code: str = "INVALID_REQUEST") -> UpstreamError:
    return UpstreamError(SERVICE, code, message)


class SimulatedKnowledgeAdmin:
    def __init__(self) -> None:
        self._documents: dict[str, list[dict[str, Any]]] = {}
        self._rules: dict[str, dict[str, dict[str, Any]]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self.fail_next: str | None = None  # an UpstreamError code the next call raises (tests)

    def _check_fail(self) -> None:
        if self.fail_next:
            code, self.fail_next = self.fail_next, None
            raise UpstreamError(
                SERVICE, code, "The knowledge service is not available (simulated switch)", retryable=True
            )

    # ---- documents ----

    async def upload(
        self, tenant_id: str, filename: str, content: bytes, document_id: str | None = None
    ) -> dict[str, Any]:
        self._check_fail()
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in TEXT and suffix not in SIGNATURES:
            raise _refused(f"unsupported file type {suffix or '(none)'}")
        if not content:
            raise _refused("the file is empty")
        doc_id = document_id or filename.rsplit(".", 1)[0].lower().replace(" ", "_")
        error = None
        if suffix in TEXT:
            if not content.decode("utf-8", errors="replace").strip():
                error = "no readable text was found in this file"
        elif not content.startswith(SIGNATURES[suffix]):
            error = "the file could not be opened: it is not a valid " + suffix[1:] + " file"
        records = self._documents.setdefault(tenant_id, [])
        version = 1 + max((r["version_number"] for r in records if r["document_id"] == doc_id), default=0)
        if error is None:
            for r in records:
                if r["document_id"] == doc_id:
                    r["current"] = False
        record = {
            "document_id": doc_id,
            "version_number": version,
            "version": f"v{version}",
            "file": f"{doc_id}.v{version}{suffix}",
            "filename": filename,
            "status": "failed" if error else "uploaded",
            "error": error,
            "current": error is None,
            "size": len(content),
            "uploaded_at": datetime.now(UTC).isoformat(),
        }
        records.append(record)
        return dict(record)

    async def documents(self, tenant_id: str) -> dict[str, Any]:
        self._check_fail()
        rows = sorted(self._documents.get(tenant_id, []), key=lambda r: r["uploaded_at"], reverse=True)
        return {"tenant_id": tenant_id, "managed": True, "documents": [dict(r) for r in rows]}

    async def remove(self, tenant_id: str, document_id: str) -> dict[str, Any]:
        hit = [
            r
            for r in self._documents.get(tenant_id, [])
            if r["document_id"] == document_id and r["status"] != "removed"
        ]
        if not hit:
            raise _refused(f"no document '{document_id}'", "NOT_FOUND")
        for r in hit:
            r["status"], r["current"] = "removed", False
        return {"document_id": document_id, "versions_removed": len(hit), "reindex_needed": True}

    async def reindex(self, tenant_id: str) -> dict[str, Any]:
        self._check_fail()
        records = self._documents.get(tenant_id, [])
        if not any(r["current"] for r in records):
            raise _refused("there is no current document to index", "INVALID_STATE")
        for r in records:
            if r["status"] == "uploaded":
                r["status"] = "indexed"
        passages = sum(1 for r in records if r["current"])
        return self._job(
            "reindex",
            tenant_id,
            {"passages": passages, "current_passages": passages, "embeddings": False, "failed": []},
        )

    def _job(self, kind: str, tenant_id: str, result: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        job: dict[str, Any] = {
            "job_id": uuid.uuid4().hex,
            "kind": kind,
            "tenant_id": tenant_id,
            "status": "succeeded",
            "started_at": now,
            "finished_at": now,
            "result": result,
            "error": None,
        }
        self._jobs[str(job["job_id"])] = job
        return dict(job)

    async def job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            raise _refused(f"No job '{job_id}'", "NOT_FOUND")
        return dict(job)

    # ---- rules ----

    def propose(
        self, tenant_id: str, rule_id: str, quote: str, action: str = "create_refund", citation: str = "doc@v1#s1"
    ) -> dict[str, Any]:
        """Test helper: a rule the model would have proposed."""
        rule: dict[str, Any] = {
            "rule_id": rule_id,
            "tenant_id": tenant_id,
            "action": action,
            "approval_status": "proposed",
            "approved_by": None,
            "source": {"document_id": citation.split("@")[0], "version": "v1", "citation": citation, "quote": quote},
            "effect": "allow",
            "else_effect": "deny",
            "conditions": [],
            "applies_if": [],
            "user_message": {"ar": "", "en": quote},
        }
        self._rules.setdefault(tenant_id, {})[rule_id] = rule
        self._log(tenant_id, "proposed", rule_id, None)
        return dict(rule)

    def _log(self, tenant_id: str, event: str, rule_id: str, by: str | None) -> None:
        self._history.setdefault(tenant_id, []).append(
            {"at": datetime.now(UTC).isoformat(), "event": event, "rule_id": rule_id, "by": by, "detail": {}}
        )

    async def extract_rules(self, tenant_id: str, document_id: str | None = None) -> dict[str, Any]:
        self._check_fail()
        return self._job(
            "extract", tenant_id, {"added": 0, "rule_ids": [], "note": "simulated service: no model proposes rules"}
        )

    def _ruleset(self, tenant_id: str) -> dict[str, Any]:
        approved = sorted(
            (r for r in self._rules.get(tenant_id, {}).values() if r["approval_status"] == "approved"),
            key=lambda r: r["rule_id"],
        )
        digest = hashlib.sha256(json.dumps(approved, sort_keys=True).encode()).hexdigest()
        return {
            "tenant_id": tenant_id,
            "ruleset_version": digest[:16],
            "ruleset_sha256": digest,
            "approved_rule_ids": [r["rule_id"] for r in approved],
            "count": len(approved),
        }

    async def rules(self, tenant_id: str) -> dict[str, Any]:
        self._check_fail()
        history = self._history.get(tenant_id, [])
        rows = [
            dict(r, stale=False, history=[h for h in history if h["rule_id"] == r["rule_id"]][-5:])
            for r in self._rules.get(tenant_id, {}).values()
        ]
        return {"tenant_id": tenant_id, "ruleset": self._ruleset(tenant_id), "rules": rows}

    def _rule(self, tenant_id: str, rule_id: str) -> dict[str, Any]:
        rule = self._rules.get(tenant_id, {}).get(rule_id)
        if rule is None:
            raise _refused(f"No rule '{rule_id}'", "NOT_FOUND")
        return rule

    async def approve_rule(self, tenant_id: str, rule_id: str, reviewer: str) -> dict[str, Any]:
        rule = self._rule(tenant_id, rule_id)
        rule.update(approval_status="approved", approved_by=reviewer)
        self._log(tenant_id, "approved", rule_id, reviewer)
        return dict(rule)

    async def reject_rule(self, tenant_id: str, rule_id: str, reviewer: str) -> dict[str, Any]:
        rule = self._rule(tenant_id, rule_id)
        rule.update(approval_status="rejected", approved_by=reviewer)
        self._log(tenant_id, "rejected", rule_id, reviewer)
        return dict(rule)

    async def edit_rule(self, tenant_id: str, rule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        rule = self._rule(tenant_id, rule_id)
        forbidden = {"rule_id", "tenant_id", "approval_status", "approved_by", "approved_at"} & changes.keys()
        if forbidden:
            raise _refused(f"Cannot edit {sorted(forbidden)} directly")
        rule.update(changes, approval_status="proposed", approved_by=None)
        self._log(tenant_id, "edited", rule_id, None)
        return dict(rule)

    async def ruleset(self, tenant_id: str) -> dict[str, Any]:
        self._check_fail()
        return self._ruleset(tenant_id)

    async def snapshot(self, tenant_id: str) -> dict[str, Any]:
        docs = await self.documents(tenant_id)
        ruleset = self._ruleset(tenant_id)
        return {
            "knowledge": {
                "provider": "simulated",
                "index_tenant": tenant_id,
                "documents": [
                    {"document_id": d["document_id"], "version": d["version_number"], "status": d["status"]}
                    for d in docs["documents"]
                    if d["current"]
                ],
            },
            "guardrails": {
                "ruleset_version": ruleset["ruleset_version"],
                "ruleset_sha256": ruleset["ruleset_sha256"],
                "approved_rule_ids": ruleset["approved_rule_ids"],
            },
        }
