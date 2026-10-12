"""AST and integration checks. AI responses here are deterministic test substitutes."""
import hashlib
import json
import shutil
from pathlib import Path
import pytest
from conftest import ROOT
from team_c.config import Settings, AppError
from team_c.code_discovery import index_project, discover_project
from team_c.grounding import validate_proposal
from team_c.models import ProposalContent


@pytest.fixture
def project(tmp_path):
    root=tmp_path/"projects";target=root/"sample"
    shutil.copytree(ROOT/"examples/local-project",target)
    settings=Settings(_env_file=None,allowed_project_directory=str(root))
    return target,settings


def inspect(project):
    target,settings=project
    return discover_project(index_project(str(target),settings),"b",settings)


def hash_tree(target):
    return {p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}


def proposal(inv):
    read=next(o for o in inv["operations"] if o["method"]=="GET")
    write=next(o for o in inv["operations"] if o["method"]=="POST")
    def b(target,kind,reference,step_id=None,response_status=None):
        return dict(target=target,kind=kind,reference=reference,step_id=step_id,response_status=response_status)
    return dict(name="Record service request",description="Lookup a record and propose creating a request",business_purpose="Help the business review service-request capability",configuration=[],questions=[dict(id="q1",text="How will ownership be verified?",configuration_key=None)],steps=[dict(id="s1",operation_id=read["id"],purpose="Read record",bindings=[b("path.record_id","runtime_argument","record_id")]),dict(id="s2",operation_id=write["id"],purpose="Create request",bindings=[b("body.record_id","previous_operation_output","/record_id","s1","200"),b("body.message","runtime_argument","message")])],outputs=[dict(name="request",step_id="s2",response_status="201",pointer="")],expected_reads=["Record"],expected_writes=["Service request"],assumptions=[],limitations=["Declared ownership dependency is unverified"],risk="medium",risk_rationale="Reads records and creates a request")


def test_routes_prefixes_models_dependencies_evidence_and_read_only(project):
    target,settings=project;before=hash_tree(target)
    inv=inspect(project)
    assert inv["valid"],inv["diagnostics"]
    assert {(o["method"],o["path"]) for o in inv["operations"]}=={("GET","/api/records/{record_id}"),("POST","/api/records/requests")}
    read=next(o for o in inv["operations"] if o["method"]=="GET")
    assert read["dependencies"][0]["symbol"]=="check_owner"
    assert read["dependencies"][0]["evidence_ids"]
    assert read["inputs"]["path.record_id"]["required"]
    assert read["responses"]["200"]["schema"]["required"]==["record_id","status"]
    assert read["call_trace"]==[{"file":"app/services.py","symbol":"lookup_record"}]
    for eid in read["evidence_ids"]:
        e=inv["evidence"][eid]
        assert e["source_hash"]==before[e["file"]] and 1<=e["line_start"]<=e["line_end"]
    assert not validate_proposal(ProposalContent(**proposal(inv)),inv)["blockers"]
    assert hash_tree(target)==before
    assert not list(target.rglob('__pycache__'))


def test_dynamic_prefix_unresolved_not_guessed(project):
    target,_=project
    p=target/"app/main.py";p.write_text(p.read_text().replace('prefix="/api"','prefix=settings.API_PREFIX'))
    inv=inspect(project)
    assert not inv["valid"]
    assert all(o["path"] is None and not o["supported"] for o in inv["operations"])
    assert any(d["code"]=="unresolved_operation" for d in inv["diagnostics"])


def test_unregistered_router_and_dynamic_registration_reported(project):
    target,_=project
    p=target/"app/main.py";p.write_text('from fastapi import FastAPI\napp=FastAPI()\napp.add_api_route(dynamic_path, handler)\n')
    inv=inspect(project)
    assert not inv["valid"]
    assert all(o["path"] is None for o in inv["operations"])
    assert any(d["code"]=="unsupported_dynamic_registration" for d in inv["diagnostics"])


def test_unknown_schema_blocks_only_that_operation_and_cannot_be_bound(project):
    target,_=project;p=target/"app/routes.py";p.write_text(p.read_text().replace('response_model=Record','response_model=Unknown'))
    inv=inspect(project)
    assert inv["valid"] and inv["coverage"]=="partial"
    with pytest.raises(AppError):validate_proposal(ProposalContent(**proposal(inv)),inv)


def test_forged_source_evidence_is_rejected(project):
    inv=inspect(project);p=proposal(inv)
    eid=inv["operations"][0]["evidence_ids"][0]
    inv["evidence"][eid]["source_hash"]="f"*64
    with pytest.raises(AppError,match="grounding"):validate_proposal(ProposalContent(**p),inv)


def test_retired_implementation_lives_in_legacy_behind_a_compatible_shim(project,monkeypatch):
    import team_c.code_discovery as shim
    import team_c.legacy.code_discovery as legacy
    public=[n for n in vars(legacy) if not n.startswith("_")]
    assert {"index_project","discover_project","parse_source","validate_setting_values","validate_code_provenance"}<=set(public)
    assert all(getattr(shim,n) is getattr(legacy,n) for n in public)
    inv=inspect(project);p=ProposalContent(**proposal(inv))
    assert inv["source_kind"]=="code" and validate_proposal(p,inv)
    def refuse(op,inventory):raise ValueError("checked by the legacy module")
    monkeypatch.setattr(legacy,"validate_code_provenance",refuse)
    with pytest.raises(AppError) as e:validate_proposal(p,inv)
    assert any("checked by the legacy module" in m for m in e.value.details["errors"])


def test_no_target_execution_secrets_or_dependency_tree_reads(project):
    target,_=project
    marker=target/"EXECUTED"
    p=target/"app/main.py";p.write_text(f"open({str(marker)!r},'w').write('bad')\n"+p.read_text()+"\nsecret_value='PRIVATE_SECRET'\n")
    (target/".env").write_text("PRIVATE_ENV")
    (target/"credentials.py").write_text("raise RuntimeError('PRIVATE_CREDENTIAL')")
    (target/".venv").mkdir();(target/".venv/a.py").write_text("PRIVATE_DEPENDENCY")
    inv=inspect(project)
    encoded=json.dumps(inv)
    assert all(s not in encoded for s in ("PRIVATE_ENV","PRIVATE_SECRET","PRIVATE_CREDENTIAL","PRIVATE_DEPENDENCY"))
    assert not marker.exists()


def test_boundaries_disabled_missing_and_parent_traversal(project,tmp_path):
    target,settings=project
    for path in (str(tmp_path),str(target/'../../outside')):
        with pytest.raises(AppError) as e:index_project(path,settings)
        assert e.value.code=="project_boundary"
    with pytest.raises(AppError) as e:index_project(str(target/'missing'),settings)
    assert e.value.code=="project_inaccessible"
    settings.allowed_project_directory=""
    with pytest.raises(AppError) as e:index_project(str(target),settings)
    assert e.value.code=="project_directory_configuration"


def test_symlink_escape_is_not_followed(project,tmp_path):
    target,settings=project;outside=tmp_path/'outside';outside.mkdir();(outside/'steal.py').write_text("SECRET_OUTSIDE")
    try:(target/'linked').symlink_to(outside,target_is_directory=True)
    except OSError:
        import os,subprocess
        if os.name!='nt':pytest.skip('OS does not permit creating test symlinks')
        quote=lambda value:"'"+str(value).replace("'","''")+"'"
        command="New-Item -ItemType Junction -Path "+quote(target/'linked')+" -Target "+quote(outside)
        result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True)
        if result.returncode:pytest.skip('OS does not permit creating test links')
    indexed=index_project(str(target),settings)
    assert not any('linked' in p for p in indexed['files'])
    assert any(d['code']=='link_excluded' for d in indexed['diagnostics'])
    with pytest.raises(AppError):index_project(str(target/'linked'),settings)


def test_limits_report_partial_coverage(project):
    _,settings=project;settings.code_max_files=1
    indexed=index_project(str(project[0]),settings)
    assert len(indexed['files'])==1 and any(d['code']=='file_limit' for d in indexed['diagnostics'])


def test_nested_include_prefixes_annotated_dependency_and_field_constraints(project):
    target,_=project
    (target/'app/deps.py').write_text('from typing import Annotated\nfrom fastapi import Depends\ndef require_guest():\n    return None\nGuest = Annotated[str, Depends(require_guest)]\n')
    (target/'app/outer.py').write_text('from fastapi import APIRouter\nfrom .routes import router\nouter = APIRouter(prefix="/v1")\nouter.include_router(router, prefix="/desk")\n')
    (target/'app/main.py').write_text('from fastapi import FastAPI\nfrom .outer import outer\napp=FastAPI()\napp.include_router(outer,prefix="/api")\n')
    p=target/'app/routes.py';p.write_text('from .deps import Guest\n'+p.read_text().replace('def get_record(record_id: str):','def get_record(record_id: str, guest: Guest):'))
    p=target/'app/models.py';p.write_text(p.read_text().replace('from pydantic import BaseModel','from pydantic import BaseModel, Field').replace('message: str','message: str = Field(min_length=1, max_length=500)'))
    inv=inspect(project)
    assert inv['valid'],inv['diagnostics']
    read=next(o for o in inv['operations'] if o['method']=='GET')
    assert read['path']=='/api/v1/desk/records/{record_id}'
    guest=next(d for d in read['dependencies'] if d['symbol']=='require_guest')
    assert inv['evidence'][guest['evidence_ids'][0]]['file']=='app/deps.py'
    assert 'query.guest' not in read['inputs']
    write=next(o for o in inv['operations'] if o['method']=='POST')
    assert write['inputs']['body.message']['schema']['maxLength']==500


def test_models_with_custom_validation_are_unresolved(project):
    target,_=project
    p=target/'app/models.py';p.write_text(p.read_text().replace('    status: str','    status: str\n    def validate_record(self):\n        return self'))
    inv=inspect(project)
    read=next(o for o in inv['operations'] if o['method']=='GET')
    assert not read['supported'] and any('custom methods' in u for u in read['unresolved'])


def test_entry_limits_and_oversize_files_explicit(project):
    target,settings=project;settings.code_max_entries=1
    indexed=index_project(str(target),settings)
    assert any(d['code']=='entry_limit' for d in indexed['diagnostics'])
    settings.code_max_entries=3000;settings.code_max_file_bytes=30
    indexed=index_project(str(target),settings)
    assert not indexed['files'] and any(d['code']=='file_size_limit' for d in indexed['diagnostics'])


def test_operation_id_stable_when_lines_change_and_snapshot_invalidates(project):
    before=inspect(project);p=project[0]/'app/routes.py';p.write_text('# changed line\n'+p.read_text());after=inspect(project)
    assert before['snapshot_id']!=after['snapshot_id']
    assert {o['id'] for o in before['operations']}=={o['id'] for o in after['operations']}


def test_legacy_code_records_are_read_only(project,tmp_path):
    from fastapi.testclient import TestClient
    from team_c.web import create_app
    target,settings=project;settings.database_path=str(tmp_path/'store.db');settings.session_secret='test'
    app=create_app(settings)
    with TestClient(app) as client:
        service=app.state.service
        bid=service.business('Code business','Legacy record')['id']
        spec=service.local_project(bid,str(target))  # stands in for a record created before retirement
        assert client.post(f'/api/v1/businesses/{bid}/local-projects',json={'path':str(target)}).status_code==410
        assert client.post(f"/api/v1/specifications/{spec['id']}/code-analysis").status_code==410
        generated=client.post(f"/api/v1/inventories/{spec['id']}/proposal-runs")
        assert generated.status_code==409 and generated.json()['code']=='legacy_code_inventory'
        with pytest.raises(AppError) as e:service.analyze_code(spec['id'])
        assert e.value.code=='legacy_code_inventory'
        assert client.get(f"/api/v1/specifications/{spec['id']}/inventory").json()['inventory']==spec['inventory']
        eid=next(iter(spec['inventory']['evidence']))
        assert client.get(f"/api/v1/specifications/{spec['id']}/evidence/{eid}").status_code==200
        page=client.get('/specifications/'+spec['id']).text
        assert 'Inspectable code evidence' in page and 'read-only' in page and 'actions/generate' not in page
        business=client.get('/businesses/'+bid).text
        assert 'actions/local_project' not in business and 'actions/fetch' in business


