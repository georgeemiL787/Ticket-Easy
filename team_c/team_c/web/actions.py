"""Browser form actions: one handler per action, each calling the same service method as the API and returning the page to show next."""
import json
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from ..config import AppError
from ..jobs import is_live, start
from ..models import (AnswerSubmission, ArtifactBuildSubmission, AreaSelectionSubmission, ClarificationSubmission, DecisionSubmission, EnforcementReviewSubmission,
                      EnforcementSubmission, GapDismissalSubmission, PublicationDisableSubmission, PublicationSubmission, RevisionSubmission, ReviewSubmission,
                      SandboxRunSubmission, SuggestionDecisionSubmission, SuggestionRunSubmission, SupersedeSubmission, ToolRequestSubmission)
from ..api.businesses import retired
from ..api.deps import JobsDep, ServiceDep, SettingsDep
from .forms import check_csrf, form_arguments

router=APIRouter()


def business(service,settings,form):
    b=service.business(str(form.get("name","")),str(form.get("description","")))
    return "/businesses/"+b["id"]


def upload(service,settings,form):
    f=form["file"]
    s=service.upload(str(form["business_id"]),f.filename,f.file.read(settings.max_upload_bytes+1))
    return "/specifications/"+s["id"]


def fetch(service,settings,form):
    s=service.fetch(str(form["business_id"]),str(form.get("url","")))
    return "/specifications/"+s["id"]


def generate(service,settings,form):
    selected=[str(v) for v in form.getlist("operation_ids")]
    if not selected: raise AppError("operation_scope","Select at least one operation")
    r=service.generate(str(form["spec_id"]),selected)
    return "/runs/"+r["run_id"]


def review_enforcement(service,settings,form):
    e=service.review_enforcement(str(form["enforcement_id"]),EnforcementReviewSubmission(note=str(form.get("note",""))))
    return "/proposals/"+e["proposal_id"]


def build_artifact(service,settings,form):
    a=service.build_artifact(str(form["proposal_id"]),ArtifactBuildSubmission(connector_id=str(form.get("connector_id",""))))
    return "/artifacts/"+a["id"]


def save_connector(service, settings, form):
    pid = str(form["proposal_id"])
    service.connectors.save(pid, str(form.get("base_url", "")), str(form.get("context_fields", "")).split(","),
                            confirmed=form.get("sandbox") == "1")
    return "/proposals/" + pid + "#build"


def save_test_identity(service, settings, form):
    aid = str(form["artifact_id"])
    artifact = service.artifact(aid)["content"]
    setup = service.connectors.identity_setup(artifact)
    schema = dict(properties={name: setup["context_schemas"].get(name, {"type": "string"}) for name in setup["context_fields"]})
    context = form_arguments(schema, form)
    service.connectors.save_identity(artifact, str(form.get("identity_name", "")).strip(), str(form.get("scope", "")),
                                     {k: str(form.get(k, "")) for k in ("username", "password", "token")}, context)
    return "/artifacts/" + aid


def publish(service,settings,form):
    service.publish(str(form["artifact_id"]),PublicationSubmission(note=str(form.get("note",""))))
    return "/artifacts/"+str(form["artifact_id"])


def request_tool(service,settings,form):
    bid=str(form["business_id"])
    service.request_tool(bid,ToolRequestSubmission(goal=str(form.get("goal","")),examples=str(form.get("examples","")),idempotency_key=str(form.get("request_key",""))))
    return "/businesses/"+bid


def clarify_request(service,settings,form):
    r=service.clarify_request(str(form["request_id"]),ClarificationSubmission(text=str(form.get("text",""))))
    return "/businesses/"+r["business_id"]


def suggest(service,settings,form):
    bid=str(form["business_id"])
    service.suggest(bid,SuggestionRunSubmission(count=int(form.get("count") or settings.suggestion_count),next_batch=form.get("next_batch")=="1"))
    return "/businesses/"+bid


def request_next_batch(service,settings,form):
    r=service.request_next_batch(str(form["request_id"]))
    return "/businesses/"+r["business_id"]


def areas_organize(service,settings,form):
    sid=str(form["spec_id"])
    service.organize_areas(sid)
    return "/specifications/"+sid


def areas_select(service,settings,form):
    sid=str(form["spec_id"])
    service.select_areas(sid,AreaSelectionSubmission(area_ids=[str(v) for v in form.getlist("area_ids")]))
    bid=service.spec(sid)["business_id"]
    if service.request_spec(bid)["id"]==sid:
        return "/businesses/"+bid+"?saved=areas#get-tools"
    return "/specifications/"+sid+"?saved=areas#areas"


def decide_suggestion(service,settings,form):
    r=service.decide_suggestion(str(form["suggestion_id"]),SuggestionDecisionSubmission(action=str(form.get("decision","")),note=str(form.get("note","")),clarification=str(form.get("clarification","")),
                                                                                         title=str(form.get("title","")) or None,purpose=str(form.get("purpose","")) or None))
    return "/businesses/"+r["suggestion"]["business_id"]


def disable_publication(service,settings,form):
    p=service.disable_publication(str(form["publication_id"]),PublicationDisableSubmission(note=str(form.get("note",""))))
    return "/artifacts/"+p["artifact_id"]


def submit_enforcement(service,settings,form):
    if "json" in form:  # Keep older clients compatible.
        try:
            data=json.loads(str(form.get("json", "") or "{}"))
        except ValueError:
            raise AppError("invalid_input", "Enter valid JSON")
    else:
        data = {}
        view = service.view(str(form["proposal_id"]))
        for requirement in view["requirements"]:
            rid = requirement["id"]
            mechanism = str(form.get("mechanism:" + rid, ""))
            if not mechanism:
                raise AppError("enforcement_required", "Choose how each access requirement will be enforced")
            config = dict(mechanism=mechanism)
            if mechanism == "response_field_matches_context":
                config.update({key: str(form.get(rid + ":" + key, "")).strip() for key in ("step_id", "response_status", "pointer", "context_field")})
                config.update(comparison="equals", check_point="after_step")
            data[rid] = config
    service.submit_enforcement(str(form["proposal_id"]),EnforcementSubmission(connector_id=str(form.get("connector_id","")),enforcement=data))
    return "/proposals/"+str(form["proposal_id"])


def sandbox_run(service,settings,form):
    aid=str(form["artifact_id"])
    data=form_arguments(service.artifact(aid)["content"]["input_schema"],form)
    try:
        service.run_sandbox(aid,SandboxRunSubmission(identity=str(form.get("identity","")),arguments=data))
    except AppError as exc:
        if "execution_id" not in exc.details: raise
    return "/artifacts/"+aid


def review(handler):
    """A proposal review action on proposal_id/version/revision; it returns to the proposal unless the handler names another page."""
    def action(service,settings,form):
        pid=str(form["proposal_id"])
        return handler(service,form,pid,int(form["version"]),int(form["revision"])) or "/proposals/"+pid
    return action


def answers(service,form,pid,version,rev):
    service.answers(pid,version,AnswerSubmission(expected_revision=rev,answers={k[2:]:str(v) for k,v in form.items() if k.startswith("q:")}))


def reconcile(service,form,pid,version,rev):
    service.reconcile(pid,version,rev)


def decision(service,form,pid,version,rev):
    service.decide(pid,version,DecisionSubmission(expected_revision=rev,action=str(form["decision"]),reason=str(form.get("reason","")),idempotency_key=str(form["idempotency_key"])))


def confirm_requirement(service,form,pid,version,rev):
    service.confirm_requirement(pid,version,str(form["requirement_id"]),ReviewSubmission(expected_revision=rev))


def supersede_question(service,form,pid,version,rev):
    service.supersede_question(pid,version,str(form["question_id"]),SupersedeSubmission(expected_revision=rev,reason=str(form.get("reason",""))))


def dismiss_gap(service,form,pid,version,rev):
    service.dismiss_gap(pid,version,int(form["gap_index"]),GapDismissalSubmission(expected_revision=rev,reason=str(form.get("reason",""))))


def revise(service,form,pid,version,rev):
    r=service.revise(pid,RevisionSubmission(expected_revision=rev,instruction=str(form.get("instruction",""))))
    if r.get("capability_gaps"):
        return "/runs/"+r["run_id"]


def accept_policy(service, form, pid, version, rev):
    from ..compiler.policies import PolicySubmission, AccessPolicy
    principals = [str(x) for x in form.getlist("principals")]
    data = dict(principals=principals, authentication=str(form.get("authentication", "declared")),
                role_source=str(form.get("role_source", "")).strip() or None,
                roles=[x.strip() for x in str(form.get("roles", "")).split(",") if x.strip()],
                permission_source=str(form.get("permission_source", "")).strip() or None,
                permissions=[x.strip() for x in str(form.get("permissions", "")).split(",") if x.strip()],
                explanation=str(form.get("explanation", "")), scopes=[], checks=[], inputs=[])
    view = service.view(pid, version)
    for r in view["requirements"]:
        rid = r["id"]
        mode = str(form.get("scope:" + rid, ""))
        if not mode:
            continue
        data["scopes"].append(dict(requirement_id=rid, mode=mode, reason=str(form.get("scope_reason:" + rid, ""))))
        if mode == "checked":
            for kind in ("ownership", "tenant", "account"):
                prefix = f"check:{rid}:{kind}:"
                if form.get(prefix + "step_id"):
                    data["checks"].append(dict(requirement_id=rid, kind=kind, **{k: str(form.get(prefix + k, "")) for k in ("resource", "step_id", "response_status", "resource_field", "identity_source")}))
    for n, item in enumerate(view["capability"]["inputs"]):
        source = str(form.get(f"input:{n}:source", ""))
        if source:
            data["inputs"].append(dict(step_id=item["step_id"], target=item["input"], source=source,
                category=str(form.get(f"input:{n}:category", item["category"])), reference=str(form.get(f"input:{n}:reference", "")), reason=str(form.get(f"input:{n}:reason", ""))))
    for key in ("financial", "irreversible", "external_side_effects", "idempotent"):
        value = form.get(key)
        data[key] = True if value == "yes" else False if value == "no" else None
    service.accept_policy(pid, version, PolicySubmission(expected_revision=rev, policy=AccessPolicy.model_validate(data)))


def reopen_policy(service, form, pid, version, rev):
    service.reopen_policy(pid, version, ReviewSubmission(expected_revision=rev))
    return "/proposals/" + pid + "#structured-policy"


def unknown(service,form,pid,version,rev):
    raise AppError("unknown_action","Unknown action",404)


ACTIONS=dict(business=business,upload=upload,fetch=fetch,local_project=lambda *_:retired(),code_analysis=lambda *_:retired(),generate=generate,
             save_connector=save_connector,save_test_identity=save_test_identity,
             review_enforcement=review_enforcement,build_artifact=build_artifact,publish=publish,request_tool=request_tool,clarify_request=clarify_request,
             suggest=suggest,request_next_batch=request_next_batch,areas_organize=areas_organize,areas_select=areas_select,decide_suggestion=decide_suggestion,
             disable_publication=disable_publication,submit_enforcement=submit_enforcement,sandbox_run=sandbox_run,
             reopen_policy=review(reopen_policy),accept_policy=review(accept_policy),answers=review(answers),reconcile=review(reconcile),decision=review(decision),confirm_requirement=review(confirm_requirement),
             supersede_question=review(supersede_question),dismiss_gap=review(dismiss_gap),revise=review(revise))


# Handlers run in a worker thread: slow provider calls do not block the ASGI loop.
@router.post("/actions/{action}")
async def action(request:Request,action:str,service:ServiceDep,settings:SettingsDep,jobs:JobsDep):
    form=await request.form()
    check_csrf(request,form)
    handler=ACTIONS.get(action,review(unknown))
    def process():
        return handler(service,settings,form)
    if not is_live(action,form):
        return RedirectResponse(await run_in_threadpool(process),status_code=303)
    return RedirectResponse(await start(jobs,action,request.headers.get("referer",""),process),status_code=303)
