"""One plain class per API operation for the owner: read, write, risky or restricted.

Combines what discovery already knows: the HTTP method, the exposure signals (credential and privileged operations are
restricted), and words that mark money or destruction. It decides nothing about permissions: the class only groups the
operations on screen and prefills the effect flags the owner must still confirm before a write tool is built.
"""
import re

from .capabilities import status

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MONEY_WORDS = {"refund", "refunds", "pay", "payment", "payments", "charge", "charges", "transfer", "transfers", "withdraw", "withdrawal",
               "payout", "payouts", "balance", "invoice", "invoices", "voucher", "vouchers", "price", "prices", "discount", "credit", "debit"}
DESTRUCTIVE_WORDS = {"delete", "remove", "cancel", "terminate", "purge", "revoke", "destroy", "close", "deactivate", "disable"}


def _words(op):
    pieces = [op.get("path", ""), op.get("original", {}).get("operationId", "") if isinstance(op.get("original"), dict) else "", op.get("operation_id") or ""]
    text = re.sub(r"\{[^}]*\}", " ", " ".join(str(p) for p in pieces))
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return {w for w in re.split(r"[^A-Za-z0-9]+", text.lower()) if w}


def _body_words(op):
    names = {key.partition(".")[2].lower() for key in op.get("inputs", {}) if key.startswith("body.")}
    return {w for name in names for w in re.split(r"[^a-z0-9]+", name) if w}


def operation_class(op):
    """{class, selectable, why, reason_unusable, suggested_effects} for one inventory operation."""
    state = status(op)
    method = op.get("method", "GET")
    words = _words(op)
    money = bool((words | _body_words(op)) & MONEY_WORDS)
    destructive = method == "DELETE" or bool(words & DESTRUCTIVE_WORDS) or any(s.get("kind") == "destructive" for s in op.get("exposure", {}).get("signals", []))
    why = []
    if state == "restricted":
        kind = "restricted"
        why.append("handles sign-in, credentials or administrator access")
    elif method in SAFE_METHODS:
        kind = "read"
        why.append("only looks things up")
    elif money or destructive:
        kind = "risky"
        if money:
            why.append("moves or changes money")
        if destructive:
            why.append("can not be undone easily")
    else:
        kind = "write"
        why.append("changes data")
    write = method not in SAFE_METHODS
    reason = None
    if state == "unsupported":
        from .capabilities import reason as unusable
        reason = unusable(op)
    return dict(**{"class": kind}, selectable=state == "eligible", why=why, reason_unusable=reason,
                suggested_effects=dict(financial=money and write, irreversible=destructive and write, external_side_effects=write,
                                       idempotent=(not write) or method in ("PUT", "DELETE")))


def operation_row(op):
    """The compact row the owner's selection screen shows (no schemas, no original document)."""
    info = operation_class(op)
    return dict(id=op["id"], method=op["method"], path=op["path"], title=op.get("business_label") or op.get("summary") or f'{op["method"]} {op["path"]}',
                summary=(op.get("summary") or op.get("description") or "")[:200], tags=op.get("tags", []), **info)
