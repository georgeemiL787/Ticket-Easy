import json
from ..config import AppError
from ..grounding import validate_proposal
from ..models import GenerationOutput, canonical_content
from ..persistence.util import uid, now, dump
from ..diagnostics import output_diagnostic, check_errors
from ..llm.budget import input_fits
from .. import capabilities
from ..persistence.repositories import runs as runs_repo


class Generation:
    """Proposal generation from an inventory scope, with one grounding retry, and the area-ordered generation batches."""

    def __init__(self, settings, store, providers, runs, discovery, proposals):
        self.settings, self.store, self.providers = settings, store, providers
        self.runs, self.discovery, self.proposals = runs, discovery, proposals

    def generate(self, spec_id, operation_ids=None, request=None):
        spec = self.discovery.spec(spec_id)
        if spec["inventory"].get("source_kind") == "code":
            raise AppError("legacy_code_inventory", "Local-code inventories are retained read-only; discover the system from its OpenAPI description to generate proposals", 409)
        if not spec["inventory"]["proposal_generation_ready"]:
            raise AppError("inventory_blocked", "No proposal-eligible operation; review discovery diagnostics")
        eligible = [o["id"] for o in spec["inventory"]["operations"] if o.get("proposal_eligible", o.get("supported", True))]
        scope = list(dict.fromkeys(operation_ids)) if operation_ids else eligible
        if not set(scope) <= set(eligible):
            raise AppError("operation_scope", "Selected operations must be proposal-eligible operations of this inventory")
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (spec["business_id"],))
        payload = dict(business=business, inventory=self.proposals.scoped(spec["inventory"], scope), contract_version="1")
        if request:
            payload["owner_request"] = {k: request[k] for k in ("goal", "examples", "clarifications")}
        try:
            return self.generation_attempt(spec, scope, payload, request)
        except AppError as exc:
            rejected = exc.details.pop("proposal", None)
            if exc.code != "invalid_bindings" or rejected is None:
                raise
            earlier = exc.details["run_id"]
            # One automatic retry: the model sees its rejected proposal and exactly what the check found.
            payload = dict(payload, grounding_feedback=dict(previous_proposal=rejected, errors=check_errors(exc.details.get("errors", []), self.settings.model_secrets)))
        try:
            return dict(self.generation_attempt(spec, scope, payload, request), earlier_run_ids=[earlier])
        except AppError as exc:
            exc.details.pop("proposal", None)
            exc.details["earlier_run_ids"] = [earlier]
            raise

    def generation_attempt(self, spec, scope, payload, request):
        run, result = self.runs.model_run(spec["business_id"], "generation", payload, GenerationOutput)
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO generation_batches VALUES(?,?,?,?)", (run, spec["id"], dump(scope), now()))
        try:
            if not result.proposals and not result.capability_gaps:
                raise AppError("empty_model_output", "Model returned neither proposals nor capability gaps")
            result.proposals = [self.runs.tidy(run, canonical_content(p)) for p in result.proposals]
            self.store.diagnostic(run, "normalized_output", output_diagnostic(result.model_dump(), self.settings.model_secrets))
            derived = []
            for index, p in enumerate(result.proposals):
                try:
                    derived.append(validate_proposal(p, spec["inventory"], scope))
                except AppError as exc:
                    exc.details["proposal_index"] = index
                    exc.details["proposal"] = p.model_dump()
                    raise
            for fields in derived:
                fields["generation_scope"] = scope
                fields["generation_capability_gaps"] = [g.model_dump() for g in result.capability_gaps]
                fields["blockers"].extend("Generation capability gap: " + g.explanation for g in result.capability_gaps)
            created, duplicates = list(zip(result.proposals, derived)), []
            if request:
                known = {}
                for t in self.proposals.current_contents(spec["business_id"]):
                    b = capabilities.behavior(t["content"]) if t["state"] not in ("rejected", "superseded") else None
                    if b is not None:
                        known.setdefault(dump(b), t["proposal_id"])
                created = []
                for content, fields in zip(result.proposals, derived):
                    b = capabilities.behavior(content.model_dump())
                    match = known.get(dump(b)) if b is not None else None
                    if match:
                        duplicates.append(match)
                    else:
                        created.append((content, dict(fields, owner_request_id=request["id"])))
            ids = []
            with self.store.connect(write=True) as c:
                for content, fields in created:
                    pid = uid()
                    ids.append(pid)
                    c.execute("INSERT INTO proposals VALUES(?,?,1)",(pid,spec["business_id"]))
                    c.execute("INSERT INTO versions(proposal_id,version,spec_id,content,derived,state,run_id,created_at) VALUES(?,1,?,?,?,'needs_clarification',?,?)",(pid,spec["id"],dump(content.model_dump()),dump(fields),run,now()))
                    self.proposals.settle(c,pid,1,content,fields)
                response = dict(run_id=run,proposal_ids=ids,capability_gaps=[g.model_dump() for g in result.capability_gaps],duplicates=duplicates)
                runs_repo.succeed(c, run, response)
            return response
        except Exception as exc:
            self.runs.fail_run(run,exc)
            raise

    def generation_used(self, spec_id):
        return {i for r in self.store.all("SELECT operation_ids FROM generation_batches WHERE spec_id=?", (spec_id,)) for i in json.loads(r["operation_ids"])}

    def next_generation_batch(self, spec_id):
        """Unused eligible operations of the selected areas, in area order, while the generation input still fits the model."""
        spec, view = self.discovery.spec(spec_id), self.discovery.areas(spec_id)
        inventory = spec["inventory"]
        ok = {o["id"] for o in inventory["operations"] if o.get("proposal_eligible", o.get("supported", True))}
        used, scope = self.generation_used(spec_id), view["included_ids"]
        order = [i for a in view["areas"] for i in a["operation_ids"] if (scope is None or i in scope) and i in ok and i not in used]
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (spec["business_id"],))
        batch = []
        for op_id in order:
            trial = batch + [op_id]
            if not input_fits(self.settings, "generation", dict(business=business, inventory=self.proposals.scoped(inventory, trial), contract_version="1")):
                break
            batch = trial
        return dict(operation_ids=batch, remaining=len(order) - len(batch), used=len(used & ok))
