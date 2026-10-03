"""Live sandbox demonstration on a COPY of the TEST-ONLY approved review database.

The source database is opened read-only and copied. Two disposable, non-superuser accounts and one
item each are created through the target's public API by an independent harness (not the tool);
the harness also checks backend state and deletes everything afterwards. Credentials live only in
the local connectors file and never appear in the printed log.
"""
import argparse
import hashlib
import json
import secrets
import sqlite3
import uuid
from pathlib import Path
import httpx
from team_c.config import AppError, Settings
from team_c.models import ArtifactBuildSubmission, EnforcementReviewSubmission, EnforcementSubmission, SandboxRunSubmission
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store


class Harness:
    """Direct API client for setup, state checks and cleanup; independent of the executor."""
    def __init__(self, base):
        self.http = httpx.Client(base_url=base + "/api/v1", timeout=10, trust_env=False)
        self.accounts = {}

    def signup(self, name):
        email, password = f"teamc-sandbox-{name}-{secrets.token_hex(4)}@teamc-sandbox.org", secrets.token_urlsafe(18)
        user = self.http.post("/users/signup", json=dict(email=email, password=password, full_name=f"Team C sandbox {name} (disposable)"))
        user.raise_for_status()
        token = self.http.post("/login/access-token", data=dict(username=email, password=password)).json()["access_token"]
        me = self.http.get("/users/me", headers=self.auth(token)).json()
        assert not me["is_superuser"]
        item = self.http.post("/items/", json=dict(title=f"{name} original title", description=f"{name} original description"), headers=self.auth(token)).json()
        self.accounts[name] = dict(email=email, password=password, token=token, id=user.json()["id"], item=item["id"])
        return self.accounts[name]

    @staticmethod
    def auth(token):
        return {"Authorization": "Bearer " + token}

    def item(self, name):
        a = self.accounts[name]
        r = self.http.get(f'/items/{a["item"]}', headers=self.auth(a["token"]))
        return r.json() if r.status_code == 200 else dict(status=r.status_code)

    def cleanup(self):
        result = {}
        for name, a in self.accounts.items():
            item = self.http.delete(f'/items/{a["item"]}', headers=self.auth(a["token"])).status_code
            user = self.http.delete("/users/me", headers=self.auth(a["token"])).status_code
            login = self.http.post("/login/access-token", data=dict(username=a["email"], password=a["password"])).status_code
            result[name] = dict(item_delete=item, user_delete=user, login_after_delete=login)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/openapi-live-review-copy.sqlite3")
    parser.add_argument("--copy", default="data/sandbox-demo.sqlite3")
    parser.add_argument("--connectors", default="data/sandbox-connectors.json")
    parser.add_argument("--proposal", default="b6d2b7da-4438-46a1-9917-cedd2f98b4ff")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--keep", action="store_true", help="keep the disposable accounts so the artifact page can run more tests")
    args = parser.parse_args()
    real = Path("data/openapi-live.sqlite3")
    real_hash = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    for suffix in ("", "-wal", "-shm"):
        Path(args.copy + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{Path(args.source).resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(args.copy)
    src.backup(dst)
    dst.close()
    src.close()

    harness, log = Harness(args.base_url), []
    alice, bob = harness.signup("alice"), harness.signup("bob")
    settings = Settings(database_path=args.copy, connectors_file=args.connectors, sandbox_hosts=httpx.URL(args.base_url).netloc.decode())
    store = Store(args.copy)
    service = Service(settings, store, Providers(settings, store))
    pid = args.proposal
    view = service.view(pid)
    log.append(dict(step="approved_copy", version=view["version"], state=view["state"], decision=view["decisions"][-1]["reason"],
                    requirements={r["id"]: r["status"] for r in view["requirements"]}))
    identity = lambda a, **extra: dict(dict(username=a["email"], password=a["password"], scope="end_user", context=dict(user_id=a["id"])), **extra)
    Path(args.connectors).write_text(json.dumps(dict(connectors={"target-sandbox": dict(
        business_id=view["business_id"], base_url=args.base_url, sandbox=True, context_fields=["user_id"],
        identities=dict(alice=identity(alice), bob=identity(bob), wrong_password=identity(alice, password="not-the-password"),
                        no_credential=dict(scope="end_user", context=dict(user_id=alice["id"]))))}), indent=2), encoding="utf-8")

    enforcement = {r["id"]: dict(mechanism="delegated_user_credential") if r["kind"] == "caller_access"
                   else dict(mechanism="response_field_matches_context", step_id="s1", response_status="200", pointer="/owner_id", context_field="user_id",
                             comparison="equals", check_point="after_step") for r in view["requirements"]}
    blocked = service.build_artifact(pid, ArtifactBuildSubmission(connector_id="target-sandbox"))
    config = service.submit_enforcement(pid, EnforcementSubmission(connector_id="target-sandbox", enforcement=enforcement))
    log.append(dict(step="enforcement_submitted", id=config["id"], status=config["status"], submitted_by=config["submitted_by"]))
    try:
        service.build_artifact(pid, ArtifactBuildSubmission(connector_id="target-sandbox"))
    except AppError as exc:
        log.append(dict(step="build_before_enforcement_review", code=exc.code))
    config = service.review_enforcement(config["id"], EnforcementReviewSubmission(note="TEST-ONLY enforcement review on the demo copy"))
    artifact = service.build_artifact(pid, ArtifactBuildSubmission(connector_id="target-sandbox"))
    c = artifact["content"]
    log.append(dict(step="artifacts_built", unenforced=dict(id=blocked["id"], execution_blockers=blocked["content"]["execution_blockers"]),
                    enforced=dict(id=artifact["id"], sha256=artifact["sha256"], name=c["name"], proposal_version=c["proposal"]["version"],
                                  decision_id=c["proposal"]["decision_id"], spec_sha256=c["source"]["spec_sha256"],
                                  steps=[f'{s["id"]} {s["method"]} {s["path"]} ({s["effect"]})' for s in c["steps"]],
                                  input_required=c["input_schema"]["required"], activation=c["activation"])))
    rebuilt = service.build_artifact(pid, ArtifactBuildSubmission(connector_id="target-sandbox"))
    log.append(dict(step="rebuild_is_idempotent", same_artifact=rebuilt["id"] == artifact["id"]))

    def attempt(label, aid, who, arguments, expected):
        before = dict(alice=harness.item("alice"), bob=harness.item("bob"))
        try:
            result = service.run_sandbox(aid, SandboxRunSubmission(identity=who, arguments=arguments))
            outcome = dict(status=result["report"]["status"], message=result["report"]["message"],
                           trace=[{k: e.get(k) for k in ("step_id", "method", "http_status", "outcome", "write_state")} for e in result["report"]["trace"]])
        except AppError as exc:
            outcome = dict(status="rejected", code=exc.code, message=exc.message, errors=exc.details.get("errors"))
        after = dict(alice=harness.item("alice"), bob=harness.item("bob"))
        changed = sorted(k for k in before if before[k] != after[k])
        log.append(dict(step=label, identity=who, argument_names=sorted(arguments), expected=expected, outcome=outcome, backend_changed=changed,
                        backend_titles={k: v.get("title") for k, v in after.items()}))

    aid = artifact["id"]
    attempt("blocked_without_enforcement", blocked["id"], "alice", dict(item_id=alice["item"], new_title="x"), "rejected: the unenforced artifact predates the reviewed enforcement (enforcement_not_current); nothing changes")
    attempt("owner_updates_own_item", aid, "alice", dict(item_id=alice["item"], new_title="alice renamed by sandbox tool"), "succeeded; only alice's title changes")
    attempt("missing_argument", aid, "alice", dict(new_title="x"), "rejected before any request; nothing changes")
    attempt("identity_as_argument", aid, "alice", dict(item_id=alice["item"], owner_id=bob["id"]), "rejected before any request (unknown argument)")
    attempt("no_credential", aid, "no_credential", dict(item_id=alice["item"], new_title="x"), "rejected: credential_missing; nothing changes")
    attempt("wrong_password", aid, "wrong_password", dict(item_id=alice["item"], new_title="x"), "failed at connector authentication; nothing changes")
    attempt("cross_user_update", aid, "alice", dict(item_id=bob["item"], new_title="hijacked"), "failed at lookup (403); bob's item unchanged; no update sent")
    attempt("missing_item", aid, "bob", dict(item_id=str(uuid.uuid4()), new_title="x"), "failed at lookup (404); no update sent")
    attempt("other_owner_updates_own_item", aid, "bob", dict(item_id=bob["item"], new_description="bob changed description"), "succeeded; only bob's item changes")

    with store.connect(write=True) as conn:
        original = conn.execute("SELECT content FROM versions WHERE proposal_id=? AND version=?", (pid, view["version"])).fetchone()[0]
        altered = json.loads(original)
        altered["steps"][1]["purpose"] += " (altered after approval)"
        conn.execute("UPDATE versions SET content=? WHERE proposal_id=? AND version=?", (json.dumps(altered), pid, view["version"]))
    attempt("changed_version_reuses_approval", aid, "alice", dict(item_id=alice["item"], new_title="x"), "rejected: approval_not_current")
    with store.connect(write=True) as conn:
        conn.execute("UPDATE versions SET content=? WHERE proposal_id=? AND version=?", (original, pid, view["version"]))

    restarted = Service(settings, Store(args.copy), Providers(settings, store))
    log.append(dict(step="history_after_restart", executions=[(e["identity"], e["status"]) for e in restarted.artifact(aid)["executions"]]))
    log.append(dict(step="cleanup", result="kept (--keep)" if args.keep else harness.cleanup()))
    log.append(dict(step="real_database_unchanged", sha256_same=real_hash is None or hashlib.sha256(real.read_bytes()).hexdigest() == real_hash))
    text = json.dumps(log, indent=2, ensure_ascii=False)
    assert not any(s in text for s in (alice["password"], bob["password"], alice["token"], bob["token"]))
    print(text)


if __name__ == "__main__":
    main()
