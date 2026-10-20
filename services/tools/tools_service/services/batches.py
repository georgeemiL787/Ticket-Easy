"""Tool batches: the owner picks operations, the tools service prepares one tool per operation, the owner confirms, and the tools are
built, tested and published together.

A batch runs the same steps an owner takes one by one in the review pages (request a tool, answer the access questions,
accept the access policy, check the answers, approve, build, test), but without a page per step. Nothing is decided for
the owner: preparing stops at `needs_owner` with a short summary, approving needs the owner's confirmations (for a tool
that changes data, the four effect flags, prefilled from the operation and never accepted silently), and every decision is
recorded in the owner's name. Test accounts and credentials are stored sealed (secretbox) and never returned.

Item states: queued, preparing, needs_owner, approved, reviewing, building, testing, ready_to_publish, published,
needs_attention (with the reason and what to do), skipped.
"""
import json
import threading

from ..classify import operation_class
from ..compiler.policies import AccessPolicy, PolicySubmission
from ..config import AppError
from ..connectors import SANDBOX, base_url as clean_base_url, load_connectors, environment_of
from ..models import SupersedeSubmission, AnswerSubmission, ArtifactBuildSubmission, DecisionSubmission, PublicationSubmission, ReviewSubmission, ToolRequestSubmission
from ..persistence.util import digest, dump, now, uid
from ..secretbox import open_value, seal_value

TERMINAL = {"needs_owner", "ready_to_publish", "published", "needs_attention", "skipped"}
EFFECT_FLAGS = ("financial", "irreversible", "external_side_effects", "idempotent")
CALLERS = {"customer": "end_user", "public": "public"}
MAX_RECONCILIATIONS = 5


def goal_text(op, info, verify):
    """A plain request for the tool, written from the operation, so the model needs no other context."""
    label = op.get("business_label") or op.get("summary") or f'{op["method"]} {op["path"]}'
    caller = "a logged-in customer"
    if info["class"] == "read":
        return f'Let {caller} use this: {label} ({op["method"]} {op["path"]}). Read only; change nothing.'
    first = f' First look up the record with {verify["method"]} {verify["path"]} to confirm it belongs to the caller, then' if verify else ""
    return f'Let {caller} do this: {label} ({op["method"]} {op["path"]}).{first} Make the change and return the reference and status.'


def owner_statement(access, actor):
    """The owner's answer to the access questions, from the choices made on the connection screen."""
    who = "a logged-in customer authenticated with their own credential" if access.get("callers") == "customer" else "anyone, without signing in"
    own = " A customer may only use their own records; another customer's record must be refused." if access.get("callers") == "customer" and access.get("own_records_only", True) else ""
    return f"Owner decision recorded for {actor}: the caller is {who}.{own} No administrator or staff access, no service account."


class Batches:
    def __init__(self, svc):
        self.svc = svc
        self.settings, self.store = svc.settings, svc.store
        self.locks = {}

    # ---- storage ----

    def _lock(self, batch_id):
        return self.locks.setdefault(batch_id, threading.Lock())

    def _load(self, batch_id):
        row = self.store.one("SELECT * FROM tool_batches WHERE id=?", (batch_id,))
        row["content"] = json.loads(row["content"])
        return row

    def _save(self, batch_id, content):
        with self.store.connect(write=True) as c:
            c.execute("UPDATE tool_batches SET content=?,updated_at=? WHERE id=?", (dump(content), now(), batch_id))

    def _update(self, batch_id, index, **fields):
        with self._lock(batch_id):
            row = self._load(batch_id)
            row["content"]["items"][index].update(fields)
            self._save(batch_id, row["content"])

    def view(self, batch_id):
        """The batch for the owner: items with state and summary. Credentials are never part of it."""
        row = self._load(batch_id)
        content = row["content"]
        settings = {k: v for k, v in content["settings"].items() if k != "credentials"}
        settings["accounts"] = [dict(name=a["name"], scope=a["scope"], context=a.get("context") or {}) for a in content["settings"].get("credentials") or []]  # names and trusted ids, never secrets
        items = content["items"]
        busy = any(i["state"] in ("queued", "preparing", "approved", "reviewing", "building", "testing") for i in items)
        return dict(id=row["id"], business_id=row["business_id"], spec_id=row["spec_id"], created_at=row["created_at"], updated_at=row["updated_at"],
                    settings=settings, items=items, busy=busy,
                    counts={s: sum(1 for i in items if i["state"] == s) for s in sorted({i["state"] for i in items})})

    def batches(self, business_id):
        return [self.view(r["id"]) for r in self.store.all("SELECT id FROM tool_batches WHERE business_id=? ORDER BY created_at DESC", (business_id,))]

    # ---- 1. create and prepare ----

    def create(self, business_id, spec_id, operation_ids, verify_operation_id=None, access=None, connection=None, actor=None):
        spec = self.svc.request_spec(business_id, spec_id)
        ops = {o["id"]: o for o in spec["inventory"]["operations"]}
        ids = list(dict.fromkeys(operation_ids))
        if not ids:
            raise AppError("batch_empty", "Choose at least one operation")
        if len(ids) > 25:
            raise AppError("batch_limit", "Choose at most 25 operations at a time")
        unknown = [i for i in ids if i not in ops]
        if unknown:
            raise AppError("batch_unknown_operation", "Some chosen operations are not part of this API description", 422)
        if verify_operation_id and verify_operation_id not in ops:
            raise AppError("batch_unknown_operation", "The record-check operation is not part of this API description", 422)
        access = dict(callers=(access or {}).get("callers", "customer"), own_records_only=bool((access or {}).get("own_records_only", True)))
        if access["callers"] not in CALLERS:
            raise AppError("batch_access", "Choose who uses the tools: customer or public", 422)
        connection = self.connection_settings(connection or {})
        items = []
        for op_id in ids:
            info = operation_class(ops[op_id])
            items.append(dict(operation_id=op_id, method=ops[op_id]["method"], path=ops[op_id]["path"], title=ops[op_id].get("business_label") or ops[op_id].get("summary") or "",
                              **{"class": info["class"]}, state="queued" if info["selectable"] else "skipped", message=None if info["selectable"] else (info["reason_unusable"] or "restricted"),
                              suggested_effects=info["suggested_effects"], proposal_id=None, request_id=None, artifact_id=None, publication_id=None, tool_name=None,
                              summary=None, problems=[], needs_owner=[]))
        batch_id = uid()
        content = dict(settings=dict(access=access, verify_operation_id=verify_operation_id, actor=actor or self.settings.dev_reviewer_id, **connection), items=items)
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO tool_batches(id,business_id,spec_id,content,created_at,updated_at) VALUES(?,?,?,?,?,?)", (batch_id, business_id, spec["id"], dump(content), now(), now()))
        if any(i["state"] == "queued" for i in items):
            self.run_in_background(self.prepare_all, batch_id)
        return self.view(batch_id)

    def connection_settings(self, connection):
        """The base URL, optional test-fixture endpoints and test accounts. Credentials are sealed before they are stored."""
        out = {}
        if connection.get("base_url"):
            out["base_url"] = clean_base_url(connection["base_url"])
        fixtures = connection.get("fixtures")
        if fixtures:
            out["fixtures"] = dict(base_url=clean_base_url(fixtures["base_url"]), key_ref=str(fixtures["key_ref"]))
        accounts = []
        for account in connection.get("accounts") or []:
            secret = {k: seal_value(account[k], self.settings) for k in ("token", "username", "password") if account.get(k)}
            accounts.append(dict(name=str(account["name"]), scope=str(account.get("scope", "end_user")), context=dict(account.get("context") or {}), **secret))
        out["credentials"] = accounts
        return out

    def run_in_background(self, target, *args):
        threading.Thread(target=self._guard, args=(target, *args), daemon=True).start()

    def _guard(self, target, batch_id, *args):
        try:
            target(batch_id, *args)
        except Exception:  # every item records its own failure; this only keeps the thread quiet
            pass

    def prepare_all(self, batch_id):
        row = self._load(batch_id)
        for index, item in enumerate(row["content"]["items"]):
            if item["state"] == "queued":
                self.prepare(batch_id, index)

    def prepare(self, batch_id, index):
        row = self._load(batch_id)
        content, item = row["content"], row["content"]["items"][index]
        spec = self.svc.spec(row["spec_id"])
        ops = {o["id"]: o for o in spec["inventory"]["operations"]}
        op, info = ops[item["operation_id"]], operation_class(ops[item["operation_id"]])
        verify = ops.get(content["settings"].get("verify_operation_id") or "")
        if verify is not None and verify["id"] == op["id"]:
            verify = None
        if info["class"] == "read":
            verify = None
        self._update(batch_id, index, state="preparing", message=None)
        try:
            scope = [verify["id"], op["id"]] if verify else [op["id"]]
            submission = ToolRequestSubmission(goal=goal_text(op, info, verify), examples="", spec_id=row["spec_id"], idempotency_key=f"batch:{batch_id}:{op['id']}"[:200])
            request = self.svc.request_tool(row["business_id"], submission, source="batch", scope=scope)
            ids = request.get("proposal_ids") or request.get("existing_proposal_ids") or []
            if not ids:
                reasons = [u.get("description") for u in request.get("unresolved") or [] if u.get("description")]
                self._update(batch_id, index, state="needs_attention", request_id=request["id"], message="No tool could be prepared for this operation.", problems=reasons[:5])
                return
            self._update(batch_id, index, state="needs_owner", request_id=request["id"], proposal_id=ids[0], summary=self.summary(ids[0], info), message=None)
        except AppError as exc:
            self._update(batch_id, index, state="needs_attention", message=exc.message, problems=[exc.code])
        except Exception:
            self._update(batch_id, index, state="needs_attention", message="Preparing this tool stopped unexpectedly. Try again.", problems=["internal_error"])

    def summary(self, pid, info):
        """What the owner reads before confirming: the tool's name and purpose, what it takes in and what it reads or changes."""
        view = self.svc.view(pid)
        content = view["content"]
        return dict(name=content["name"], purpose=content["business_purpose"], description=content["description"], risk=content["risk"],
                    asks_for=view.get("interface_preview", {}).get("arguments", []), reads=content["expected_reads"], changes=content["expected_writes"],
                    questions=len(content["questions"]), limitations=content["limitations"], why=info["why"])

    def retry(self, batch_id, operation_id):
        row = self._load(batch_id)
        for index, item in enumerate(row["content"]["items"]):
            if item["operation_id"] == operation_id and item["state"] in ("needs_attention",):
                self._update(batch_id, index, state="queued", message=None, problems=[], proposal_id=None)
                self.run_in_background(lambda b: self.prepare(b, index), batch_id)
                return self.view(batch_id)
        raise AppError("batch_item", "No such tool in this batch that can be retried", 404)

    # ---- 2. approve: review, build, test ----

    def approve(self, batch_id, proposal_ids, confirmations, actor=None):
        """Start review, build and tests for the chosen tools. A tool that changes data needs the owner's four effect flags in
        `confirmations[proposal_id]` (financial, irreversible, external_side_effects, idempotent), each true or false."""
        row = self._load(batch_id)
        actor = actor or self.settings.dev_reviewer_id
        chosen = []
        for index, item in enumerate(row["content"]["items"]):
            if item["proposal_id"] not in proposal_ids:
                continue
            if item["state"] != "needs_owner":
                raise AppError("batch_state", f'"{item.get("title") or item["path"]}" is {item["state"]}; only tools waiting for you can be approved', 409)
            if item["class"] != "read":
                flags = (confirmations or {}).get(item["proposal_id"]) or {}
                missing = [k for k in EFFECT_FLAGS if not isinstance(flags.get(k), bool)]
                if missing:
                    raise AppError("effects_unconfirmed", "Confirm what this tool does before approving it: " + ", ".join(missing), 422, dict(proposal_id=item["proposal_id"], missing=missing))
            chosen.append(index)
        if not chosen:
            raise AppError("batch_empty", "Choose at least one tool that is waiting for you", 422)
        for index in chosen:
            self._update(batch_id, index, state="approved", message=None, confirmed_by=actor, confirmed_effects=(confirmations or {}).get(row["content"]["items"][index]["proposal_id"]))
        self.run_in_background(self.work_approved, batch_id, actor)
        return self.view(batch_id)

    def work_approved(self, batch_id, actor):
        row = self._load(batch_id)
        for index, item in enumerate(row["content"]["items"]):
            if item["state"] == "approved":
                self.build_one(batch_id, index, actor)

    def build_one(self, batch_id, index, actor):
        row = self._load(batch_id)
        content, item = row["content"], row["content"]["items"][index]
        pid = item["proposal_id"]
        try:
            self._update(batch_id, index, state="reviewing")
            self.review_to_approval(pid, content, item, actor)
            self._update(batch_id, index, state="building")
            aid = self.build(pid, row, content, actor)
            self._update(batch_id, index, state="testing", artifact_id=aid)
            result = self.svc.auto_test(aid, False, False)
            if result["outcome"] in ("ready_to_publish", "published"):
                self._update(batch_id, index, state="ready_to_publish", needs_owner=[], problems=[])
            else:
                asks = [a["question"] for a in result.get("needs_owner") or []]
                self._update(batch_id, index, state="needs_attention", message="The automatic tests need something from you.", needs_owner=asks, problems=result.get("problems") or [])
        except AppError as exc:
            self._update(batch_id, index, state="needs_attention", message=exc.message, problems=[exc.code])
        except Exception:
            self._update(batch_id, index, state="needs_attention", message="This tool stopped on an unexpected error. Nothing was published.", problems=["internal_error"])

    def review_to_approval(self, pid, content, item, actor):
        svc, access = self.svc, content["settings"]["access"]
        view = svc.view(pid)
        statement = owner_statement(access, actor)
        if view["state"] != "approved_to_build":
            # A question about a field the API only returns cannot be bound or configured; access and ownership are covered by the
            # server-derived requirements, so the question is set aside with the reason kept in the history.
            for note in {n["question_id"]: n for n in view.get("question_review") or []}.values():
                current = svc.view(pid)
                if any(q["id"] == note["question_id"] and not q.get("configuration_key") for q in current["content"]["questions"]):
                    svc.supersede_question(pid, current["version"], note["question_id"], SupersedeSubmission(
                        expected_revision=current["review_revision"], reason=f'{note["field"]} is only returned by the API; access and ownership are covered by the access requirements.'))
            view = svc.view(pid)
            answers = {q["id"]: statement for q in view["content"]["questions"]}
            if not (view.get("policy") or {}).get("accepted_at"):
                answers.update({r["id"]: statement for r in view["requirements"]})
            if answers:
                svc.answers(pid, view["version"], AnswerSubmission(expected_revision=view["review_revision"], answers=answers))
            # Checking the answers can revise the proposal, and an accepted policy belongs to one version: check first, then decide access.
            checked = svc.view(pid)
            svc.reconcile(pid, checked["version"], checked["review_revision"])
            self.accept_policy(pid, row_spec(self, pid), content, item, statement)
            view = self.reconcile_until_ready(pid, statement)
            for requirement in view.get("requirements", []):
                if requirement.get("status") == "answer_sufficient":
                    current = svc.view(pid)
                    svc.confirm_requirement(pid, current["version"], requirement["id"], ReviewSubmission(expected_revision=current["review_revision"]))
            view = svc.view(pid)
            svc.decide(pid, view["version"], DecisionSubmission(expected_revision=view["review_revision"], action="approve_to_build",
                                                              reason=f"Approved by {actor} in the batch review", idempotency_key=f"batch-{pid}-{view['version']}"[:200]))

    def accept_policy(self, pid, spec_id, content, item, statement):
        svc, access = self.svc, content["settings"]["access"]
        view = svc.view(pid)
        if view.get("policy") and view["policy"].get("accepted_at"):
            return
        inventory = svc.spec(spec_id)["inventory"]
        ops = {o["id"]: o for o in inventory["operations"]}
        steps = view["content"]["steps"]
        reads = [s for s in steps if (ops.get(s["operation_id"]) or {}).get("method") == "GET"]
        checks, scopes = [], []
        customer = access["callers"] == "customer"
        suggested = {x["requirement_id"]: x for x in view.get("policy_check_suggestions") or []}  # ownership checks found in the API description
        for req in [r for r in view["requirements"] if r["kind"] == "record_scope"]:
            if customer and access["own_records_only"] and req["id"] in suggested:
                checks.append(suggested[req["id"]]["check"])
                scopes.append(suggested[req["id"]]["scope"])
            elif customer and access["own_records_only"] and reads:
                named = [s for s in reads if f'{ops[s["operation_id"]]["method"]} {ops[s["operation_id"]]["path"]}' in req["operations"]]
                step_for = (named or reads)[0]
                checks.append(dict(requirement_id=req["id"], kind="ownership", resource="Record", step_id=step_for["id"], response_status="200", resource_field="/customer_id", identity_source="customer_id"))
                scopes.append(dict(requirement_id=req["id"], mode="checked", reason="A customer may only use their own records."))
            else:
                scopes.append(dict(requirement_id=req["id"], mode="unrestricted", reason=f"Owner decision: {statement}"[:500]))
        for req in [r for r in view["requirements"] if r["kind"] == "data_scope"]:
            scopes.append(dict(requirement_id=req["id"], mode="api_filtered" if customer else "unrestricted", reason="Owner decision: the API limits data to the signed-in caller." if customer else "Owner decision: public data."))
        flags = item.get("confirmed_effects") or dict(item["suggested_effects"], idempotent=item["class"] == "read", financial=False, irreversible=False, external_side_effects=False)
        principals = (["end_user", "resource_owner"] if checks else ["end_user"]) if customer else ["public"]
        policy = dict(principals=principals, authentication="required" if customer else "none", role_source=None, roles=[], permission_source=None, permissions=[], scopes=scopes, checks=checks,
                      inputs=(view.get("policy_form") or {}).get("inputs", []), financial=bool(flags["financial"]), irreversible=bool(flags["irreversible"]),
                      external_side_effects=bool(flags["external_side_effects"]), idempotent=bool(flags["idempotent"]), explanation=statement[:1000])
        svc.accept_policy(pid, view["version"], PolicySubmission(expected_revision=view["review_revision"], policy=AccessPolicy.model_validate(policy)))

    def reconcile_until_ready(self, pid, statement):
        svc = self.svc
        for _ in range(MAX_RECONCILIATIONS):
            view = svc.view(pid)
            if view["state"] == "ready_for_review":
                return view
            last = (view.get("reconciliations") or [{}])[-1].get("result") or {}
            ids = {q["id"] for q in view["content"]["questions"]}
            open_findings = [f for f in last.get("findings", []) if f["status"] != "resolved" and f["question_id"] in ids]
            if open_findings:
                svc.answers(pid, view["version"], AnswerSubmission(expected_revision=view["review_revision"], answers={f["question_id"]: statement for f in open_findings}))
                view = svc.view(pid)
            svc.reconcile(pid, view["version"], view["review_revision"])
        view = svc.view(pid)
        if view["state"] != "ready_for_review":
            raise AppError("batch_review_open", f'The tool "{view["content"]["name"]}" still has open questions after checking; review it in the advanced view', 409)
        return view

    def build(self, pid, row, content, actor):
        svc = self.svc
        settings = content["settings"]
        if not settings.get("base_url"):
            raise AppError("batch_no_connection", "Enter the API's base URL on the connection screen first", 409)
        view = svc.view(pid)
        draft = svc.connectors.draft(pid)
        fields = sorted(draft.get("context_fields") or [])  # what the accepted access policy needs the caller's trusted context to hold
        cid = svc.connectors.save(pid, settings["base_url"], fields, confirmed=True, environment=SANDBOX, fixtures=settings.get("fixtures"))
        artifact = svc.build_artifact(pid, ArtifactBuildSubmission(connector_id=cid))
        artifact = self.review_enforcement(pid, artifact)
        aid = artifact["id"]
        full = svc.artifact(aid)
        for account in settings.get("credentials") or []:
            credential = {k: open_value(account[k], self.settings) for k in ("token", "username", "password") if account.get(k)}
            svc.connectors.save_identity(full["content"], account["name"], account["scope"], credential, account.get("context") or {})
        return aid

    def review_enforcement(self, pid, artifact):
        from ..models import EnforcementReviewSubmission
        svc = self.svc
        content = svc.artifact(artifact["id"])["content"]
        if not content.get("execution_blockers"):
            return artifact
        pending = [e for e in (svc.view(pid).get("enforcement") or []) if e.get("status") == "awaiting_review"]
        for e in pending:
            svc.review_enforcement(e["id"], EnforcementReviewSubmission(note="Reviewed with the batch approval by the owner"))
        if pending:
            artifact = svc.build_artifact(pid, ArtifactBuildSubmission(connector_id=content["connector"]["id"]))
        blockers = svc.artifact(artifact["id"])["content"].get("execution_blockers")
        if blockers:
            raise AppError("batch_blocked", "Execution is blocked: " + "; ".join(str(b) for b in blockers)[:300], 409)
        return artifact

    # ---- 3. publish ----

    def publish(self, batch_id, actor=None):
        """Publish every tool whose automatic tests passed to the sandbox. Never Live Demo or production."""
        row = self._load(batch_id)
        actor = actor or self.settings.dev_reviewer_id
        published = []
        for index, item in enumerate(row["content"]["items"]):
            if item["state"] != "ready_to_publish":
                continue
            try:
                pub = self.svc.publish(item["artifact_id"], PublicationSubmission(environment="sandbox", note=f"Published by {actor} from the batch screen after passing the automatic tests"))
                self._update(batch_id, index, state="published", publication_id=pub["id"], tool_name=pub["tool_name"], message=None)
                published.append(item["proposal_id"])
            except AppError as exc:
                self._update(batch_id, index, state="needs_attention", message=exc.message, problems=[exc.code])
        if not published and not any(i["state"] == "published" for i in self._load(batch_id)["content"]["items"]):
            raise AppError("batch_nothing_to_publish", "No tool in this batch has passed its automatic tests yet", 409)
        return self.view(batch_id)


def row_spec(batches, pid):
    return batches.svc.view(pid)["spec_id"]
