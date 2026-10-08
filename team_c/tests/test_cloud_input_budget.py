"""A cloud primary must size its batches against a real input allowance.

Regression: `input_fits` returned True for any cloud primary that stayed under `max_model_chars`,
so an operation index was built to a character budget that a credential's token allowance could not
accept. Every request was then sent anyway, answered HTTP 413, and only advanced to a fallback
after spending all configured credentials.
"""
import pytest

from team_c.config import Settings
from team_c.llm.budget import CLOUD_CHARS_PER_TOKEN, cloud_budget, input_ceiling, input_fits
from team_c.llm.prompts import model_messages


def payload(description="x"):
    return dict(business=dict(description=description))


def test_cloud_budget_counts_input_tokens_from_characters():
    messages = [{"role": "user", "content": "x" * 3000}]
    budget = cloud_budget(messages, 24000)
    # Dense content is bounded at the measured characters-per-token floor, not an optimistic chars/4.
    assert budget["input_chars"] == 3000
    assert budget["input_tokens_upper_bound"] == pytest.approx(3000 / CLOUD_CHARS_PER_TOKEN, abs=1)
    assert budget["fits"] is True
    assert cloud_budget(messages, 1000)["fits"] is False


def test_generation_system_prompt_alone_consumes_most_of_a_small_allowance():
    """The generation prompt carries a large fixed system prompt before any inventory.

    Sizing batches on the payload alone understates the request, which is why an allowance must be
    applied to the complete message set.
    """
    settings = Settings(llm_primary="groq", llm_fallback="none", groq_input_tokens=100)
    settings.max_model_chars = 10_000_000
    _, messages = model_messages("generation", dict(business=dict(description="")))
    empty = cloud_budget(messages, input_ceiling(settings, "groq"))
    assert empty["input_chars"] > 10000
    # A small allowance cannot admit the fixed prompt, whatever the business supplies.
    assert not input_fits(settings, "generation", payload("x" * 100))


def test_a_reasonable_allowance_is_not_mistaken_for_an_oversized_prompt():
    """A prompt-heavy request measured ~3.8 characters per token against a real provider.

    An over-conservative estimate would starve the operation index and report the capability as
    absent, so an allowance that comfortably holds the fixed prompt must still admit operations.
    """
    settings = Settings(llm_primary="groq", llm_fallback="none", groq_input_tokens=8000)
    settings.max_model_chars = 10_000_000
    assert input_fits(settings, "request_triage", payload("x" * 580))


def test_cloud_budget_without_a_ceiling_never_claims_a_fit():
    assert cloud_budget(model_messages("generation", payload())[1], None)["fits"] is False


@pytest.mark.parametrize("primary", ["groq", "openrouter"])
def test_oversized_request_does_not_fit_the_cloud_primary(primary):
    settings = Settings(llm_primary=primary, llm_fallback="none")
    ceiling = input_ceiling(settings, primary)
    assert ceiling
    settings.max_model_chars = 10_000_000
    assert not input_fits(settings, "generation", payload("x" * int(ceiling * CLOUD_CHARS_PER_TOKEN)))


@pytest.mark.parametrize("primary", ["groq", "openrouter"])
def test_small_request_still_fits_the_cloud_primary(primary):
    settings = Settings(llm_primary=primary, llm_fallback="none")
    # A generation prompt carries a large fixed system prompt; the ceiling must still admit a
    # minimal request rather than rejecting everything.
    assert cloud_budget(model_messages("generation", payload("x" * 100))[1],
                        input_ceiling(settings, primary))["fits"] is True


def test_ollama_primary_is_unaffected_by_cloud_ceilings():
    # ollama_context is pinned so the assertion does not depend on a developer's local .env.
    settings = Settings(llm_primary="ollama", llm_fallback="none", ollama_context=32768)
    settings.max_model_chars = 10_000_000
    assert not input_fits(settings, "generation", payload("x" * 9500))
    assert input_fits(settings, "generation", payload("x" * 100))


def test_measured_dense_index_does_not_fit_an_8000_token_allowance():
    """The live failure: a ~15600-character request was measured by Groq at 9370 tokens against an
    8000-token allowance, so all four credentials answered HTTP 413 before any fallback was tried.

    Batches must now be refused up front instead of being sent.
    """
    settings = Settings(llm_primary="groq", llm_fallback="openrouter,ollama",
                        groq_input_tokens=8000, max_model_chars=10_000_000)
    assert not input_fits(settings, "generation", payload("x" * 15635))
    settings.groq_input_tokens = 20000
    assert input_fits(settings, "generation", payload("x" * 15635))