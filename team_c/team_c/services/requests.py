import json
from ..config import AppError
from ..models import RequestTriageOutput, SuggestionOutput, ToolRequestSubmission
from ..persistence.util import uid, now, dump, digest
from ..diagnostics import check_errors
from .. import capabilities


class Requests:
    """Owner tool requests (triage, then generation) and model suggestions that become requests only when accepted."""

    def __init__(self, settings, store, providers, runs, discovery, proposals, generation):
        self.settings, self.store, self.providers = settings, store, providers
        self.runs, self.discovery, self.proposals, self.generation = runs, discovery, proposals, generation

    def tool_request(self, rid):
        row = self.store.one("SELECT * FROM tool_requests WHERE id=?", (rid,))
        for key in ("examples", "clarifications", "operation_scope", "run_ids", "proposal_ids", "existing_proposal_ids", "unresolved", "coverage", "outcome", "error"):
            if key != "examples" and row[key] is not None:
                row[key] = json.loads(row[key])
        tools = {t["proposal_id"]: t for t in self.proposals.existing_tools(row["business_id"])}
        row["proposals"] = [tools[p] for p in row["proposal_ids"] if p in tools]
        row["existing_tools"] = [tools[p] for p in row["existing_proposal_ids"] if p in tools]
        return row

    def tool_requests(self, business_id):
        return [self.tool_request(r["id"]) for r in self.store.all("SELECT id FROM tool_requests WHERE business_id=? ORDER BY created_at DESC", (business_id,))]

    def request_tool(self, business_id, submission, source="owner", suggestion_id=None, scope=None, clarifications=()):
        """Record an owner request once per idempotency key, then triage it; the proposal it yields enters normal review."""
        spec = self.discovery.request_spec(business_id, submission.spec_id)
        goal, examples = submission.goal.strip(), submission.examples.strip()
        request_hash = digest(dict(business=business_id, spec=spec["id"], goal=goal, examples=examples, source=source, suggestion=suggestion_id, scope=scope, clarifications=list(clarifications)))
        rid, stamp = uid(), now()
        notes = [dict(text=t, by=self.settings.dev_reviewer_id, at=stamp) for t in clarifications]
        with self.store.connect(write=True) as c:
            existing = c.execute("SELECT id,request_hash FROM tool_requests WHERE idempotency_key=?", (submission.idempotency_key,)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise AppError("idempotency_conflict", "This request key was already used for a different request", 409)
                return self.tool_request(existing["id"])
            c.execute("INSERT INTO tool_requests(id,business_id,spec_id,source,suggestion_id,goal,examples,clarifications,operation_scope,idempotency_key,request_hash,status,run_ids,proposal_ids,existing_proposal_ids,unresolved,requester,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,'processing','[]','[]','[]','[]',?,?,?)",
                      (rid, business_id, spec["id"], source, suggestion_id, goal, examples, dump(notes), dump(scope) if scope is not None else None, submission.idempotency_key, request_hash, self.settings.dev_reviewer_id, stamp, stamp))
        return self.process_request(rid)

    def process_request(self, rid, exclude_ids=()):
        r = self.tool_request(rid)
        spec = self.discovery.spec(r["spec_id"])
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (r["business_id"],))
        request = dict(id=rid, goal=r["goal"], examples=r["examples"], clarifications=[x["text"] for x in r["clarifications"]])
        existing = self.proposals.existing_tools(r["business_id"])
        build = lambda index, coverage: dict(business=business, owner_request={k: request[k] for k in ("goal", "examples", "clarifications")},
                                             operation_index=dict(operations=index, coverage=capabilities.model_coverage(coverage)), existing_tools=existing)
        index, coverage, payload = self.runs.fitted_index("request_triage", build, spec["inventory"], self.discovery.area_scope(spec["id"]), exclude_ids)
        coverage["all_considered_ids"] = list(dict.fromkeys(list(exclude_ids) + coverage["considered_ids"]))
        runs, unresolved, fields = list(r["run_ids"]), [], dict(proposal_ids=[], existing_proposal_ids=[], error=None)
        try:
            if r["operation_scope"] is not None:
                scope, fields["outcome"] = r["operation_scope"], dict(summary="Accepted suggestion; its supporting operations were used without a separate triage.")
            else:
                run, triage = self.runs.model_run(r["business_id"], "request_triage", payload, RequestTriageOutput)
                runs.append(run)
                try:
                    capabilities.check_triage(triage, index, existing)
                except AppError as exc:
                    self.runs.fail_run(run, exc)
                    raise
                self.store.finish_run(run, triage.model_dump())
                unresolved = [dict(m.model_dump(), source="triage") for m in triage.missing] + [dict(kind="question", description=q, operation_ids=[], source="triage") for q in triage.questions]
                fields["outcome"] = dict(summary=triage.summary, triage=triage.outcome)
                if triage.outcome != "feasible":
                    status = dict(existing_tool="existing_tool", needs_clarification="needs_clarification", unavailable="unavailable")[triage.outcome]
                    return self.save_request(rid, status, runs, unresolved, coverage, dict(fields, existing_proposal_ids=triage.existing_proposal_ids))
                scope = triage.operation_ids
            result = self.generation.generate(r["spec_id"], scope, request)
            runs += result.get("earlier_run_ids", []) + [result["run_id"]]
            unresolved += [dict(kind="capability_gap", description=g["explanation"], operation_ids=[], source="generation") for g in result["capability_gaps"]]
            status = "proposed" if result["proposal_ids"] else "existing_tool" if result["duplicates"] and not result["capability_gaps"] else "unavailable"
            return self.save_request(rid, status, runs, unresolved, coverage, dict(fields, proposal_ids=result["proposal_ids"], existing_proposal_ids=result["duplicates"]))
        except AppError as exc:
            for run in exc.details.get("earlier_run_ids", []) + [exc.details.get("run_id")]:
                if run and run not in runs:
                    runs.append(run)
            error = dict(code=exc.code, message=exc.message)
            if isinstance(exc.details.get("errors"), list):
                error["errors"] = check_errors(exc.details["errors"], self.settings.model_secrets)
            return self.save_request(rid, "failed", runs, unresolved, coverage, dict(fields, error=error))
        except Exception:
            return self.save_request(rid, "failed", runs, unresolved, coverage, dict(fields, error=dict(code="internal_error", message="Unexpected processing failure; retry the request")))

    def save_request(self, rid, status, runs, unresolved, coverage, fields):
        with self.store.connect(write=True) as c:
            c.execute("UPDATE tool_requests SET status=?,run_ids=?,proposal_ids=?,existing_proposal_ids=?,unresolved=?,coverage=?,outcome=?,error=?,updated_at=? WHERE id=?",
                      (status, dump(runs), dump(fields["proposal_ids"]), dump(fields["existing_proposal_ids"]), dump(unresolved), dump(coverage),
                       dump(fields.get("outcome")), dump(fields["error"]) if fields["error"] else None, now(), rid))
        return self.tool_request(rid)

    def clarify_request(self, rid, submission):
        """Add owner information to a request that could not become a proposal, then process it again."""
        text = submission.text.strip()
        with self.store.connect(write=True) as c:
            row = c.execute("SELECT status,clarifications FROM tool_requests WHERE id=?", (rid,)).fetchone()
            if not row:
                raise AppError("not_found", "Record not found", 404)
            if row["status"] not in ("needs_clarification", "unavailable", "failed"):
                raise AppError("request_closed", "This request already produced a proposal or found an existing tool; continue in that tool's review", 409)
            if not text and row["status"] != "failed":
                raise AppError("clarification_required", "Describe what the tool should do; only a failed request can be retried unchanged")
            items = json.loads(row["clarifications"]) + ([dict(text=text, by=self.settings.dev_reviewer_id, at=now())] if text else [])
            c.execute("UPDATE tool_requests SET clarifications=?,status='processing',updated_at=? WHERE id=?", (dump(items), now(), rid))
        return self.process_request(rid)

    def request_next_batch(self, rid):
        """Triage an unavailable request again against the operations earlier batches did not review."""
        with self.store.connect(write=True) as c:
            row = c.execute("SELECT status,operation_scope,coverage FROM tool_requests WHERE id=?", (rid,)).fetchone()
            if not row:
                raise AppError("not_found", "Record not found", 404)
            coverage = json.loads(row["coverage"]) if row["coverage"] else {}
            if row["status"] != "unavailable" or row["operation_scope"] is not None or not coverage.get("omitted_ids"):
                raise AppError("no_next_batch", "Only a request found unavailable while some operations were not yet reviewed can look in the next batch", 409)
            c.execute("UPDATE tool_requests SET status='processing',updated_at=? WHERE id=?", (now(), rid))
        return self.process_request(rid, coverage.get("all_considered_ids", coverage.get("considered_ids", [])))

    def suggestion(self, sid):
        row = self.store.one("SELECT * FROM suggestions WHERE id=?", (sid,))
        row["content"] = json.loads(row["content"])
        return row

    def suggestions(self, business_id):
        batches = self.store.all("SELECT * FROM suggestion_batches WHERE business_id=? ORDER BY created_at DESC", (business_id,))
        for b in batches:
            for key in ("coverage", "withheld", "recognized"):
                b[key] = json.loads(b[key])
        items = [dict(r, content=json.loads(r["content"])) for r in self.store.all("SELECT * FROM suggestions WHERE business_id=? ORDER BY created_at", (business_id,))]
        return dict(batches=batches, suggestions=items)

    def suggest(self, business_id, submission):
        """Ask the model for a few additional tools; deterministic screening decides category, grounding and duplicates."""
        spec = self.discovery.request_spec(business_id, submission.spec_id)
        count = max(1, min(submission.count or self.settings.suggestion_count, self.settings.suggestion_max))
        business = self.store.one("SELECT * FROM businesses WHERE id=?", (business_id,))
        existing = self.proposals.existing_tools(business_id)
        prior = [dict(r["content"], status=r["status"], category=r["category"]) for r in self.suggestions(business_id)["suggestions"]]
        goals = [r["goal"] for r in self.store.all("SELECT goal FROM tool_requests WHERE business_id=? ORDER BY created_at DESC LIMIT 10", (business_id,))]
        exclude = []
        if submission.next_batch:
            latest = self.store.all("SELECT coverage FROM suggestion_batches WHERE business_id=? AND spec_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (business_id, spec["id"]))
            if latest:
                previous = json.loads(latest[0]["coverage"])
                exclude = previous.get("all_considered_ids", previous.get("considered_ids", []))
        build = lambda index, coverage: dict(business=business, owner_goals=goals, max_suggestions=count, operation_index=dict(operations=index, coverage=capabilities.model_coverage(coverage)),
                                             existing_tools=existing, earlier_suggestions=[dict(title=p["title"], category=p["category"], status=p["status"], operation_ids=p["operation_ids"]) for p in prior])
        index, coverage, payload = self.runs.fitted_index("suggestion", build, spec["inventory"], self.discovery.area_scope(spec["id"]), exclude)
        if not index:
            raise AppError("no_next_batch", "Every operation in the selected areas has already been reviewed; press Suggest additional tools to start again", 409)
        coverage["all_considered_ids"] = list(dict.fromkeys(list(exclude) + coverage["considered_ids"]))
        run, output = self.runs.model_run(business_id, "suggestion", payload, SuggestionOutput)
        try:
            kept, withheld, recognized = capabilities.screen(output, index, existing, prior, count)
            bid, stamp = uid(), now()
            with self.store.connect(write=True) as c:
                c.execute("INSERT INTO suggestion_batches VALUES(?,?,?,?,?,?,?,?,?)", (bid, business_id, spec["id"], run, count, dump(coverage), dump(withheld), dump(recognized), stamp))
                for d in kept:
                    c.execute("INSERT INTO suggestions(id,batch_id,business_id,spec_id,content,category,status,created_at) VALUES(?,?,?,?,?,?,'open',?)",
                              (uid(), bid, business_id, spec["id"], dump(d), d["category"], stamp))
            self.store.finish_run(run, dict(batch_id=bid, kept=len(kept), withheld=len(withheld), recognized=len(recognized)))
        except Exception as exc:
            self.runs.fail_run(run, exc)
            raise
        batch = next(b for b in self.suggestions(business_id)["batches"] if b["id"] == bid)
        return dict(batch, suggestions=[dict(r, content=json.loads(r["content"])) for r in self.store.all("SELECT * FROM suggestions WHERE batch_id=? ORDER BY rowid", (bid,))])

    def decide_suggestion(self, sid, submission):
        """Accept starts a normal tool request (never build approval); dismiss and revise touch only the suggestion."""
        s = self.suggestion(sid)
        if submission.action == "accept" and s["status"] == "accepted":
            return dict(suggestion=s, request=self.tool_request(s["request_id"]))
        if s["status"] != "open":
            raise AppError("suggestion_closed", f'This suggestion is already {s["status"]}', 409)
        who, stamp, note = self.settings.dev_reviewer_id, now(), submission.note.strip()
        if submission.action == "dismiss":
            with self.store.connect(write=True) as c:
                c.execute("UPDATE suggestions SET status='dismissed',decided_by=?,decided_at=?,decision_note=? WHERE id=? AND status='open'", (who, stamp, note, sid))
            return dict(suggestion=self.suggestion(sid))
        if submission.action == "revise":
            title, purpose = (submission.title or "").strip(), (submission.purpose or "").strip()
            if not title and not purpose:
                raise AppError("revision_required", "Give a new title or purpose")
            content = dict(s["content"], title=title or s["content"]["title"], purpose=purpose or s["content"]["purpose"], revised_by_owner=True)
            new = uid()
            with self.store.connect(write=True) as c:
                if c.execute("UPDATE suggestions SET status='revised',decided_by=?,decided_at=?,decision_note=? WHERE id=? AND status='open'", (who, stamp, note, sid)).rowcount != 1:
                    raise AppError("suggestion_closed", "This suggestion changed; refresh", 409)
                c.execute("INSERT INTO suggestions(id,batch_id,business_id,spec_id,parent_id,content,category,status,created_at) VALUES(?,?,?,?,?,?,?,'open',?)",
                          (new, s["batch_id"], s["business_id"], s["spec_id"], sid, dump(content), s["category"], stamp))
            return dict(suggestion=self.suggestion(new), revised=self.suggestion(sid))
        if s["category"] == "blocked_by_missing_api":
            raise AppError("suggestion_blocked", "The API lacks what this suggestion needs; it cannot become a proposal until the operation exists", 409)
        clarification = submission.clarification.strip()
        if s["category"] == "needs_clarification" and not clarification:
            raise AppError("clarification_required", "Answer the missing information before accepting this suggestion")
        c0 = s["content"]
        goal = f'{c0["title"]}: {c0["purpose"]}'[:2000]
        request = ToolRequestSubmission(goal=goal.ljust(10, "."), examples=c0["benefit"][:4000], spec_id=s["spec_id"], idempotency_key="suggestion:" + sid)
        with self.store.connect(write=True) as c:
            if c.execute("UPDATE suggestions SET status='accepted',decided_by=?,decided_at=?,decision_note=? WHERE id=? AND status='open'", (who, stamp, note, sid)).rowcount != 1:
                raise AppError("suggestion_closed", "This suggestion changed; refresh", 409)
        try:
            result = self.request_tool(s["business_id"], request, "suggestion", sid, c0["operation_ids"], [clarification] if clarification else [])
        except AppError:
            with self.store.connect(write=True) as c:
                c.execute("UPDATE suggestions SET status='open',decided_by=NULL,decided_at=NULL,decision_note=NULL WHERE id=? AND request_id IS NULL", (sid,))
            raise
        with self.store.connect(write=True) as c:
            c.execute("UPDATE suggestions SET request_id=? WHERE id=?", (result["id"], sid))
        return dict(suggestion=self.suggestion(sid), request=result)
