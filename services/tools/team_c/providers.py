"""Compatibility imports: model calls live in team_c.llm."""
from pydantic import ValidationError  # noqa: F401 (re-exported)
from .config import AppError  # noqa: F401 (re-exported)
from .diagnostics import response_diagnostic, output_diagnostic  # noqa: F401 (re-exported)
from .progress import LIVE, check_cancelled, live_step  # noqa: F401 (re-exported)
from .llm.prompts import SYSTEM, CODE_SYSTEM, AREA_SYSTEM, eligible, model_payload, model_messages  # noqa: F401 (re-exported)
from .llm.budget import (OLLAMA_NUM_PREDICT, OLLAMA_TEMPLATE_BYTES, OLLAMA_MARGIN_TOKENS, GENERATION_NUM_PREDICT, PROPOSAL_KINDS,  # noqa: F401 (re-exported)
                         CLOUD_CHARS_PER_TOKEN, CLOUD_MARGIN_TOKENS, THINK_KINDS, base_kind, num_predict, ollama_budget,
                         cloud_budget, input_ceiling, input_fits)
from .llm.schemas import strict_schema, response_pointers, constrain, drop_echoes  # noqa: F401 (re-exported)
from .llm.ollama import ollama_stream  # noqa: F401 (re-exported)
from .llm.router import Providers  # noqa: F401 (re-exported)
