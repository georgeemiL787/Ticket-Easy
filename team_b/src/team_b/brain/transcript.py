"""The conversation as it can be rebuilt from stored traces (what handoff shows a human)."""

from collections.abc import Sequence

from team_b.domain.handoff import TranscriptLine
from team_b.domain.trace import DecisionTrace


def transcript_from_traces(traces: Sequence[DecisionTrace]) -> list[TranscriptLine]:
    """Customer message then agent reply for every customer turn, oldest first. Empty texts are skipped."""
    lines: list[TranscriptLine] = []
    for trace in sorted((t for t in traces if t.kind == "customer_turn"), key=lambda t: t.turn_index):
        for role, text in (("customer", trace.customer_message), ("agent", trace.response_text)):
            if text:
                lines.append(
                    TranscriptLine(role=role, text=text, trace_id=trace.trace_id, turn_index=trace.turn_index)  # type: ignore[arg-type]
                )
    return lines
