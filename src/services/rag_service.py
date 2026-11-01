from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.api.schemas.common import SourceResponse
from src.core.config import Settings
from src.etl.populate_qdrant import TIPO_TRECHO
from src.observability.metrics import RAG_LATENCY

if TYPE_CHECKING:
    from src.services.qdrant_service import QdrantFaqService

logger = logging.getLogger("ecociente.rag")

# Título dos trechos do .md local (no índice em memória e na carga do Qdrant).
TITULO_BASE_LOCAL = "FAQ EcoCiente"


class HashEmbeddings(Embeddings):
    """Embeddings determinísticos para testes; não representam qualidade semântica de produção."""

    dimension = 192

    @classmethod
    def _embed(cls, text: str) -> list[float]:
        vector = [0.0] * cls.dimension
        for token in re.findall(r"[\wÀ-ÿ]+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % cls.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class _TextExtractor(HTMLParser):
    """Texto visível da página, sem script/style/noscript/svg."""

    _SKIP = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if not self._depth:
            self.parts.append(data)


def dividir_em_trechos(
    texto: str, *, title: str, source: str, url: str | None = None, kind: str, settings: Settings
) -> list[Document]:
    """Corta um texto nos trechos do RAG. Usado pelo índice em memória e pela
    carga no Qdrant, para os dois gerarem os mesmos trechos."""
    metadata = {"title": title, "source": source, "kind": kind}
    if url:
        metadata["url"] = url
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    chunks = splitter.split_documents([Document(page_content=texto, metadata=metadata)])
    for index, chunk in enumerate(chunks):
        chunk.metadata["chunk"] = index
    return chunks


async def baixar_fonte_externa(settings: Settings) -> str:
    """Texto visível da página externa (SINIR), limitado a 120 mil caracteres."""
    timeout = httpx.Timeout(float(settings.external_timeout_seconds))
    headers = {"User-Agent": "EcoCiente-Academic-RAG/1.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(settings.external_source_url)
        response.raise_for_status()
    parser = _TextExtractor()
    parser.feed(response.text)
    parser.close()
    text = "\n".join(line.strip() for line in "\n".join(parser.parts).splitlines() if line.strip())
    return text[:120_000]


class RAGService:
    """Busca os trechos que entram no prompt dos agentes.

    Com FAQ_BACKEND=qdrant (e um QdrantFaqService informado) os trechos vêm da
    coleção do Qdrant, carregada antes por `python -m src.etl.populate_qdrant`:
    a API não lê o .md, não baixa o SINIR e não gera embedding ao subir. Com
    FAQ_BACKEND=rag tudo continua em memória, indexado no start().
    """

    def __init__(self, settings: Settings, *, qdrant: QdrantFaqService | None = None):
        self.settings = settings
        self.qdrant = qdrant
        self.vector_store = None
        self.documents_count = 0
        self.external_loaded = False
        self.external_error: str | None = None

    @property
    def usa_qdrant(self) -> bool:
        return self.settings.faq_backend == "qdrant" and self.qdrant is not None

    async def start(self) -> None:
        if self.usa_qdrant:
            # Nada a indexar: os trechos já estão no Qdrant.
            return
        started = time.perf_counter()
        docs: list[Document] = []
        kb_path = self.settings.knowledge_base_file
        if not kb_path.exists():
            raise FileNotFoundError(f"Base RAG não encontrada: {kb_path}")
        local_text = await self._read_local(kb_path)
        docs.append(
            Document(
                page_content=local_text,
                metadata={
                    "title": TITULO_BASE_LOCAL,
                    "source": str(Path(self.settings.knowledge_base_path).as_posix()),
                    "kind": "local",
                },
            )
        )
        if self.settings.enable_external_source:
            try:
                external_text = await baixar_fonte_externa(self.settings)
                if external_text:
                    docs.append(
                        Document(
                            page_content=external_text,
                            metadata={
                                "title": self.settings.external_source_title,
                                "source": self.settings.external_source_url,
                                "url": self.settings.external_source_url,
                                "kind": "external",
                            },
                        )
                    )
                    self.external_loaded = True
            except (httpx.HTTPError, TimeoutError, OSError, ValueError) as exc:
                self.external_error = f"{type(exc).__name__}: {exc}"[:300]

        chunks: list[Document] = []
        for doc in docs:
            chunks.extend(dividir_em_trechos(doc.page_content, settings=self.settings, **doc.metadata))

        from src.core.llm import build_embeddings

        embeddings = build_embeddings(self.settings)
        self.vector_store = await asyncio.to_thread(InMemoryVectorStore.from_documents, chunks, embeddings)
        self.documents_count = len(chunks)
        RAG_LATENCY.labels(operation="startup_index").observe(time.perf_counter() - started)

    async def close(self) -> None:
        self.vector_store = None

    async def _read_local(self, path: Path) -> str:
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def search(self, query: str, k: int | None = None) -> tuple[str, list[SourceResponse]]:
        limite = k or self.settings.rag_top_k
        if self.usa_qdrant:
            return await self._search_qdrant(query, limite)
        if self.vector_store is None:
            return "", []
        started = time.perf_counter()
        docs = await asyncio.to_thread(self.vector_store.similarity_search, query, k=limite)
        RAG_LATENCY.labels(operation="search").observe(time.perf_counter() - started)
        return self._montar_contexto((doc.metadata, doc.page_content) for doc in docs)

    async def _search_qdrant(self, query: str, limite: int) -> tuple[str, list[SourceResponse]]:
        try:
            trechos = await self.qdrant.buscar_trechos(query, limite)
        except Exception:
            # Qdrant fora do ar não derruba o chat: o agente segue sem contexto.
            logger.warning("rag_qdrant_busca_falhou", exc_info=True)
            return "", []
        return self._montar_contexto((trecho, str(trecho.get("texto") or "")) for trecho in trechos)

    @staticmethod
    def _montar_contexto(itens: Iterable[tuple[Mapping[str, Any], str]]) -> tuple[str, list[SourceResponse]]:
        """Mesmo formato de contexto e de fontes nos dois modos (memória e Qdrant)."""
        context_parts: list[str] = []
        sources: list[SourceResponse] = []
        seen: set[tuple[str, int | None]] = set()
        for meta, texto in itens:
            source = str(meta.get("source") or "")
            chunk = meta.get("chunk")
            key = (source, int(chunk) if chunk is not None else None)
            if key in seen:
                continue
            seen.add(key)
            title = meta.get("title") or source
            context_parts.append(f"[Fonte: {title} | chunk {chunk}]\n{texto}")
            sources.append(
                SourceResponse(
                    title=str(title),
                    source=source,
                    chunk=int(chunk) if chunk is not None else None,
                    url=str(meta.get("url")) if meta.get("url") else None,
                )
            )
        return "\n\n".join(context_parts), sources

    async def health(self) -> str:
        if self.usa_qdrant:
            # Sem trechos carregados os agentes responderiam sem contexto:
            # rode python -m src.etl.populate_qdrant.
            try:
                return "ok" if await self.qdrant.contar(TIPO_TRECHO) > 0 else "error"
            except Exception:
                logger.warning("rag_qdrant_health_falhou", exc_info=True)
                return "error"
        if self.vector_store is None:
            return "error"
        return "ok_external+local" if self.external_loaded else "ok_local_fallback"
