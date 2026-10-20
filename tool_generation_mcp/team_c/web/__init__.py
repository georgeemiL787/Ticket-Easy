"""Browser UI: HTML pages, form actions and templates. Also the historical import path of create_app (`uvicorn team_c.web:create_app --factory`)."""
from .forms import field_kind, form_arguments


def __getattr__(name):
    # Lazy: team_c.app imports this package's modules, so importing it eagerly here would be circular.
    if name=="create_app":
        from ..app import create_app
        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
