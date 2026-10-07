"""Target-shaped OpenAPI 3.1 document, its inventory and an authored item-lookup proposal."""
import json
import uuid
from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from team_c.discovery import discover


def target_spec():
    """Small FastAPI app shaped like the observed target; FastAPI itself emits the OpenAPI 3.1 document."""
    app = FastAPI()
    token = OAuth2PasswordBearer(tokenUrl="/api/v1/login/access-token")
    class Token(BaseModel):
        access_token: str
        token_type: str = "bearer"
    class ItemUpdate(BaseModel):
        title: str | None = Field(default=None, min_length=1, max_length=255)
        description: str | None = Field(default=None, max_length=255)
    class ItemPublic(BaseModel):
        title: str = Field(min_length=1, max_length=255)
        description: str | None = None
        id: uuid.UUID
        owner_id: uuid.UUID
    class ItemsPublic(BaseModel):
        data: list[ItemPublic]
        count: int
    class UserRegister(BaseModel):
        email: str
        password: str = Field(min_length=8)
    @app.post("/api/v1/login/access-token", tags=["login"])
    def login_access_token(form: OAuth2PasswordRequestForm = Depends()) -> Token: ...
    @app.get("/api/v1/items/", tags=["items"])
    def read_items(t: str = Depends(token), skip: int = 0, limit: int = 100) -> ItemsPublic: ...
    @app.get("/api/v1/items/{id}", tags=["items"])
    def read_item(id: uuid.UUID, t: str = Depends(token)) -> ItemPublic: ...
    @app.put("/api/v1/items/{id}", tags=["items"])
    def update_item(id: uuid.UUID, item: ItemUpdate, t: str = Depends(token)) -> ItemPublic: ...
    @app.post("/api/v1/password-recovery-html-content/{email}", response_class=HTMLResponse, tags=["login"])
    def recover_password_html_content(email: str, t: str = Depends(token)): ...
    @app.post("/api/v1/users/signup", tags=["users"])
    def register_user(user: UserRegister): ...
    @app.get("/api/v1/utils/health-check/", tags=["utils"])
    def health_check() -> bool: ...
    return app.openapi()


def inventory(doc=None):
    return discover(json.dumps(doc or target_spec()).encode(), "openapi.json", "biz")[1]


def op(inv, method, path):
    return next(o for o in inv["operations"] if o["method"] == method and o["path"] == path)


def proposal(step_op, output):
    return dict(name="Item lookup", description="Look up an item", business_purpose="Help owners review items", configuration=[],
                questions=[dict(id="q1", text="Which callers may read items?", configuration_key=None)],
                steps=[dict(id="s1", operation_id=step_op["id"], purpose="Read item", bindings=[dict(target="path.id", kind="runtime_argument", reference="item_id", step_id=None, response_status=None)])],
                outputs=[output], expected_reads=["Item"], expected_writes=[], assumptions=[], limitations=["Authorization unverified"], risk="low", risk_rationale="Reads one item")
