from __future__ import annotations

import asyncio

import httpx
import pytest

from src.core.config import Settings
from src.integrations.a2a.client import ask_external_agent


def test_agent_card_is_published(client):
    r = client.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    card = r.json()
    assert card["name"] == "EcoCiente Educational Agent"
    assert any(skill["id"] == "educacao_ambiental" for skill in card["skills"])


def test_a2a_client_and_server_round_trip(client):
    async def run() -> str:
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as http:
            return await ask_external_agent(
                "http://127.0.0.1:8000", "Como separar resíduos recicláveis?", httpx_client=http
            )

    resposta = asyncio.run(run())
    assert resposta.startswith("Evidência EcoCiente:")
    assert "Fontes:" in resposta


def test_remote_agent_is_off_by_default():
    settings = Settings(environment="test", llm_provider="mock", embedding_provider="mock", storage_mode="memory")
    assert settings.a2a_remote_agent_url is None
    assert settings.a2a_remote_timeout_seconds == 8.0
