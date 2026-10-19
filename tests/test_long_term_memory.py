from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.core.config import Settings
from src.services.session_service import SessionService


class _Stub:
    database = None
    client = None


def make_service(**overrides) -> SessionService:
    settings = Settings(
        environment="test", llm_provider="mock", embedding_provider="mock", storage_mode="memory", **overrides
    )
    return SessionService(settings, _Stub(), _Stub())  # type: ignore[arg-type]


def test_user_memory_starts_empty():
    service = make_service()
    assert asyncio.run(service.get_user_memory(1)) == {"texto": "", "revision": 0, "updated_at": None}


def test_save_is_compare_and_set():
    service = make_service()
    assert asyncio.run(service.save_user_memory(1, "- tem composteira", expected_revision=0)) is True
    memoria = asyncio.run(service.get_user_memory(1))
    assert memoria["texto"] == "- tem composteira" and memoria["revision"] == 1
    assert asyncio.run(service.save_user_memory(1, "versão velha", expected_revision=0)) is False
    assert asyncio.run(service.get_user_memory(1))["texto"] == "- tem composteira"


def test_user_memory_is_per_user_and_deletable():
    service = make_service()
    asyncio.run(service.save_user_memory(1, "- tem composteira", expected_revision=0))
    assert asyncio.run(service.get_user_memory(2))["texto"] == ""
    asyncio.run(service.delete_user_memory(1))
    assert asyncio.run(service.get_user_memory(1))["texto"] == ""


def test_memory_context_includes_user_memory():
    service = make_service()
    session = asyncio.run(service.create_session(1))
    asyncio.run(service.save_user_memory(1, "- prefere respostas curtas", expected_revision=0))
    context = asyncio.run(service.get_memory_context(session["session_id"], 1))
    assert context["user_memory"] == "- prefere respostas curtas"


def test_memory_context_without_long_term_memory_when_disabled():
    service = make_service(long_term_memory_enabled=False)
    session = asyncio.run(service.create_session(1))
    asyncio.run(service.save_user_memory(1, "- prefere respostas curtas", expected_revision=0))
    assert asyncio.run(service.get_memory_context(session["session_id"], 1))["user_memory"] == ""


def test_mark_long_term_memory_is_stored_in_the_session():
    service = make_service()
    session = asyncio.run(service.create_session(1))
    quando = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    asyncio.run(service.mark_long_term_memory(session["session_id"], 1, quando))
    assert asyncio.run(service.get_session(session["session_id"], 1))["memoria_longa_ate"] == quando


@pytest.mark.parametrize("campo,valor", [("long_term_memory_every_turns", 0), ("long_term_memory_max_chars", 100)])
def test_settings_validate_long_term_memory(campo, valor):
    with pytest.raises(ValueError):
        Settings.from_env().with_overrides(**{campo: valor})
