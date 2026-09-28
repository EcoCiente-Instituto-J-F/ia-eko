"""Carga do FAQ canônico (data/FAQ_KNOWLEDGE_BASE.md) no Qdrant.

Qdrant em memória (mesma API do servidor) + embedder falso por hashing de
palavras: o e5-large (~2 GB) não é baixado nos testes, mas a busca ainda é
"de verdade" o bastante para a pergunta achar o FAQ com as mesmas palavras.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from pathlib import Path

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import AsyncQdrantClient, models  # noqa: E402

from src.core.config import Settings  # noqa: E402
from src.etl.populate_qdrant import (  # noqa: E402
    TIPO_FAQ,
    DimensaoIncompativel,
    FaqParseError,
    QdrantFaqLoader,
    main,
    normalizar_perfis,
    parse_faq_canonicas,
)
from src.services.qdrant_service import QdrantFaqService  # noqa: E402

KB = Path(__file__).resolve().parents[1] / "data" / "FAQ_KNOWLEDGE_BASE.md"
DIM = 256
_STOP = {"o", "a", "os", "as", "de", "do", "da", "e", "é", "que", "um", "uma", "no", "na", "em", "para", "por", "com", "como", "qual", "quais"}


def _vetor(texto: str) -> list[float]:
    vec = [0.0] * DIM
    for palavra in re.findall(r"\w+", texto.lower()):
        if palavra in _STOP:
            continue
        vec[int(hashlib.md5(palavra.encode()).hexdigest(), 16) % DIM] += 1.0
    norma = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norma for v in vec]


class FakeEmbedder:
    model_name = "fake-hash"

    def __init__(self, dim: int = DIM):
        self.dim = dim
        self.document_batches: list[list[str]] = []

    def gerar_embeddings_documentos(self, textos, batch_size=16):
        self.document_batches.append(list(textos))
        return [_vetor(t)[: self.dim] for t in textos]

    async def agerar_embedding(self, texto):
        return _vetor(texto)[: self.dim]

    @property
    def documentos_embedados(self) -> int:
        return sum(len(b) for b in self.document_batches)


def _settings(**overrides) -> Settings:
    base = dict(
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        storage_mode="memory",
        enable_external_source=False,
        postgres_url=None,
        qdrant_collection="faq",
        qdrant_embedding_model="fake-hash",
        qdrant_min_score=0.30,  # calibrado para o embedder falso
    )
    base.update(overrides)
    return Settings.from_env().with_overrides(**base)


@pytest.fixture(scope="module")
def faqs():
    return parse_faq_canonicas(KB.read_text(encoding="utf-8"))


def _loader(client=None, embedder=None, **overrides):
    client = client or AsyncQdrantClient(location=":memory:")
    embedder = embedder or FakeEmbedder()
    return QdrantFaqLoader(_settings(**overrides), client=client, embedding_service=embedder), client, embedder


# ------------------------------------------------------------- parser


def test_real_knowledge_base_has_64_valid_canonical_faqs(faqs) -> None:
    assert len(faqs) == 64
    ids = [f.faq_id for f in faqs]
    assert len(set(ids)) == 64 and ids[0] == "FAQ-001" and ids[-1] == "FAQ-064"
    primeiro = faqs[0]
    assert primeiro.titulo == "O que é o EcoCiente?"
    assert primeiro.categoria == "Projeto"
    assert primeiro.intencao == "visão_geral"
    assert primeiro.perfis_relacionados == []
    assert primeiro.resposta_canonica.startswith("O EcoCiente é um aplicativo móvel")
    assert "reciclagem" in primeiro.palavras_chave
    assert all(f.resposta_canonica and f.intencao and f.categoria for f in faqs)


def test_payload_has_the_fields_the_chat_reads(faqs) -> None:
    payload = faqs[0].payload("modelo-x")
    assert set(payload) >= {"faq_id", "titulo", "intencao", "perfis_relacionados", "resposta_canonica", "palavras_chave", "tipo", "score"} - {"score"}
    assert payload["tipo"] == TIPO_FAQ and payload["embedding_model"] == "modelo-x"
    assert len(payload["content_hash"]) == 16
    assert faqs[0].point_id == parse_faq_canonicas(KB.read_text(encoding="utf-8"))[0].point_id  # determinístico


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Todos", []),
        ("Todos conforme permissão", []),
        ("perfis com mapa", []),
        ("Cooperativa", ["COOPERATIVA"]),
        ("Síndicos", ["SINDICO_RESIDENCIAL", "SINDICO_COMERCIAL"]),
        ("Residencial, Síndicos", ["MORADOR_RESIDENCIAL", "SINDICO_RESIDENCIAL", "SINDICO_COMERCIAL"]),
        ("Usuário Comum, Morador Residencial", ["USUARIO_COMUM", "MORADOR_RESIDENCIAL"]),
        # Um rótulo desconhecido abre o FAQ inteiro em vez de restringir errado.
        ("Cooperativa, perfis autorizados", []),
    ],
)
def test_profile_labels_are_normalized(texto, esperado) -> None:
    assert normalizar_perfis(texto) == esperado


_MINI = """# Base

## 43. Perguntas Frequentes Canônicas

### Grupo A

#### FAQ-001 — Pergunta um?

**Intenção:** um
**Perfis relacionados:** Todos
**Resposta canônica:** Primeira linha
continua aqui.
**Palavras-chave:** a, b

#### FAQ-002 — Pergunta dois?

**Intenção:** dois
**Perfis relacionados:** Cooperativa
**Resposta canônica:** Resposta dois.

## 44. Glossário

#### FAQ-999 — Fora da seção

**Intenção:** x
**Perfis relacionados:** Todos
**Resposta canônica:** não deve entrar
"""


def test_parser_handles_continuation_lines_crlf_and_stops_at_next_section() -> None:
    faqs = parse_faq_canonicas(_MINI.replace("\n", "\r\n"))
    assert [f.faq_id for f in faqs] == ["FAQ-001", "FAQ-002"]
    assert faqs[0].resposta_canonica == "Primeira linha continua aqui."
    assert faqs[1].perfis_relacionados == ["COOPERATIVA"] and faqs[1].palavras_chave == []


def test_parser_rejects_broken_items_instead_of_indexing_half() -> None:
    quebrado = _MINI.replace("**Resposta canônica:** Resposta dois.", "").replace("FAQ-002", "FAQ-001")
    with pytest.raises(FaqParseError) as exc:
        parse_faq_canonicas(quebrado)
    assert "id duplicado" in str(exc.value) and "sem resposta_canonica" in str(exc.value)
    with pytest.raises(FaqParseError):
        parse_faq_canonicas("# nada aqui")


# ------------------------------------------------------------- carga


def test_first_load_creates_collection_and_indexes_everything(faqs) -> None:
    loader, client, embedder = _loader()
    r = asyncio.run(loader.carregar(faqs))
    assert (r.inseridos, r.atualizados, r.inalterados, r.removidos) == (64, 0, 0, 0)
    assert r.colecao_criada and r.dimensao == DIM
    assert asyncio.run(client.count("faq")).count == 64
    # O texto indexado é pergunta + resposta + palavras-chave.
    assert embedder.document_batches[0][0].startswith("O que é o EcoCiente?\nO EcoCiente é um aplicativo")


def test_second_load_is_a_no_op_and_never_loads_the_model(faqs) -> None:
    loader, client, embedder = _loader()
    asyncio.run(loader.carregar(faqs))
    antes = embedder.documentos_embedados
    r = asyncio.run(loader.carregar(faqs))
    assert (r.inseridos, r.atualizados, r.inalterados) == (0, 0, 64)
    assert embedder.documentos_embedados == antes


def test_only_changed_faqs_are_reembedded_and_removed_ones_are_deleted(faqs) -> None:
    from dataclasses import replace

    loader, client, embedder = _loader()
    asyncio.run(loader.carregar(faqs))
    # Conteúdo de outra origem na mesma coleção não pode ser apagado pela carga.
    asyncio.run(
        client.upsert("faq", points=[models.PointStruct(id=999999, vector=[1.0] + [0.0] * (DIM - 1), payload={"tipo": "outro"})])
    )
    editado = replace(faqs[4], resposta_canonica="Login com e-mail e senha.")
    nova_base = [editado if f.faq_id == "FAQ-005" else f for f in faqs if f.faq_id != "FAQ-064"]
    antes = embedder.documentos_embedados

    r = asyncio.run(loader.carregar(nova_base))

    assert (r.inseridos, r.atualizados, r.inalterados, r.removidos) == (0, 1, 62, 1)
    assert embedder.documentos_embedados - antes == 1
    assert asyncio.run(client.count("faq")).count == 64  # 63 FAQs + o ponto "outro"
    ponto = asyncio.run(client.retrieve("faq", ids=[editado.point_id]))[0]
    assert ponto.payload["resposta_canonica"] == "Login com e-mail e senha."


def test_keep_missing_flag(faqs) -> None:
    loader, client, _ = _loader()
    asyncio.run(loader.carregar(faqs))
    r = asyncio.run(loader.carregar(faqs[:10], remover_ausentes=False))
    assert r.removidos == 0 and asyncio.run(client.count("faq")).count == 64


def test_model_with_other_dimension_requires_recreate(faqs) -> None:
    from dataclasses import replace

    loader, client, _ = _loader()
    asyncio.run(loader.carregar(faqs))
    outro, _, _ = _loader(client=client, embedder=FakeEmbedder(dim=128), qdrant_embedding_model="outro-modelo")
    # Troca de modelo muda o hash de todos → tudo seria reembedado com 128 dims.
    with pytest.raises(DimensaoIncompativel):
        asyncio.run(outro.carregar([replace(faqs[0], titulo="x")]))
    r = asyncio.run(outro.carregar(faqs, recriar=True))
    assert r.colecao_criada and r.dimensao == 128 and r.inseridos == 64


# --------------------------------------------- chat encontra o que foi carregado


def _service_on(client) -> QdrantFaqService:
    return QdrantFaqService(_settings(), embedding_service=FakeEmbedder(), client=client)  # type: ignore[arg-type]


def test_loaded_collection_answers_real_questions(faqs) -> None:
    loader, client, _ = _loader()
    asyncio.run(loader.carregar(faqs))
    service = _service_on(client)

    achados = asyncio.run(service.buscar_faq("Como funciona o login no aplicativo?"))
    melhor = service.melhor_resposta(achados, "USUARIO_COMUM")
    assert melhor["faq_id"] == "FAQ-005"
    assert melhor["resposta_canonica"] == next(f.resposta_canonica for f in faqs if f.faq_id == "FAQ-005")

    # Um FAQ exclusivo da Cooperativa não é devolvido a outro perfil.
    coop = next(f for f in faqs if f.perfis_relacionados == ["COOPERATIVA"])
    achados = asyncio.run(service.buscar_faq(coop.titulo))
    assert achados[0]["faq_id"] == coop.faq_id
    assert service.melhor_resposta(achados, "COOPERATIVA")["faq_id"] == coop.faq_id
    melhor_comum = service.melhor_resposta(achados, "USUARIO_COMUM")
    assert melhor_comum is None or melhor_comum["faq_id"] != coop.faq_id


def test_health_flags_empty_collection_and_model_mismatch(faqs) -> None:
    client = AsyncQdrantClient(location=":memory:")
    asyncio.run(client.create_collection("faq", vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE)))
    service = _service_on(client)
    assert asyncio.run(service.health()) == "error"  # vazia

    loader, _, _ = _loader(client=client)
    asyncio.run(loader.carregar(faqs))
    assert asyncio.run(service.health()) == "ok"

    divergente = QdrantFaqService(_settings(qdrant_embedding_model="intfloat/multilingual-e5-large"), client=client)
    info = asyncio.run(divergente.verificar_collection())
    assert info["modelo_indexado"] == "fake-hash"
    assert asyncio.run(divergente.health()) == "error"


# ---------------------------------------------------------- embeddings e CLI


def test_e5_uses_passage_prefix_for_documents_and_query_for_questions() -> None:
    import numpy as np

    from src.services.embedding_service import EmbeddingService

    class _Model:
        def __init__(self):
            self.inputs = []

        def embed(self, textos, batch_size=None):
            self.inputs.extend(textos)
            return [np.array([0.1, 0.2]) for _ in textos]

    e5 = EmbeddingService("intfloat/multilingual-e5-large")
    e5._model = _Model()
    e5.gerar_embeddings_documentos(["doc"])
    e5.gerar_embedding("pergunta")
    assert e5._model.inputs == ["passage: doc", "query: pergunta"]

    outro = EmbeddingService("BAAI/bge-small-en-v1.5")
    outro._model = _Model()
    outro.gerar_embeddings_documentos(["doc"])
    outro.gerar_embedding("pergunta")
    assert outro._model.inputs == ["doc", "pergunta"]

    with pytest.raises(ValueError):
        e5.gerar_embeddings_documentos(["ok", "   "])


def test_cli_dry_run_and_invalid_file(tmp_path, capsys) -> None:
    assert main(["--dry-run", "--arquivo", str(KB)]) == 0
    assert '"faqs": 64' in capsys.readouterr().out
    ruim = tmp_path / "ruim.md"
    ruim.write_text("# sem FAQ", encoding="utf-8")
    assert main(["--dry-run", "--arquivo", str(ruim)]) == 2


def test_recreate_keeps_the_old_collection_if_embedding_fails(faqs) -> None:
    loader, client, _ = _loader()
    asyncio.run(loader.carregar(faqs))

    class Broken(FakeEmbedder):
        def gerar_embeddings_documentos(self, textos, batch_size=16):
            raise RuntimeError("download do modelo falhou")

    quebrado, _, _ = _loader(client=client, embedder=Broken())
    with pytest.raises(RuntimeError):
        asyncio.run(quebrado.carregar(faqs, recriar=True))
    assert asyncio.run(client.count("faq")).count == 64  # o chat continua respondendo
