from __future__ import annotations

import hashlib
import math
import re
import time
from pathlib import Path
from pydantic import BaseModel

import httpx
from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from prometheus_client import Histogram

from src.core.config import Settings

RAG_LATENCY = Histogram("ecociente_rag_duration_seconds", "Latência do RAG", ["operation"])


class SourceResponse(BaseModel):
    title: str
    source: str
    chunk: int | None = None
    url: str | None = None




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


class RAGService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.vector_store = None
        self.documents_count = 0
        self.external_loaded = False
        self.external_error: str | None = None

    async def start(self) -> None:
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
                    "title": "FAQ EcoCiente",
                    "source": str(Path(self.settings.knowledge_base_path).as_posix()),
                    "kind": "local",
                },
            )
        )
        if self.settings.enable_external_source:
            try:
                external_text = await self._fetch_external()
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
            except Exception as exc:
                self.external_error = f"{type(exc).__name__}: {exc}"[:300]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.settings.rag_chunk_size,
            chunk_overlap=self.settings.rag_chunk_overlap,
        )
        chunks: list[Document] = []
        for doc in docs:
            split = splitter.split_documents([doc])
            for index, chunk in enumerate(split):
                chunk.metadata["chunk"] = index
                chunks.append(chunk)

        from src.core.llm import build_embeddings
        from langchain_community.vectorstores import FAISS

        embeddings = build_embeddings(self.settings)
        self.vector_store = await self._to_thread(FAISS.from_documents, chunks, embeddings)
        self.documents_count = len(chunks)
        RAG_LATENCY.labels(operation="startup_index").observe(time.perf_counter() - started)

    async def close(self) -> None:
        self.vector_store = None

    async def _read_local(self, path: Path) -> str:
        return await self._to_thread(path.read_text, encoding="utf-8")

    async def _fetch_external(self) -> str:
        timeout = httpx.Timeout(float(self.settings.external_timeout_seconds))
        headers = {"User-Agent": "EcoCiente-Academic-RAG/1.0"}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(self.settings.external_source_url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
        return text[:120_000]

    async def search(self, query: str, k: int | None = None) -> tuple[str, list[SourceResponse]]:
        if self.vector_store is None:
            return "", []
        started = time.perf_counter()
        docs = await self._to_thread(
            self.vector_store.similarity_search,
            query,
            k=k or self.settings.rag_top_k,
        )
        RAG_LATENCY.labels(operation="search").observe(time.perf_counter() - started)
        context_parts: list[str] = []
        sources: list[SourceResponse] = []
        seen: set[tuple[str, int | None]] = set()
        for doc in docs:
            meta = doc.metadata
            source = str(meta.get("source", ""))
            chunk = meta.get("chunk")
            key = (source, int(chunk) if chunk is not None else None)
            if key in seen:
                continue
            seen.add(key)
            context_parts.append(
                f"[Fonte: {meta.get('title', source)} | chunk {chunk}]\n{doc.page_content}"
            )
            sources.append(
                SourceResponse(
                    title=str(meta.get("title", source)),
                    source=source,
                    chunk=int(chunk) if chunk is not None else None,
                    url=str(meta.get("url")) if meta.get("url") else None,
                )
            )
        return "\n\n".join(context_parts), sources

    def health(self) -> str:
        if self.vector_store is None:
            return "error"
        return "ok_external+local" if self.external_loaded else "ok_local_fallback"

    @staticmethod
    async def _to_thread(func, /, *args, **kwargs):
        import asyncio
        from functools import partial

        return await asyncio.to_thread(partial(func, *args, **kwargs))
