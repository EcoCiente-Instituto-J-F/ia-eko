from __future__ import annotations

import asyncio

import pytest

from src.core.config import Settings
from src.services.session_service import (
    SessionForbidden,
    SessionService,
    count_conversation_messages,
)


class RecordingSummarizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, list[dict]]] = []

    async def summarize(self, previous_summary: str | None, messages: list[dict]) -> str:
        snapshot = [dict(message) for message in messages]
        self.calls.append((previous_summary, snapshot))
        parts = [previous_summary] if previous_summary else []
        parts.extend(f"{message['role']}:{message['content']}" for message in messages)
        return " || ".join(part for part in parts if part)


class FailingSummarizer:
    async def summarize(self, previous_summary: str | None, messages: list[dict]) -> str:
        raise RuntimeError("falha simulada")


class SlowSummarizer(RecordingSummarizer):
    async def summarize(self, previous_summary: str | None, messages: list[dict]) -> str:
        await asyncio.sleep(0.01)
        return await super().summarize(previous_summary, messages)


class _MongoStub:
    database = None


class _RedisStub:
    client = None


def make_service(*, max_messages: int = 20, keep_recent: int = 6) -> SessionService:
    settings = Settings(
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        storage_mode="memory",
        memory_max_messages=max_messages,
        memory_keep_recent_messages=keep_recent,
    )
    return SessionService(settings, _MongoStub(), _RedisStub())  # type: ignore[arg-type]


async def seed_messages(service: SessionService, session_id: str, user_id: int, count: int, *, start: int = 1) -> None:
    for index in range(start, start + count):
        role = "user" if index % 2 else "assistant"
        await service.append_message(session_id, user_id, role, f"m{index}")


@pytest.mark.asyncio
async def test_less_than_20_messages_does_not_compact() -> None:
    service = make_service()
    session = await service.create_session(1)
    await seed_messages(service, session["session_id"], 1, 18)
    summarizer = RecordingSummarizer()

    compacted = await service.compact_memory_if_needed(session["session_id"], 1, summarizer)

    assert compacted is False
    assert summarizer.calls == []
    stored = await service.get_session(session["session_id"], 1)
    assert len(stored["messages"]) == 18
    assert stored["memory_summary"] == ""


@pytest.mark.asyncio
async def test_exactly_20_messages_does_not_compact() -> None:
    service = make_service()
    session = await service.create_session(1)
    await seed_messages(service, session["session_id"], 1, 20)
    summarizer = RecordingSummarizer()

    compacted = await service.compact_memory_if_needed(session["session_id"], 1, summarizer)

    assert compacted is False
    assert summarizer.calls == []
    assert len((await service.get_session(session["session_id"], 1))["messages"]) == 20


@pytest.mark.asyncio
async def test_21_messages_triggers_compaction() -> None:
    service = make_service()
    session = await service.create_session(1)
    await seed_messages(service, session["session_id"], 1, 21)
    summarizer = RecordingSummarizer()

    compacted = await service.compact_memory_if_needed(session["session_id"], 1, summarizer)

    assert compacted is True
    assert len(summarizer.calls) == 1
    assert len(summarizer.calls[0][1]) == 15
    stored = await service.get_session(session["session_id"], 1)
    assert len(stored["messages"]) == 6
    assert stored["memory_summary"]


def test_count_conversation_messages_ignores_internal_roles() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "tool", "content": "t"},
        {"role": "assistant", "content": "a"},
        {"role": "function", "content": "f"},
    ]
    assert count_conversation_messages(messages) == 2


@pytest.mark.asyncio
async def test_compaction_preserves_recent_messages_integrally() -> None:
    service = make_service()
    session = await service.create_session(1)
    await seed_messages(service, session["session_id"], 1, 21)
    before = (await service.get_session(session["session_id"], 1))["messages"]
    expected = [(item["role"], item["content"]) for item in before[-6:]]

    await service.compact_memory_if_needed(session["session_id"], 1, RecordingSummarizer())

    after = (await service.get_session(session["session_id"], 1))["messages"]
    assert [(item["role"], item["content"]) for item in after] == expected


@pytest.mark.asyncio
async def test_summary_is_incremental() -> None:
    service = make_service()
    session = await service.create_session(1)
    summarizer = RecordingSummarizer()
    sid = session["session_id"]

    await seed_messages(service, sid, 1, 21)
    await service.compact_memory_if_needed(sid, 1, summarizer)
    summary_1 = (await service.get_session(sid, 1))["memory_summary"]

    # Restaram 6; mais 15 mensagens fazem a janela voltar a 21.
    await seed_messages(service, sid, 1, 15, start=22)
    await service.compact_memory_if_needed(sid, 1, summarizer)

    assert len(summarizer.calls) == 2
    assert summarizer.calls[1][0] == summary_1
    summary_2 = (await service.get_session(sid, 1))["memory_summary"]
    assert summary_1 in summary_2


@pytest.mark.asyncio
async def test_memory_context_recovers_summary_and_recent_messages() -> None:
    service = make_service()
    session = await service.create_session(1)
    sid = session["session_id"]
    await seed_messages(service, sid, 1, 21)
    await service.compact_memory_if_needed(sid, 1, RecordingSummarizer())

    context = await service.get_memory_context(sid, 1)

    assert context["memory_summary"]
    assert len(context["recent_messages"]) == 6
    assert context["conversation_message_count"] == 6


@pytest.mark.asyncio
async def test_current_user_message_is_not_duplicated_in_agent_context() -> None:
    service = make_service()
    session = await service.create_session(1)
    sid = session["session_id"]
    await service.append_message(sid, 1, "user", "pergunta atual")

    context = await service.get_memory_context(sid, 1, current_user_message="pergunta atual")
    stored = await service.get_session(sid, 1)

    assert context["recent_messages"] == []
    assert sum(1 for message in stored["messages"] if message["content"] == "pergunta atual") == 1
    assert context["conversation_message_count"] == 1


@pytest.mark.asyncio
async def test_summarizer_failure_preserves_all_messages_and_summary() -> None:
    service = make_service()
    session = await service.create_session(1)
    sid = session["session_id"]
    await seed_messages(service, sid, 1, 21)
    before = await service.get_session(sid, 1)

    with pytest.raises(RuntimeError, match="falha simulada"):
        await service.compact_memory_if_needed(sid, 1, FailingSummarizer())

    after = await service.get_session(sid, 1)
    assert after["memory_summary"] == before["memory_summary"]
    assert after["messages"] == before["messages"]
    assert after["memory_revision"] == before["memory_revision"]


@pytest.mark.asyncio
async def test_sessions_are_isolated() -> None:
    service = make_service()
    session_a = await service.create_session(1)
    session_b = await service.create_session(1)
    await seed_messages(service, session_a["session_id"], 1, 21)
    await service.compact_memory_if_needed(session_a["session_id"], 1, RecordingSummarizer())

    context_a = await service.get_memory_context(session_a["session_id"], 1)
    context_b = await service.get_memory_context(session_b["session_id"], 1)

    assert context_a["memory_summary"]
    assert context_b["memory_summary"] == ""
    assert context_b["recent_messages"] == []


@pytest.mark.asyncio
async def test_users_are_isolated() -> None:
    service = make_service()
    session = await service.create_session(10)

    with pytest.raises(SessionForbidden):
        await service.get_memory_context(session["session_id"], 11)


@pytest.mark.asyncio
async def test_concurrent_compactions_do_not_overwrite_summary() -> None:
    service = make_service()
    session = await service.create_session(1)
    sid = session["session_id"]
    await seed_messages(service, sid, 1, 21)
    summarizer = SlowSummarizer()

    results = await asyncio.gather(
        service.compact_memory_if_needed(sid, 1, summarizer),
        service.compact_memory_if_needed(sid, 1, summarizer),
    )

    assert sorted(results) == [False, True]
    assert len(summarizer.calls) == 1
    stored = await service.get_session(sid, 1)
    assert stored["memory_revision"] == 1
    assert len(stored["messages"]) == 6


def test_memory_settings_reject_invalid_window() -> None:
    with pytest.raises(ValueError):
        Settings(memory_max_messages=20, memory_keep_recent_messages=20)
