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
        qdrant_url=None,  # nunca herdar o QDRANT_URL real do .env
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
                    "tipo": "faq_canonica",
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
                    "tipo": "faq_canonica",
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
                    "tipo": "faq_canonica",
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


# ------------------------------------------------- trechos do RAG no Qdrant

PERGUNTA_VALIDACAO = "Como funciona a validação?"


async def _seed_trechos(client: AsyncQdrantClient) -> None:
    # Trecho com o mesmo vetor (e o mesmo texto) da pergunta: se a busca do FAQ
    # não filtrasse por tipo, ele empataria com o FAQ-001.
    await client.upsert(
        "faq",
        points=[
            models.PointStruct(
                id=101,
                vector=VETORES["validacao"],
                payload={
                    "tipo": "rag_trecho",
                    "title": "FAQ EcoCiente",
                    "source": "data/FAQ_KNOWLEDGE_BASE.md",
                    "url": None,
                    "kind": "local",
                    "chunk": 0,
                    "texto": PERGUNTA_VALIDACAO,
                },
            ),
            models.PointStruct(
                id=102,
                vector=VETORES["pontos"],
                payload={
                    "tipo": "rag_trecho",
                    "title": "SINIR",
                    "source": "https://sinir.gov.br",
                    "url": "https://sinir.gov.br",
                    "kind": "external",
                    "chunk": 3,
                    "texto": "Os pontos variam conforme o resíduo.",
                },
            ),
        ],
    )


def _service_com_trechos(settings: Settings) -> QdrantFaqService:
    service, _ = _service(settings)
    asyncio.run(_seed_trechos(service.client))
    return service


def test_faq_search_ignores_rag_chunks() -> None:
    service = _service_com_trechos(_settings())
    faqs = asyncio.run(service.buscar_faq(PERGUNTA_VALIDACAO, limite=10))
    assert faqs and all(f["tipo"] == "faq_canonica" for f in faqs)
    assert faqs[0]["faq_id"] == "FAQ-001"


def test_buscar_trechos_and_contar() -> None:
    service = _service_com_trechos(_settings())
    trechos = asyncio.run(service.buscar_trechos(PERGUNTA_VALIDACAO, 1))
    assert trechos == [
        {
            "title": "FAQ EcoCiente",
            "source": "data/FAQ_KNOWLEDGE_BASE.md",
            "url": None,
            "chunk": 0,
            "texto": PERGUNTA_VALIDACAO,
            "score": pytest.approx(1.0),
        }
    ]
    assert asyncio.run(service.contar("rag_trecho")) == 2
    assert asyncio.run(service.contar("faq_canonica")) == 3


def test_rag_search_reads_chunks_from_qdrant_in_the_same_format() -> None:
    from src.services.rag_service import RAGService

    settings = _settings()
    rag = RAGService(settings, qdrant=_service_com_trechos(settings))
    asyncio.run(rag.start())
    context, sources = asyncio.run(rag.search(PERGUNTA_VALIDACAO, k=2))

    assert context.startswith("[Fonte: FAQ EcoCiente | chunk 0]\n" + PERGUNTA_VALIDACAO)
    assert "[Fonte: SINIR | chunk 3]\nOs pontos variam" in context
    assert sources[0].source == "data/FAQ_KNOWLEDGE_BASE.md"
    assert sources[0].title == "FAQ EcoCiente" and sources[0].chunk == 0 and sources[0].url is None
    assert sources[1].url == "https://sinir.gov.br"
    assert asyncio.run(rag.health()) == "ok"


def test_rag_start_does_not_index_when_backend_is_qdrant(monkeypatch) -> None:
    from pathlib import Path

    from langchain_core.vectorstores import InMemoryVectorStore

    from src.services import rag_service
    from src.services.rag_service import RAGService

    def proibido(*args, **kwargs):
        raise AssertionError("com FAQ_BACKEND=qdrant o start() não indexa nada")

    monkeypatch.setattr(InMemoryVectorStore, "from_documents", proibido)
    monkeypatch.setattr(Path, "read_text", proibido)
    monkeypatch.setattr(rag_service, "baixar_fonte_externa", proibido)

    settings = _settings(enable_external_source=True)
    service, embedder = _service(settings)
    rag = RAGService(settings, qdrant=service)
    asyncio.run(rag.start())
    assert embedder.calls == []
    assert rag.vector_store is None


def test_rag_search_degrades_when_qdrant_is_down() -> None:
    from src.services.rag_service import RAGService

    class Fora:
        async def query_points(self, *args, **kwargs):
            raise ConnectionError("qdrant fora do ar")

        async def count(self, *args, **kwargs):
            raise ConnectionError("qdrant fora do ar")

    settings = _settings()
    service = QdrantFaqService(settings, embedding_service=FakeEmbedding(), client=Fora())  # type: ignore[arg-type]
    rag = RAGService(settings, qdrant=service)
    asyncio.run(rag.start())
    assert asyncio.run(rag.search("Como funciona a validação?")) == ("", [])
    assert asyncio.run(rag.health()) == "error"


def test_rag_health_is_error_without_chunks() -> None:
    from src.services.rag_service import RAGService

    settings = _settings()
    service, _ = _service(settings)  # só FAQs, nenhum trecho carregado
    assert asyncio.run(RAGService(settings, qdrant=service).health()) == "error"


def test_rag_backend_keeps_memory_index_even_with_qdrant_service() -> None:
    from src.services.rag_service import RAGService

    settings = _settings(faq_backend="rag")
    service, embedder = _service(settings)
    rag = RAGService(settings, qdrant=service)
    asyncio.run(rag.start())
    assert rag.vector_store is not None and rag.documents_count > 0
    context, sources = asyncio.run(rag.search("Como separar resíduos recicláveis?"))
    assert context and sources and embedder.calls == []
    assert asyncio.run(rag.health()) == "ok_local_fallback"


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
        # O RAG também lê do Qdrant com este backend; sem isto ele usaria o
        # QDRANT_URL do ambiente.
        client.app.state.rag.qdrant = service
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


def test_qdrant_is_the_default_backend() -> None:
    from src.core.config import Settings

    assert Settings().faq_backend == "qdrant"


def test_rag_backend_is_untouched_when_pinned(client) -> None:
    # O fixture `client` fixa faq_backend="rag": nada de Qdrant.
    assert client.app.state.graph.faq_search is None
    assert client.get("/health").json()["services"]["qdrant"] == "not_configured"


def test_search_without_qdrant_url_fails_before_loading_the_embedding_model() -> None:
    import pytest as _pytest

    from src.services.qdrant_service import QdrantUnavailable

    class Contador:
        chamadas = 0

        async def agerar_embedding(self, texto):
            Contador.chamadas += 1
            return [0.0] * DIM

    service = QdrantFaqService(_settings(), embedding_service=Contador())  # type: ignore[arg-type]
    with _pytest.raises(QdrantUnavailable):
        asyncio.run(service.buscar_faq("como faço login?"))
    with _pytest.raises(QdrantUnavailable):
        asyncio.run(service.buscar_trechos("como faço login?", 3))
    assert Contador.chamadas == 0
