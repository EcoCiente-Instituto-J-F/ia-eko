"""FAQ por busca vetorial no Qdrant (FAQ_BACKEND=qdrant), sem LLM.

Usa o Qdrant em memória (`AsyncQdrantClient(location=":memory:")`) com a
mesma API do servidor real, e um embedder falso: o modelo e5 de ~2 GB não é
baixado nos testes.
"""

from __future__ import annotations

import asyncio

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import AsyncQdrantClient, models  # noqa: E402

from src.core.config import Settings  # noqa: E402
from src.services.qdrant_service import QdrantFaqService  # noqa: E402

DIM = 4
# Cada "tema" é um eixo; a pergunta aponta para o eixo do FAQ certo.
VETORES = {
    "validacao": [1.0, 0.0, 0.0, 0.0],
    "pontos": [0.0, 1.0, 0.0, 0.0],
    "sindico": [0.0, 0.0, 1.0, 0.0],
}


class FakeEmbedding:
    def __init__(self):
        self.calls: list[str] = []

    async def agerar_embedding(self, texto: str) -> list[float]:
        self.calls.append(texto)
        lower = texto.lower()
        if "valida" in lower:
            return VETORES["validacao"]
        if "ponto" in lower:
            return VETORES["pontos"]
        if "aprovar a entrada" in lower:
            return VETORES["sindico"]
        return [0.0, 0.0, 0.0, 1.0]  # nada parecido na base


def _settings(**overrides) -> Settings:
    base = dict(
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        storage_mode="memory",
        enable_external_source=False,
        postgres_url=None,
        allow_test_identity_headers=True,
        faq_backend="qdrant",
        qdrant_collection="faq",
        qdrant_min_score=0.80,
        rate_limit_per_minute=100,
    )
    base.update(overrides)
    return Settings.from_env().with_overrides(**base)


async def _seed(client: AsyncQdrantClient) -> None:
    await client.create_collection(
        "faq", vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE)
    )
    await client.upsert(
        "faq",
        points=[
            models.PointStruct(
                id=1,
                vector=VETORES["validacao"],
                payload={
                    "faq_id": "FAQ-001",
                    "titulo": "Validação de postagens",
                    "intencao": "entender_validacao",
                    "perfis_relacionados": [],
                    "resposta_canonica": "Cada postagem é validada pelos vizinhos em até 24 horas.",
                    "palavras_chave": ["validação", "postagem"],
                    "tipo": "regra",
                },
            ),
            models.PointStruct(
                id=2,
                vector=VETORES["pontos"],
                payload={
                    "faq_id": "FAQ-002",
                    "titulo": "Cálculo de pontos",
                    "perfis_relacionados": ["todos"],
                    "resposta_canonica": "Os pontos dependem da categoria do resíduo postado.",
                    "tipo": "regra",
                },
            ),
            models.PointStruct(
                id=3,
                vector=VETORES["sindico"],
                payload={
                    "faq_id": "FAQ-003",
                    "titulo": "Aprovação de moradores",
                    "perfis_relacionados": ["sindico"],
                    "resposta_canonica": "O síndico aprova a entrada pelo painel do condomínio.",
                    "tipo": "procedimento",
                },
            ),
        ],
    )


def _service(settings: Settings) -> tuple[QdrantFaqService, FakeEmbedding]:
    client = AsyncQdrantClient(location=":memory:")
    asyncio.run(_seed(client))
    embedder = FakeEmbedding()
    return QdrantFaqService(settings, embedding_service=embedder, client=client), embedder  # type: ignore[arg-type]


# ------------------------------------------------------------ serviço


def test_buscar_faq_returns_payload_fields_and_score() -> None:
    service, embedder = _service(_settings())
    faqs = asyncio.run(service.buscar_faq("Como funciona a validação?"))
    assert faqs[0]["faq_id"] == "FAQ-001"
    assert faqs[0]["resposta_canonica"].startswith("Cada postagem")
    assert faqs[0]["score"] == pytest.approx(1.0)
    assert set(faqs[0]) >= {"faq_id", "titulo", "intencao", "perfis_relacionados", "resposta_canonica", "palavras_chave", "tipo", "score"}
    assert embedder.calls == ["Como funciona a validação?"]


def test_verificar_collection() -> None:
    service, _ = _service(_settings())
    info = asyncio.run(service.verificar_collection())
    assert info == {"collection": "faq", "existe": True, "points_count": 3}
    assert asyncio.run(service.health()) == "ok"


def test_melhor_resposta_respects_min_score_and_profile() -> None:
    service, _ = _service(_settings())
    faqs = [
        {"faq_id": "a", "score": 0.95, "resposta_canonica": "só síndico", "perfis_relacionados": ["sindico"]},
        {"faq_id": "b", "score": 0.90, "resposta_canonica": "para todos", "perfis_relacionados": []},
        {"faq_id": "c", "score": 0.99, "resposta_canonica": "", "perfis_relacionados": []},
    ]
    assert service.melhor_resposta(faqs, "MORADOR_RESIDENCIAL")["faq_id"] == "b"
    assert service.melhor_resposta(faqs, "SINDICO_COMERCIAL")["faq_id"] == "a"  # "sindico" vale p/ os dois
    assert service.melhor_resposta([{**faqs[1], "score": 0.5}], "USUARIO_COMUM") is None


def test_missing_url_is_reported_not_raised_on_health() -> None:
    service = QdrantFaqService(_settings(qdrant_url=None))
    assert asyncio.run(service.health()) == "error"


# ---------------------------------------------------------------- /chat


@pytest.fixture()
def qdrant_client_app():
    pytest.importorskip("langgraph")
    from fastapi.testclient import TestClient

    from src.api.main import create_app

    def factory(**overrides):
        settings = _settings(**overrides)
        client = TestClient(create_app(settings))
        client.__enter__()
        service, embedder = _service(settings)
        client.app.state.graph.faq_search = service
        return client, embedder

    created = []

    def make(**overrides):
        client, embedder = factory(**overrides)
        created.append(client)
        return client, embedder

    yield make
    for client in created:
        client.__exit__(None, None, None)


def _chat(client, mensagem: str, perfil: str = "comum"):
    return client.post(
        "/api/v1/chat",
        headers={"X-Usuario-Id": "5", "X-Perfil": perfil},
        json={"mensagem": mensagem},
    )


def test_chat_answers_with_canonical_text_and_no_llm(qdrant_client_app) -> None:
    client, _ = qdrant_client_app()
    graph = client.app.state.graph
    called: list[str] = []
    original = graph.agents.invoke

    async def spy(agent_name, prompt):
        called.append(agent_name)
        return await original(agent_name, prompt)

    graph.agents.invoke = spy
    body = _chat(client, "Como funciona a validação das postagens?").json()

    assert body["agent"] == "faq"
    assert body["answer"] == "Cada postagem é validada pelos vizinhos em até 24 horas."
    assert body["sources"][0]["source"] == "qdrant:faq/FAQ-001"
    assert body["judge"]["motivo"] == "resposta_canonica_qdrant"
    # Nem o agente faq nem o juiz de saída chamaram LLM.
    assert "faq" not in called and "juiz_saida" not in called


def test_chat_without_match_does_not_call_llm_by_default(qdrant_client_app) -> None:
    client, _ = qdrant_client_app()
    body = _chat(client, "Qual a cor do céu?").json()
    assert body["agent"] == "faq"
    assert "Não encontrei essa resposta" in body["answer"]
    assert body["sources"] == []


def test_chat_without_match_falls_back_to_rag_when_enabled(qdrant_client_app) -> None:
    client, _ = qdrant_client_app(faq_llm_fallback=True)
    body = _chat(client, "Qual a cor do céu?").json()
    assert "Não encontrei essa resposta" not in body["answer"]
    assert body["judge"]["motivo"] != "resposta_canonica_qdrant"


def test_chat_profile_restricted_faq(qdrant_client_app) -> None:
    client, _ = qdrant_client_app()
    comum = _chat(client, "Quem pode aprovar a entrada de um morador?", perfil="comum").json()
    sindico = _chat(client, "Quem pode aprovar a entrada de um morador?", perfil="sindico").json()
    assert "Não encontrei essa resposta" in comum["answer"]
    assert sindico["answer"] == "O síndico aprova a entrada pelo painel do condomínio."


def test_chat_qdrant_down_is_graceful(qdrant_client_app) -> None:
    client, _ = qdrant_client_app()

    async def boom(*args, **kwargs):
        raise RuntimeError("qdrant fora do ar")

    client.app.state.graph.faq_search.buscar_faq = boom
    body = _chat(client, "Como funciona a validação das postagens?").json()
    assert "indisponível" in body["answer"]


def test_canonical_flag_does_not_leak_to_next_turn(qdrant_client_app) -> None:
    """faq_canonica pula o juiz LLM; se vazasse para o turno seguinte (o
    checkpointer guarda o estado da sessão), uma resposta gerada por LLM
    passaria sem juiz."""
    client, _ = qdrant_client_app()
    first = _chat(client, "Como funciona a validação das postagens?").json()
    second = client.post(
        "/api/v1/chat",
        headers={"X-Usuario-Id": "5", "X-Perfil": "comum"},
        json={"session_id": first["session_id"], "mensagem": "Como separar resíduos recicláveis?"},
    ).json()
    assert second["agent"] == "educacional"
    assert second["judge"]["motivo"] != "resposta_canonica_qdrant"


def test_rag_backend_is_untouched_by_default(client) -> None:
    assert client.app.state.graph.faq_search is None
    assert client.get("/health").json()["services"]["qdrant"] == "not_configured"
