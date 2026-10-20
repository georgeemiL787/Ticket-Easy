"""Browser form helpers: CSRF check and typed tool-argument fields."""
import json
import secrets
from ..config import AppError


def check_csrf(request,form):
    if not secrets.compare_digest(str(form.get("csrf","")),request.session.get("csrf","missing")):
        raise AppError("csrf_rejected","Reload the page and try again",403)


def field_kind(prop):
    t=prop.get("type")
    t=next((x for x in t if x!="null"),None) if isinstance(t,list) else t
    return t if t in ("string","integer","number","boolean") else "json"


def form_arguments(schema,form):
    """Tool arguments from one form field per declared input; empty fields are omitted."""
    data={}
    for name,prop in (schema.get("properties") or {}).items():
        raw=str(form.get("a:"+name,"")).strip()
        if not raw: continue
        kind=field_kind(prop)
        try:
            data[name]={"integer":int,"number":float,"boolean":lambda v:v=="true","json":json.loads}.get(kind,str)(raw)
        except ValueError:
            raise AppError("invalid_input",f"{prop.get('title') or name}: enter a valid {'JSON value' if kind=='json' else kind}")
    return data
