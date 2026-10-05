import json

import pytest

from team_b.observability import bind_context, clear_context, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _clean_context() -> None:
    clear_context()


def lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_json_logs_carry_the_bound_ids(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True)
    bind_context(tenant_id="shop_001", conversation_id="conv-1", request_id="req-1", trace_id="t-1")
    get_logger("test").info("hello", extra_field=3)
    (entry,) = lines(capsys)
    assert entry["event"] == "hello" and entry["level"] == "info" and entry["extra_field"] == 3
    assert (entry["tenant_id"], entry["conversation_id"]) == ("shop_001", "conv-1")
    assert (entry["request_id"], entry["trace_id"]) == ("req-1", "t-1")
    assert "timestamp" in entry


def test_binding_is_additive_and_none_is_ignored(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True)
    bind_context(request_id="req-1")
    bind_context(tenant_id="shop_001", request_id=None)
    get_logger("test").info("x")
    (entry,) = lines(capsys)
    assert entry["request_id"] == "req-1" and entry["tenant_id"] == "shop_001"
    assert "trace_id" not in entry


def test_clear_context_removes_the_ids(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=True)
    bind_context(tenant_id="shop_001")
    clear_context()
    get_logger("test").info("x")
    (entry,) = lines(capsys)
    assert "tenant_id" not in entry


def test_console_mode_is_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json_logs=False)
    get_logger("test").info("plain message")
    out = capsys.readouterr().out
    assert "plain message" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.strip().splitlines()[0])
    configure_logging(json_logs=True)


async def test_ids_are_isolated_between_concurrent_tasks(capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    configure_logging(json_logs=True)

    async def work(tenant: str) -> None:
        bind_context(tenant_id=tenant)
        await asyncio.sleep(0)
        get_logger("test").info("work", who=tenant)

    await asyncio.gather(work("shop_001"), work("shop_002"))
    for entry in lines(capsys):
        assert entry["tenant_id"] == entry["who"]
