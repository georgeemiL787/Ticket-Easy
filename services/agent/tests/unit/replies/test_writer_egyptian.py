"""The Egyptian-Arabic writer: routing by locale, plain-text answers, and the same checks as the main writer."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_service.adapters.llm import OpenAICompatibleLLM
from agent_service.adapters.llm_registry import LLMRegistry, RoleLLM
from agent_service.agent.reviewer import Reviewer
from agent_service.config import Settings
from agent_service.container import build_container
from agent_service.contracts.errors import UpstreamError
from agent_service.domain.agent import ReplyPlan
from agent_service.domain.decision import Decision
from agent_service.domain.understanding import Locale
from agent_service.observability import ai_events
from agent_service.replies.writer import ReplyWriter, plain_reply
from tests.fakes import FakeLLM
from tests.support import make_settings

STATUS = "Your order NS-20877 is on its way. It is expected on 2026-09-24."
AR_GOOD = "طلبك NS-20877 في الطريق، ومتوقع يوصلك يوم 2026-09-24."
AR_BAD = "طلبك NS-20877 في الطريق ومعاه 3 هدايا يوم 2026-09-24."
EN_GOOD = {"text": "Good news: order NS-20877 is on the way, due on 2026-09-24."}
PLAN = ReplyPlan(
    decision=Decision.ANSWER, intent="tell where the order is", facts={"order_id": "NS-20877", "date": "2026-09-24"}
)


async def write(writer: ReplyWriter, locale: Locale) -> Any:
    return await writer.write(PLAN, STATUS, locale, message="فين طلبي؟")


# ---- routing ----


@pytest.mark.parametrize("locale", [Locale.AR, Locale.ARABIZI])
async def test_arabic_and_arabizi_replies_are_written_by_the_egyptian_model(locale: Locale) -> None:
    main, egyptian = FakeLLM(EN_GOOD), FakeLLM(AR_GOOD)
    out = await write(ReplyWriter(main, egyptian_llm=egyptian), locale)
    assert out.text == AR_GOOD and out.status == "used"
    assert main.calls == [] and len(egyptian.calls) == 1 and egyptian.calls[0]["plain"]


async def test_english_replies_still_use_the_main_writer() -> None:
    main, egyptian = FakeLLM(EN_GOOD), FakeLLM(AR_GOOD)
    out = await write(ReplyWriter(main, egyptian_llm=egyptian), Locale.EN)
    assert out.text == EN_GOOD["text"] and egyptian.calls == []


async def test_without_an_egyptian_model_nothing_changes() -> None:
    main = FakeLLM(EN_GOOD)
    writer = ReplyWriter(main)
    out = await write(writer, Locale.AR)
    assert out.text == EN_GOOD["text"] and not main.calls[0].get("plain") and not writer.writes_egyptian(Locale.AR)


def test_writes_egyptian_says_which_replies_use_it() -> None:
    writer = ReplyWriter(FakeLLM(), egyptian_llm=FakeLLM())
    assert [writer.writes_egyptian(x) for x in (Locale.EN, Locale.AR, Locale.ARABIZI)] == [False, True, True]


async def test_the_same_prompt_and_the_register_go_to_the_egyptian_model() -> None:
    main, egyptian = FakeLLM(EN_GOOD), FakeLLM(AR_GOOD)
    await write(ReplyWriter(main, egyptian_llm=egyptian), Locale.AR)
    call = egyptian.calls[0]
    assert "Egyptian" in call["system"] and "NS-20877" in call["user"] and "only the text of the reply" in call["user"]


# ---- plain text ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (AR_GOOD, AR_GOOD),
        (f'"{AR_GOOD}"', AR_GOOD),
        (f"«{AR_GOOD}»", AR_GOOD),
        (f"الرد: {AR_GOOD}", AR_GOOD),
        (f"Reply: {AR_GOOD}", AR_GOOD),
        (f"```\n{AR_GOOD}\n```", AR_GOOD),
        (json.dumps({"text": AR_GOOD}, ensure_ascii=False), AR_GOOD),
        ("  \n ", None),
        ('{"other": 1}', None),
        ("", None),
    ],
)
def test_plain_reply_strips_quotes_labels_fences_and_json(raw: str, expected: str | None) -> None:
    assert plain_reply(raw) == expected


async def test_a_labelled_and_quoted_answer_is_accepted_end_to_end() -> None:
    out = await write(ReplyWriter(FakeLLM(), egyptian_llm=FakeLLM(f'الرد: "{AR_GOOD}"')), Locale.AR)
    assert out.text == AR_GOOD and out.status == "used"


async def test_a_client_without_a_plain_text_call_is_asked_for_json_instead() -> None:
    class JsonOnly:
        async def complete_json(self, **kw: Any) -> dict[str, Any]:
            return {"text": AR_GOOD}

    out = await write(ReplyWriter(FakeLLM(), egyptian_llm=JsonOnly()), Locale.ARABIZI)  # type: ignore[arg-type]
    assert out.text == AR_GOOD


# ---- the same checks ----


async def test_a_new_number_from_the_egyptian_model_is_caught_and_retried_with_the_problems() -> None:
    egyptian = FakeLLM(AR_BAD, AR_GOOD)
    out = await write(ReplyWriter(FakeLLM(), egyptian_llm=egyptian), Locale.AR)
    assert out.status == "retried" and out.text == AR_GOOD
    assert "Your last reply had these problems" in egyptian.calls[1]["user"]


async def test_two_bad_answers_mean_the_template_and_a_dead_model_too() -> None:
    assert (await write(ReplyWriter(FakeLLM(), egyptian_llm=FakeLLM(AR_BAD, AR_BAD)), Locale.AR)).text == STATUS
    assert (await write(ReplyWriter(FakeLLM(), egyptian_llm=FakeLLM(RuntimeError("down"))), Locale.AR)).text == STATUS


async def test_the_reviewer_checks_the_egyptian_text_too() -> None:
    reviewer_llm = FakeLLM({"ok": False, "problems": ["promises a refund"]}, {"ok": True, "problems": []})
    writer = ReplyWriter(FakeLLM(), reviewer=Reviewer(reviewer_llm), egyptian_llm=FakeLLM(AR_GOOD, AR_GOOD))
    out = await write(writer, Locale.AR)
    assert out.status == "retried" and out.review == "ok" and len(reviewer_llm.calls) == 2


# ---- the settings and the registry ----


def env(**extra: str) -> Settings:
    base = {"TEAM_B_LLM": "ollama", "TEAM_B_LLM_MODEL": "qwen3:8b", "TEAM_B_LLM_REWRITE": "1"}
    return Settings.from_env({**base, **extra})


EGYPTIAN_ENV = {
    "TEAM_B_LLM_BASE_URL_WRITER_EGYPTIAN": "http://127.0.0.1:11434/v1",
    "TEAM_B_LLM_MODEL_WRITER_EGYPTIAN": "hf.co/mradermacher/Nile-Chat-4B-GGUF:Q4_K_M",
}


def test_the_container_uses_the_egyptian_model_only_when_the_job_has_its_own_settings(tmp_path: Path) -> None:
    def writer_of(settings: Settings) -> ReplyWriter:
        built = build_container(make_settings(tmp_path, **dict(settings)))
        assert built.orchestrator is not None
        writer: Any = built.orchestrator._deps.writer
        assert isinstance(writer, ReplyWriter)
        return writer

    assert not writer_of(env()).writes_egyptian(Locale.AR)  # no settings of its own: it never borrows the general model
    assert writer_of(env(**EGYPTIAN_ENV)).writes_egyptian(Locale.AR)


async def test_the_http_client_asks_for_plain_text_with_no_json_format() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "اهلا بيك"}}]})

    llm = OpenAICompatibleLLM(base_url="http://ollama.test/v1/", model="nile", transport=httpx.MockTransport(handler))
    assert await llm.complete_text(system="s", user="u") == "اهلا بيك"
    assert "response_format" not in seen[0] and seen[0]["model"] == "nile"
    assert seen[0]["messages"][0]["content"] == "s"  # no "reply with one JSON object" line added


async def test_the_job_client_gives_plain_text_and_uses_the_backup_like_any_other_call() -> None:
    class Text:
        def __init__(self, *answers: str | Exception) -> None:
            self.answers = list(answers)

        async def complete_text(self, *, system: str, user: str, temperature: float = 0.0) -> str:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    main, backup = Text(UpstreamError("llm", "TIMEOUT", "slow")), Text("اهلا")
    llm = RoleLLM("writer_egyptian", main, backup, "backup")  # type: ignore[arg-type]
    with ai_events() as events:
        assert await llm.complete_text(system="s", user="u") == "اهلا"
    assert events[0][:2] == ("writer_egyptian", "backup_model")


def test_the_registry_builds_the_egyptian_job() -> None:
    reg = LLMRegistry(env(**EGYPTIAN_ENV))
    assert isinstance(reg.llm_for("writer_egyptian"), OpenAICompatibleLLM)
