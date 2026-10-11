"""The outcomes a message can end in, and the named reasons for handing off to a human."""

from enum import StrEnum


class Decision(StrEnum):
    """Every customer message ends in exactly one of these."""

    ANSWER = "answer"
    CLARIFY = "clarify"
    VERIFY_IDENTITY = "verify_identity"
    CONFIRM = "confirm"
    EXECUTE = "execute"
    REFUSE = "refuse"
    HANDOFF = "handoff"


class EscalationReason(StrEnum):
    """Why a conversation goes to a human. Each one has a priority and a suggested next step (later prompts)."""

    CUSTOMER_REQUEST = "customer_request"
    MANDATORY_RISK = "mandatory_risk"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    REPEATED_TOOL_FAILURE = "repeated_tool_failure"
    UNVERIFIED_RESULT = "unverified_result"
    NO_EVIDENCE = "no_evidence"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    LOW_CONFIDENCE = "low_confidence"
    IDENTITY_FAILED = "identity_failed"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    HIGH_FRUSTRATION = "high_frustration"
    CAPABILITY_MISSING = "capability_missing"
    UNSUPPORTED = "unsupported"
