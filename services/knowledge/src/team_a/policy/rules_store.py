"""JSON-file rule store with the review workflow: proposed -> approved | rejected.

Nothing but an explicit approve() call makes a rule active, and approve() refuses a rule that
would break a guardrail case. Uploading a document or running the extractor only ever adds
`proposed` rules.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from team_a.config import settings
from team_a.schemas import Rule


class RuleNotFound(KeyError):
    pass


class RuleStore:
    def __init__(self, tenant_id: str, path: Path | None = None):
        self.tenant_id = tenant_id
        self.path = path or settings.rules_file(tenant_id)

    def all(self) -> list[Rule]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        rules = [Rule.model_validate(r) for r in raw["rules"]]
        foreign = [r.rule_id for r in rules if r.tenant_id != self.tenant_id]
        if foreign:
            raise ValueError(f"Rules from another tenant in {self.path}: {foreign}")
        return rules

    # ---- history, staleness and the version of the approved rules ----

    @property
    def history_path(self) -> Path:
        return self.path.with_name("rules_history.jsonl")

    def _log(self, event: str, rule_id: str, by: str | None = None, detail: dict | None = None) -> None:
        """An append-only record of every change to the rules: who did what, and when."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, "rule_id": rule_id, "by": by, "detail": detail or {}}
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + chr(10))

    def history(self, rule_id: str | None = None) -> list[dict]:
        if not self.history_path.exists():
            return []
        rows = [json.loads(line) for line in self.history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if rule_id is None or r["rule_id"] == rule_id]

    def current_documents(self) -> set[tuple[str, str]] | None:
        """(document_id, version) of every current document, or None when the tenant has no manifest to compare with."""
        manifest = settings.corpus_dir(self.tenant_id) / "manifest.json"
        if not manifest.exists():
            return None
        docs = json.loads(manifest.read_text(encoding="utf-8")).get("documents", [])
        return {(d["document_id"], d["version"]) for d in docs if d.get("current")}

    def is_stale(self, rule: Rule, current: set[tuple[str, str]] | None = None) -> bool:
        """The document version a rule was derived from is no longer current: the rule is not enforced until a person
        looks at it again. Rules of a tenant with no manifest (or whose source is not a manifest document) are never stale."""
        current = self.current_documents() if current is None else current
        if current is None:
            return False
        known = {d for d, _ in current}
        return rule.source.document_id in known and (rule.source.document_id, rule.source.version) not in current

    def ruleset(self) -> dict:
        """The approved, enforced rules and a version id that changes whenever any of them does."""
        current = self.current_documents()
        enforced = sorted((r for r in self.all() if r.approval_status == "approved" and not self.is_stale(r, current)), key=lambda r: r.rule_id)
        canonical = json.dumps([r.model_dump(mode="json") for r in enforced], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {"tenant_id": self.tenant_id, "ruleset_version": digest[:16], "ruleset_sha256": digest,
                "approved_rule_ids": [r.rule_id for r in enforced], "count": len(enforced)}

    def get(self, rule_id: str) -> Rule:
        for rule in self.all():
            if rule.rule_id == rule_id:
                return rule
        raise RuleNotFound(rule_id)

    def active_for(self, action: str, as_of: date) -> list[Rule]:
        current = self.current_documents()
        return [
            r for r in self.all()
            if r.action == action and r.approval_status == "approved" and r.effective_date <= as_of
            and not self.is_stale(r, current)
        ]

    def save_all(self, rules: list[Rule]) -> None:
        ids = [r.rule_id for r in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate rule_id in rule set")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tenant_id": self.tenant_id, "rules": [r.model_dump(mode="json") for r in rules]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def add_proposed(self, candidates: list[Rule]) -> list[Rule]:
        existing = {r.rule_id: r for r in self.all()}
        added = []
        for rule in candidates:
            if rule.rule_id in existing:
                continue
            added.append(rule.model_copy(update={
                "approval_status": "proposed", "approved_by": None, "approved_at": None,
            }))
        self.save_all([*existing.values(), *added])
        for rule in added:
            self._log("proposed", rule.rule_id, detail={"citation": rule.source.citation, "action": rule.action})
        return added

    def _replace(self, rule_id: str, **changes) -> Rule:
        rules = self.all()
        for i, rule in enumerate(rules):
            if rule.rule_id == rule_id:
                updated = Rule.model_validate({**rule.model_dump(), **changes})
                rules[i] = updated
                self.save_all(rules)
                return updated
        raise RuleNotFound(rule_id)

    def approve(self, rule_id: str, reviewer: str) -> Rule:
        """Approve a rule, unless doing so would break a guardrail case (GuardrailRegression)."""
        from team_a.policy.guardrails import GuardrailRegression, failures_if_approved  # avoids an import cycle

        rules = self.all()
        if rule_id not in {r.rule_id for r in rules}:
            raise RuleNotFound(rule_id)
        trial = [
            r.model_copy(update={"approval_status": "approved", "approved_by": reviewer})
            if r.rule_id == rule_id else r
            for r in rules
        ]
        failures = failures_if_approved(self, rule_id, trial)
        if failures:
            raise GuardrailRegression(rule_id, failures)
        approved = self._replace(
            rule_id,
            approval_status="approved",
            approved_by=reviewer,
            approved_at=datetime.now(timezone.utc),
        )
        self._log("approved", rule_id, reviewer)
        return approved

    def reject(self, rule_id: str, reviewer: str) -> Rule:
        rejected = self._replace(rule_id, approval_status="rejected", approved_by=reviewer, approved_at=None)
        self._log("rejected", rule_id, reviewer)
        return rejected

    def edit(self, rule_id: str, changes: dict) -> Rule:
        """Edit a rule. Any edit sends it back to `proposed` so it must be re-approved."""
        forbidden = {"rule_id", "tenant_id", "approval_status", "approved_by", "approved_at"} & changes.keys()
        if forbidden:
            raise ValueError(f"Cannot edit {sorted(forbidden)} directly")
        edited = self._replace(rule_id, **changes, approval_status="proposed", approved_by=None, approved_at=None)
        self._log("edited", rule_id, detail={"fields": sorted(changes)})
        return edited
