"""Automatic testing of a built tool: provision what the tests need, derive the scenarios, run them, fix test setup, report.

Everything goes through the existing paths so verdicts, failure reports and evidence are exactly the manual ones:
`Building.run_sandbox_test` for evidence tests, `repair.diagnose` for failures, `Capability.tests` for the guard scenarios and
`Publication.publication_problems` for "may this be published". Automation never approves anything, never calls the model
repair (a wiring defect becomes a question for the owner), never retries a write blindly (every attempt that writes uses a
fresh record) and only ever publishes to the sandbox, and only when asked.
"""
import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .. import autotest as plans
from .. import repair as repairs
from ..config import AppError
from ..connectors import SANDBOX, environment_of, external_connectors, load_connectors
from ..executor import artifact_environment
from ..fixtures import FixtureClient
from ..models import PublicationSubmission, SandboxExpectation, SandboxRunSubmission, SandboxTestSubmission
from ..persistence.util import digest, now, uid
from ..progress import live_step
from ..publishing import failure_category as publishing_category

WRONG_STATE = (409, 422)  # the API refuses the record as it is: wrong state, so try the next state / candidate
MAX_SETUP_RETRIES = 2  # extra attempts per scenario for test-setup fixes (never for wiring or access questions)
RUNNING_FRESH = timedelta(hours=1)


@contextmanager
def progress(kind):
    step = live_step(kind, "local", False)
    try:
        yield
    finally:
        if step:
            step["finished"] = time.time()


def failing_http(report):
    """HTTP status of the failing step of a failed test, from its structured failure report."""
    actual = (report or {}).get("actual") or {}
    step = (actual.get("failure") or {}).get("step_id")
    return next((e.get("http_status") for e in actual.get("trace", []) if e.get("step_id") == step), None)


def find_ids(value, names, found=None):
    """Values of keys called one of `names` anywhere in a response (record candidates found through a list operation)."""
    found = [] if found is None else found
    if isinstance(value, dict):
        for k, v in value.items():
            if k in names and isinstance(v, (str, int)) and not isinstance(v, bool):
                found.append(v)
            else:
                find_ids(v, names, found)
    elif isinstance(value, list):
        for v in value:
            find_ids(v, names, found)
    return list(dict.fromkeys(found))


class Fixtures:
    """Accounts and records from the sandbox's fixture provider, created on demand and tracked for this run."""

    def __init__(self, client, run_id, plan):
        self.client, self.run_id, self.plan = client, run_id, plan
        self.accounts, self.discard = {}, []

    def account(self, label):
        if label not in self.accounts:
            self.accounts[label] = self.client.accounts(f"auto-{self.run_id[-6:]}-{label}", self.run_id)
        return self.accounts[label]

    def options(self, label, state=None):
        """Records to try for this account: one new record per state (in the provider's order), or only `state`."""
        for s in ([state] if state else self.plan["states_to_try"] or [None]):
            rec = self.client.record(self.account(label)["account_id"], self.plan["record_kind"], s, run_id=self.run_id)
            yield rec["record_id"], rec.get("state") or s


class Existing:
    """No fixture provider: records are what the existing test accounts own, found through a read/list tool of the same connector."""

    def __init__(self, candidates):
        self.candidates = candidates  # label -> [record ids]
        self.discard = []

    def options(self, label, state=None):
        for rid in self.candidates.get(label, []):
            yield rid, None


class AutoTest:
    def __init__(self, settings, store, proposals, discovery, building, publishing, capability, connectors):
        self.settings, self.store = settings, store
        self.proposals, self.discovery = proposals, discovery
        self.building, self.publishing, self.capability, self.connectors = building, publishing, capability, connectors

    # ---- state ----

    def runs(self, aid):
        rows = self.store.all("SELECT id,status,result,error,created_at,completed_at FROM runs WHERE kind='auto_test' AND input_hash=? ORDER BY created_at DESC, rowid DESC", (digest(aid),))
        return [self.run_view(r) for r in rows]

    @staticmethod
    def run_view(row):
        return dict(id=row["id"], status=row["status"], created_at=row["created_at"], completed_at=row["completed_at"],
                    result=json.loads(row["result"]) if row["result"] else None, error=json.loads(row["error"]) if row["error"] else None)

    def run(self, rid):
        row = self.store.one("SELECT id,status,result,error,created_at,completed_at FROM runs WHERE id=? AND kind='auto_test'", (rid,))
        if not row:
            raise AppError("run_not_found", "No automatic test run with this id", 404)
        return self.run_view(row)

    def latest(self, aid):
        runs = self.runs(aid)
        return runs[0] if runs else None

    def running(self, aid):
        since = (datetime.now(timezone.utc) - RUNNING_FRESH).isoformat()
        rows = self.store.all("SELECT id FROM runs WHERE kind='auto_test' AND input_hash=? AND status='running' AND created_at>=? ORDER BY created_at DESC LIMIT 1", (digest(aid), since))
        return rows[0]["id"] if rows else None

    def readiness_state(self, aid, a=None, problems=None):
        """not_tested | testing | needs_owner | ready_to_publish | published, computed from the tests, the evidence and the
        publications (never stored, so it cannot drift from them)."""
        if any(p["status"] == "published" and p["execution_enabled"] for p in self.publishing.publications(artifact_id=aid)):
            return "published"
        if self.running(aid):
            return "testing"
        a = a or self.building.artifact(aid)
        if not a["tests"]:
            return "not_tested"
        if problems is None:
            try:
                problems = self.publishing.publication_problems(aid)[1]
            except AppError:
                problems = ["not buildable"]
        return "ready_to_publish" if not problems else "needs_owner"

    # ---- starting ----

    def settings_of(self, business_id):
        """Per-business choices for automatic tests. Publishing when ready is OFF unless the owner switched it on."""
        row = self.store.all("SELECT auto_publish_sandbox,updated_by,updated_at FROM business_settings WHERE business_id=?", (business_id,))
        return dict(business_id=business_id, auto_publish_sandbox=bool(row[0]["auto_publish_sandbox"]) if row else False,
                    updated_by=row[0]["updated_by"] if row else None, updated_at=row[0]["updated_at"] if row else None)

    def set_settings(self, business_id, auto_publish_sandbox):
        if not self.store.all("SELECT 1 FROM businesses WHERE id=?", (business_id,)):
            raise AppError("business_not_found", "No business with this id", 404)
        with self.store.connect(write=True) as c:
            c.execute("INSERT INTO business_settings(business_id,auto_publish_sandbox,updated_by,updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(business_id) DO UPDATE SET auto_publish_sandbox=excluded.auto_publish_sandbox,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                      (business_id, int(bool(auto_publish_sandbox)), self.settings.dev_reviewer_id, now()))
        return self.settings_of(business_id)

    def start(self, aid, publish_when_ready=None, cleanup=False):
        """Begin a run in the background and return its id at once (poll `run`); one run per artifact at a time."""
        a = self.building.artifact(aid)
        self.preconditions(a)
        existing = self.running(aid)
        if existing:
            return dict(run_id=existing, status="running", already_running=True)
        rid = self.store.start_run(a["content"]["proposal"]["business_id"], "auto_test", aid)
        threading.Thread(target=self.background, args=(aid, rid, publish_when_ready, cleanup), daemon=True).start()
        return dict(run_id=rid, status="running", already_running=False)

    def background(self, *args):
        try:
            self.execute(*args)
        except Exception:  # already recorded on the run (failed, with a redacted error)
            pass

    def auto_test(self, aid, publish_when_ready=None, cleanup=False):
        """Run to completion in this call (the browser job and tests use this). publish_when_ready=None uses the business setting."""
        a = self.building.artifact(aid)
        self.preconditions(a)
        existing = self.running(aid)
        if existing:
            raise AppError("auto_test_running", "An automatic test run is already in progress for this tool", 409, dict(run_id=existing))
        rid = self.store.start_run(a["content"]["proposal"]["business_id"], "auto_test", aid)
        return self.execute(aid, rid, publish_when_ready, cleanup)

    def preconditions(self, a):
        c = a["content"]
        if artifact_environment(c) != SANDBOX:
            raise AppError("autotest_sandbox_only", "Automatic tests run on sandbox connectors only; Live Demo and production tools are never given generated accounts or records", 409)
        self.building.approval(c["proposal"]["id"], c["proposal"]["version"], c["proposal"]["content_sha256"])
        self.building.check_enforcement(c)
        if c["execution_blockers"]:
            raise AppError("access_enforcement_missing", "Execution is blocked until every access requirement has an enforceable mechanism", 409, {"blockers": c["execution_blockers"]})
        connector = load_connectors(self.settings).get(c["connector"]["id"])
        if not connector or environment_of(connector) != SANDBOX:
            raise AppError("not_sandbox", "The tool's connector is not a configured sandbox connector", 409)

    def execute(self, aid, rid, publish_when_ready=False, cleanup=False):
        try:
            result = self.run_all(aid, publish_when_ready, cleanup)
        except AppError as exc:
            self.store.finish_run(rid, error=dict(code=exc.code, message=exc.message))
            raise
        except Exception:
            self.store.finish_run(rid, error=dict(code="internal_error", message="The automatic test run stopped on an unexpected error. Nothing was published."))
            raise
        result["run_id"] = rid
        self.store.finish_run(rid, result)
        return result

    # ---- the run ----

    def run_all(self, aid, publish_when_ready, cleanup):
        a = self.building.artifact(aid)
        c = a["content"]
        if publish_when_ready is None:
            publish_when_ready = self.settings_of(c["proposal"]["business_id"])["auto_publish_sandbox"]
        connector = load_connectors(self.settings).get(c["connector"]["id"]) or {}
        fx_run = "auto-" + uid()[:12]
        log, asks = [], []
        out = dict(artifact_id=aid, fixture_run=fx_run, source="fixtures" if connector.get("fixtures") else "existing_accounts", plan=None, attempts=log,
                   needs_owner=asks, identities={}, diagnostics=[], policy_tests=None, problems=[], outcome="needs_owner", published=None, readiness_state=None, fixture_summary=None)
        selectors = self.building.record_selectors(a)
        client = FixtureClient(connector, self.settings, self.building.execution_transport) if connector.get("fixtures") else None
        kinds = []
        if client:
            kinds = client.kinds()
        test_plan = plans.plan(c, selectors, kinds)
        out["plan"] = test_plan
        ask = lambda where, question, **more: asks.append(dict(where=where, question=question, **more))
        for b in test_plan["blockers"]:
            ask("fixtures", b)
        source = names = None
        if not asks:
            with progress("autotest_accounts"):
                try:
                    source, names = self.provision(a, c, connector, client, fx_run, test_plan, out, ask)
                except AppError as exc:
                    source, names = None, {}
                    ask("#identity", f"Test accounts could not be set up: {exc.message}")
        if not asks:
            self.retire_stale(a, test_plan)
            with progress("autotest_run"):
                self.run_plan(a, c, connector, client, source, names, test_plan, out, ask)
        a = self.building.artifact(aid)
        with progress("autotest_evidence"):
            if not asks:
                try:
                    out["policy_tests"] = bool(self.capability.tests(a)["passed"]) if c.get("access_policy") else None
                except AppError as exc:
                    out["policy_tests"] = False
                    ask("#policy", f"The generated guard scenarios could not run: {exc.message}")
            problems = self.publishing.publication_problems(aid)[1]
        out["problems"] = problems
        if client and out["identities"] and test_plan["writes"]:
            out["fixture_summary"] = self.summary(client, fx_run)
        if cleanup and client:
            try:
                client.reset(fx_run, keep_accounts=True)
            except AppError as exc:
                log.append(dict(step="cleanup", result="failed", note=exc.message))
        elif client and source is not None and source.discard:
            try:
                client.reset(fx_run, keep_accounts=True, record_ids=source.discard)
            except AppError as exc:
                log.append(dict(step="cleanup", result="failed", note=exc.message))
        if not problems and not asks:
            out["outcome"] = "ready_to_publish"
            if publish_when_ready:
                pub = self.publishing.publish(aid, PublicationSubmission(note="Automatic tests passed (publish when ready, sandbox only)", environment="sandbox"))
                out["published"] = pub["id"]
                out["outcome"] = "published"
        elif not asks:
            ask("#publish", "The tests ran but publication still has open requirements: " + "; ".join(problems))
        out["pruned_identities"] = self.prune_identities(c["connector"]["id"], c["proposal"]["business_id"])
        out["readiness_state"] = self.readiness_state(aid)
        out.pop("_fixture_accounts", None)
        return out

    def prune_identities(self, cid, business_id):
        """Delete `auto-*` identities of this connector that no current test (of any of its artifacts) uses as caller or record
        owner, so repeated runs do not pile up accounts. Identities that evidence rests on are never touched, nor are the
        owner's own (non-`auto-`) identities. Returns the names removed."""
        connector = load_connectors(self.settings).get(cid) or {}
        mine = [n for n in (connector.get("identities") or {}) if n.startswith("auto-")]
        if not mine or cid in external_connectors(self.settings):
            return []
        rows = self.store.all("SELECT t.artifact_id,t.name,t.record_owner,e.identity FROM sandbox_tests t LEFT JOIN executions e ON e.id=t.execution_id "
                              "JOIN artifacts a ON a.id=t.artifact_id WHERE json_extract(a.content,'$.connector.id')=? ORDER BY t.created_at, t.rowid", (cid,))
        latest = {(r["artifact_id"], r["name"]): r for r in rows}
        used = {x for r in latest.values() for x in (r["identity"], r["record_owner"]) if x}
        gone = [n for n in mine if n not in used]
        for n in gone:
            self.connectors.remove_identity(cid, business_id, n)
        return gone

    def summary(self, client, fx_run):
        """Counts only from the provider's independent read-back; values never enter the run."""
        try:
            state = client.state(fx_run)
        except AppError as exc:
            return dict(error=exc.code)
        if not isinstance(state, dict):
            return None
        return {k: len(v) for k, v in state.items() if isinstance(v, list)}

    def scope_of(self, a, connector):
        guide = self.connectors.identity_guide(a["content"], connector)
        return guide["scope"] or "end_user"

    def provision(self, a, c, connector, client, fx_run, test_plan, out, ask):
        """Test accounts for the labels the plan needs. Fixture accounts are created and saved as connector identities;
        without a provider, existing identities of the right account type are used."""
        scope = self.scope_of(a, connector)
        names = {}
        if client:
            source = Fixtures(client, fx_run, test_plan)
            fields = set(connector.get("context_fields") or [])
            for label in test_plan["accounts"] + (["revoked"] if any(t["scenario"] == "bad_credential" for t in test_plan["tests"]) else []):
                account = source.account(label)
                credential = account["credential"]
                context = {k: v for k, v in (account.get("context") or {}).items() if k in fields}
                name = f"auto-{fx_run[-6:]}-{label}"
                self.connectors.save_identity(c, name, scope, credential, context)
                out.setdefault("_fixture_accounts", {})[name] = account["account_id"]
                names[label] = name
                out["identities"][label] = name
            return source, names
        identities = {n: i for n, i in (connector.get("identities") or {}).items() if i.get("scope") == scope}
        pool = sorted(identities)
        if len(pool) < len(test_plan["accounts"]):
            missing = len(test_plan["accounts"]) - len(pool)
            ask("#identity", f"Add {missing} more test account{'s' if missing > 1 else ''} of type {scope}" + (" that owns a record the tool can use" if test_plan["needs_records"] else "")
                + ", or declare test fixtures on the connector so accounts are created automatically.")
            return None, names
        for label, name in zip(test_plan["accounts"], pool):
            names[label] = name
            out["identities"][label] = name
        candidates = self.candidates(a, c, connector, names, test_plan, out) if test_plan["record_inputs"] else {}
        for label in test_plan["accounts"] if test_plan["record_inputs"] else []:
            if not candidates.get(label):
                ask("#identity", f"Test account {names[label]} has no record this tool can use. Add a record it owns (and a tool on this connector that lists the account's records), or declare test fixtures.")
        return Existing(candidates), names

    def candidates(self, a, c, connector, names, test_plan, out):
        """Record ids each identity can see through a no-input read tool of the same connector (§1.4): never a signup or admin call."""
        wanted = set(test_plan["record_inputs"]) | {"id"}
        found = {label: [] for label in names}
        rows = self.store.all("SELECT id FROM artifacts WHERE json_extract(content,'$.connector.id')=? AND id!=? ORDER BY created_at DESC LIMIT 20", (c["connector"]["id"], a["id"]))
        for r in rows:
            other = self.building.artifact(r["id"])["content"]
            if other["input_schema"].get("required") or any(s["effect"] == "write" for s in other["steps"]) or other.get("execution_blockers"):
                continue
            for label, name in names.items():
                try:
                    result = self.building.run_sandbox(r["id"], SandboxRunSubmission(identity=name, arguments={}))
                except AppError:
                    continue
                out["attempts"].append(dict(step="find_records", tool=other["name"], identity=label, result=result["report"]["status"]))
                if result["report"]["status"] == "succeeded":
                    found[label] += [i for i in find_ids(result["outputs"], wanted) if i not in found[label]]
            if all(found.values()):
                break
        return found

    def retire_stale(self, a, test_plan):
        """An earlier automatic plan's failed test that this plan no longer runs would block publication forever; retire it."""
        current = {t["name"] for t in test_plan["tests"]}
        for t in a["tests"]:
            if t["name"].startswith("auto.") and t["current"] and t["verdict"] == "failed" and t["name"] not in current:
                self.building.retire_test(t["id"], "Superseded by a newer automatic test plan that no longer runs this scenario.", "auto_test")

    # ---- running the plan ----

    def run_plan(self, a, c, connector, client, source, names, test_plan, out, ask):
        aid = a["id"]
        if source is None:
            return
        arguments = self.resolve_arguments(aid, test_plan, names, out, ask)
        if arguments is None:
            return
        record_inputs, records, state = test_plan["record_inputs"], {}, None
        for t in [x for x in test_plan["tests"] if x["purpose"] == plans.EVIDENCE]:
            args = dict(t["arguments"], **arguments)
            if t["scenario"] == "cross_user":
                if not records.get(t["record_of"]):
                    ask("#test-results", f"{t['name']} was not run: the record owner's own-record test has not passed.")
                    return
                args.update({i: records[t["record_of"]] for i in record_inputs})
                if not self.run_one(aid, c, t, names, args, out, ask):
                    return
            elif not test_plan["needs_records"]:
                if not self.run_one(aid, c, t, names, args, out, ask):
                    return
            else:
                worked = self.run_with_records(aid, c, t, names, args, record_inputs, source, state, records, out, ask)
                if worked is None:
                    return
                state = worked or state
        self.run_diagnostics(aid, c, client, names, test_plan, arguments, records, out)

    def run_with_records(self, aid, c, t, names, args, record_inputs, source, state, records, out, ask):
        """One evidence test on a record: try records (a new one per attempt, in each state the kind offers for writes) until one
        passes. Returns the state that worked ("" when states do not apply), or None after an owner question was recorded."""
        who, last = t["identity_label"], None
        for rec_id, rec_state in source.options(who, state):
            run_args = dict(args, **{i: rec_id for i in record_inputs})
            result = self.run_one(aid, c, t, names, run_args, out, None, state=rec_state)
            if result["verdict"] == "passed":
                records[who] = rec_id
                return rec_state or ""
            last = result
            source.discard.append(rec_id)
            if failing_http(result.get("failure_report")) not in WRONG_STATE:
                break
        self.diagnose_and_ask(aid, c, t, names, last, out, ask)
        return None

    def run_diagnostics(self, aid, c, client, names, test_plan, arguments, records, out):
        """Checks that explain a problem but are not publication evidence, so a failure here never blocks publishing."""
        record_args = {i: records["a"] for i in test_plan["record_inputs"] if records.get("a")}
        for t in [x for x in test_plan["tests"] if x["purpose"] == plans.DIAGNOSTIC]:
            args = dict(t["arguments"], **arguments, **record_args)
            note = dict(name=t["name"], scenario=t["scenario"])
            try:
                if t["scenario"] == "invalid_arguments":
                    try:
                        self.building.run_sandbox(aid, SandboxRunSubmission(identity=names["a"], arguments=dict(args, autotest_unknown_argument=1)))
                        out["diagnostics"].append(dict(note, verdict="failed", detail="An unknown argument was accepted and a request was sent"))
                    except AppError as exc:
                        ok = exc.code == "invalid_arguments"
                        out["diagnostics"].append(dict(note, verdict="passed" if ok else "failed", detail="Rejected before any request" if ok else f"{exc.code}: {exc.message}"))
                elif t["scenario"] == "bad_credential":
                    if not client or "revoked" not in names:
                        out["diagnostics"].append(dict(note, verdict="skipped", detail="No fixture provider to revoke a credential"))
                        continue
                    client.revoke(self.account_id(client, names["revoked"], out))
                    result = self.building.run_sandbox(aid, SandboxRunSubmission(identity=names["revoked"], arguments=args))
                    category = publishing_category(result["report"])
                    out["diagnostics"].append(dict(note, verdict="passed" if result["report"]["status"] == "failed" and category == "authentication_failed" else "failed",
                                                   detail=f"{result['report']['status']}: {category}"))
            except AppError as exc:
                out["diagnostics"].append(dict(note, verdict="skipped", detail=f"{exc.code}: {exc.message}"))

    def account_id(self, client, identity_name, out):
        """The fixture account behind a saved identity (we created it this run)."""
        fx = out.get("_fixture_accounts") or {}
        if identity_name not in fx:
            raise AppError("fixtures_unknown_account", "The revoked identity is not a fixture account of this run")
        return fx[identity_name]

    def run_one(self, aid, c, t, names, args, out, ask, state=None):
        """One evidence test through Building.run_sandbox_test with bounded test-setup retries. With `ask` it returns True when
        the test passed (else records the owner question and returns False); without it, it returns the last result."""
        identity = names[t["identity_label"]]
        result = None
        for attempt in range(1 + MAX_SETUP_RETRIES):
            try:
                result = self.building.run_sandbox_test(aid, SandboxTestSubmission(
                    name=t["name"], identity=identity, arguments=args, scenario=t["scenario"],
                    record_owner=names.get(t["record_owner_label"]) if t["record_owner_label"] else None, expect=SandboxExpectation(status=t["expect"])))
            except AppError as exc:
                result = dict(verdict="failed", failure_report=None, error=dict(code=exc.code, message=exc.message), status="rejected")
            out["attempts"].append(dict(test=t["name"], identity=t["identity_label"], attempt=attempt + 1, verdict=result["verdict"], status=result.get("status"),
                                        state=state, classification=(result.get("failure_report") or {}).get("classification"),
                                        code=(result.get("error") or {}).get("code")))
            if result["verdict"] == "passed" or failing_http(result.get("failure_report")) in WRONG_STATE:
                break
            fix = self.setup_fix(c, result, names, t, out)
            if not fix:
                break
            identity = fix
        if ask is None:
            return result
        if result["verdict"] == "passed":
            return True
        self.diagnose_and_ask(aid, c, t, names, result, out, ask)
        return False

    def setup_fix(self, c, result, names, t, out):
        """The 'auto' branch of repair.diagnose: only test setup that holds no secrets and widens nothing. Returns an identity name or None."""
        report = result.get("failure_report")
        if not report:
            return None
        connector = load_connectors(self.settings).get(c["connector"]["id"]) or {}
        how, action, why = repairs.diagnose(report, c, connector.get("identities") or {})
        if how != "auto":
            return None
        out["attempts"].append(dict(test=t["name"], identity=t["identity_label"], step="test_setup_fix", note=why))
        if action.get("create_identity"):
            self.connectors.save_identity(c, action["create_identity"], action["scope"], {}, {})
            return action["create_identity"]
        return action["use_identity"]

    def diagnose_and_ask(self, aid, c, t, names, result, out, ask):
        """A failure the automation may not fix. The model repair is never called here; the owner gets the failure report
        and the existing Fix-and-retest button."""
        report = (result or {}).get("failure_report")
        if not report:
            error = (result or {}).get("error") or {}
            ask("#test-results", f"{t['name']} could not run: {error.get('message', 'no result')}", test=t["name"])
            return
        connector = load_connectors(self.settings).get(c["connector"]["id"]) or {}
        how, action, why = repairs.diagnose(report, c, connector.get("identities") or {})
        if how == "repair":
            ask("#test-results", f"Needs your review: the tool's wiring may be wrong. {why} Use Fix and retest on the failed test {t['name']} to let the AI repair it (you approve the new version).",
                test=t["name"], diagnosis="repair", failure=report)
        else:
            ask(action if isinstance(action, str) else "#test-results", why, test=t["name"], diagnosis=how, failure=report)

    def resolve_arguments(self, aid, test_plan, names, out, ask):
        """Values for inputs the schema cannot decide: the existing model helper (never sees credentials), marked source model."""
        if not test_plan["unresolved"]:
            return {}
        values = {}
        try:
            suggestion = self.building.suggest_test_inputs(aid, names["a"], "own_record" if test_plan["record_scope"] else "general")
            values = {k: v for k, v in suggestion["arguments"].items() if k in test_plan["unresolved"]}
        except Exception:  # no provider or an unusable answer: the owner is asked below
            values = {}
        missing = [n for n in test_plan["unresolved"] if n not in values]
        for t in test_plan["tests"]:
            for n in values:
                t["argument_sources"][n] = "model"
        if missing:
            ask("#run", "These inputs have no value the tool's schema decides and the test helper could not choose one: " + ", ".join(missing) + ". Run the manual test with values, then run the automatic tests again.")
            return None
        return values
