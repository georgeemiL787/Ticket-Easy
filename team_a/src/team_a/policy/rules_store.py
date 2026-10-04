"""JSON-file rule store with the review workflow: proposed -> approved | rejected.

Nothing but an explicit approve() call makes a rule active. Uploading a document or running the
extractor only ever adds `proposed` rules.
"""

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

    def get(self, rule_id: str) -> Rule:
        for rule in self.all():
            if rule.rule_id == rule_id:
                return rule
        raise RuleNotFound(rule_id)

    def active_for(self, action: str, as_of: date) -> list[Rule]:
        return [
            r for r in self.all()
            if r.action == action and r.approval_status == "approved" and r.effective_date <= as_of
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
        return self._replace(
            rule_id,
            approval_status="approved",
            approved_by=reviewer,
            approved_at=datetime.now(timezone.utc),
        )

    def reject(self, rule_id: str, reviewer: str) -> Rule:
        return self._replace(rule_id, approval_status="rejected", approved_by=reviewer, approved_at=None)

    def edit(self, rule_id: str, changes: dict) -> Rule:
        """Edit a rule. Any edit sends it back to `proposed` so it must be re-approved."""
        forbidden = {"rule_id", "tenant_id", "approval_status", "approved_by", "approved_at"} & changes.keys()
        if forbidden:
            raise ValueError(f"Cannot edit {sorted(forbidden)} directly")
        return self._replace(rule_id, **changes, approval_status="proposed", approved_by=None, approved_at=None)
