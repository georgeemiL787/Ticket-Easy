"""Local appointment / service-request API: an independent second domain for Team C (test fixture only).

Expected behavior and access rules, fixed before any proposal is generated:
- Every business operation needs a bearer token; each token belongs to exactly one customer account.
- Booking lookup takes the external reference printed on a confirmation (required query parameter) and
  returns the booking with its internal identifier and its holder nested inside an envelope. It returns
  the booking to ANY authenticated caller who knows the reference (front-desk semantics), so the tool
  must not rely on the lookup for ownership.
- Filing a service request needs the internal booking identifier from the lookup, a category (enum) and a
  text. The backend answers 201 with a different envelope, 404 for an unknown booking, 403 when the caller
  does not hold the booking, 422 for invalid input.
- Attaching a file to a request uses multipart/form-data (intentionally outside Team C's supported subset).
- Cancelling a booking is not offered at all.
- Business rule for tools: a customer may file service requests only for bookings they hold.

Variants rename every path, parameter and field; the "renamed" variant also uses an integer holder number.
Test-harness routes (hidden from the OpenAPI document, protected by FIXTURE_HARNESS_KEY) create disposable
accounts/bookings and expose state for independent verification.
"""
import os
import secrets
import threading
import uuid
from enum import Enum
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, create_model

VARIANTS = {
    "base": dict(prefix="/v2", lookup="/bookings/lookup", ref="reference", create="/service-requests", attach="/service-requests/{ticket}/attachments",
                 lookup_env="result", record="booking", internal="internal_id", holder="holder", holder_id="account_id", holder_int=False,
                 create_env="data", created="service_request", ticket="ticket", state="state", body_id="booking_internal_id", category="category",
                 text="details", categories=["cleaning", "maintenance", "billing"], tag="bookings"),
    "renamed": dict(prefix="/care/api", lookup="/reservations/find", ref="code", create="/tickets", attach="/tickets/{ticket}/files",
                    lookup_env="payload", record="reservation", internal="reservation_key", holder="guest", holder_id="customer_no", holder_int=True,
                    create_env="created", created="ticket", ticket="number", state="status", body_id="reservation_key", category="kind",
                    text="message", categories=["housekeeping", "repair", "invoice"], tag="reservations"),
}


def create_app(variant=None, harness_key=None):
    n = VARIANTS[variant or os.environ.get("FIXTURE_VARIANT", "base")]
    key = harness_key or os.environ.get("FIXTURE_HARNESS_KEY") or secrets.token_urlsafe(24)
    lock, accounts, tokens, bookings, requests = threading.Lock(), {}, {}, {}, []
    bearer = HTTPBearer(auto_error=False)
    Category = Enum("Category", {c: c for c in n["categories"]}, type=str)
    HolderId = int if n["holder_int"] else uuid.UUID
    Holder = create_model("Holder", **{n["holder_id"]: (HolderId, ...), "display_name": (str, ...)})
    Record = create_model(n["record"].capitalize(), **{n["internal"]: (uuid.UUID, ...), n["ref"]: (str, ...), "service_date": (str, ...), n["holder"]: (Holder, ...)})
    LookupBody = create_model("LookupBody", **{n["record"]: (Record, ...)})
    Lookup = create_model("LookupResponse", **{n["lookup_env"]: (LookupBody, ...)})
    Create = create_model("ServiceRequestCreate", **{n["body_id"]: (uuid.UUID, ...), n["category"]: (Category, ...), n["text"]: (str, Field(min_length=1, max_length=500))})
    Created = create_model("ServiceRequest", **{n["ticket"]: (str, ...), n["state"]: (str, ...), n["body_id"]: (uuid.UUID, ...)})
    CreatedBody = create_model("CreatedBody", **{n["created"]: (Created, ...)})
    CreatedEnvelope = create_model("CreatedEnvelope", **{n["create_env"]: (CreatedBody, ...)})
    app = FastAPI(title=f"Service desk fixture ({variant or 'base'})", version="1.0.0")

    def caller(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        account = tokens.get(credentials.credentials) if credentials else None
        if account is None:
            raise HTTPException(401, "Not authenticated")
        return account

    def view(booking):
        a = accounts[booking["holder"]]
        return {n["lookup_env"]: {n["record"]: {n["internal"]: booking["id"], n["ref"]: booking["ref"], "service_date": booking["date"],
                                                n["holder"]: {n["holder_id"]: a["number"] if n["holder_int"] else a["id"], "display_name": a["name"]}}}}

    @app.get(n["prefix"] + n["lookup"], response_model=Lookup, tags=[n["tag"]], responses={404: {"description": "Unknown reference"}})
    def find_booking(value: str = Query(..., alias=n["ref"], min_length=4, max_length=40), account=Depends(caller)):
        booking = next((b for b in bookings.values() if b["ref"] == value), None)
        if booking is None:
            raise HTTPException(404, "Unknown reference")
        return view(booking)

    @app.post(n["prefix"] + n["create"], status_code=201, response_model=CreatedEnvelope, tags=["requests"],
              responses={403: {"description": "Not the booking holder"}, 404: {"description": "Unknown booking"}})
    def file_request(body: Create, account=Depends(caller)):
        data = body.model_dump()
        with lock:
            booking = bookings.get(str(data[n["body_id"]]))
            if booking is None:
                raise HTTPException(404, "Unknown booking")
            if booking["holder"] != account["id"]:
                raise HTTPException(403, "Not the booking holder")
            ticket = f"SR-{len(requests) + 1:04d}"
            requests.append(dict(ticket=ticket, booking=booking["id"], filed_by=account["id"], category=data[n["category"]].value, text=data[n["text"]]))
        return {n["create_env"]: {n["created"]: {n["ticket"]: ticket, n["state"]: "open", n["body_id"]: booking["id"]}}}

    @app.post(n["prefix"] + n["attach"], status_code=201, tags=["requests"])
    def attach_file(ticket: str, upload: UploadFile = File(...), account=Depends(caller)) -> dict[str, str]:
        if not any(r["ticket"] == ticket and r["filed_by"] == account["id"] for r in requests):
            raise HTTPException(404, "Unknown request")
        return {"stored": upload.filename or ""}

    @app.get("/health", tags=["health"])
    def health() -> bool:
        return True

    def harness(x_harness_key: str = Header(...)):
        if not secrets.compare_digest(x_harness_key, key):
            raise HTTPException(403, "Harness only")

    @app.post("/_harness/accounts", include_in_schema=False, dependencies=[Depends(harness)])
    def new_account(body: dict):
        with lock:
            account = dict(id=str(uuid.uuid4()), number=len(accounts) + 1001, name=str(body.get("name", "customer")))
            token = secrets.token_urlsafe(24)
            accounts[account["id"]], tokens[token] = account, account
        return dict(account, token=token)

    @app.post("/_harness/bookings", include_in_schema=False, dependencies=[Depends(harness)])
    def new_booking(body: dict):
        with lock:
            booking = dict(id=str(uuid.uuid4()), ref=f"BK-{secrets.token_hex(4).upper()}", date=str(body.get("date", "2026-11-02")), holder=body["account_id"])
            bookings[booking["id"]] = booking
        return booking

    @app.get("/_harness/state", include_in_schema=False, dependencies=[Depends(harness)])
    def state():
        with lock:
            return dict(bookings=list(bookings.values()), requests=list(requests))

    app.state.names = n
    return app
