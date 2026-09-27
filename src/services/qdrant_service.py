"""Busca vetorial do FAQ no Qdrant — responde sem LLM.

A pergunta vira embedding localmente (`EmbeddingService`, fastembed) e a
coleção `QDRANT_COLLECTION` devolve os FAQs mais próximos com o payload:
`faq_id`, `titulo`, `intencao`, `perfis_relacionados`, `resposta_canonica`,
`palavras_chave` e `tipo`. O agente FAQ usa a `resposta_canonica` do melhor
resultado acima de `QDRANT_MIN_SCORE` como resposta final.

Configuração vem de `Settings` (o `.env` só é lido em `src/core/config.py`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.core.config import Settings
from src.observability.metrics import RAG_LATENCY
from src.security.roles import normalize_profile
from src.services.embedding_service import EmbeddingService

logger = logging.getLogger("ecociente.qdrant")


class QdrantUnavailable(RuntimeError):
    pass


class QdrantFaqService:
    def __init__(
        self,
        settings: Settings,
        *,
        embedding_service: EmbeddingService | None = None,
        client: Any = None,
    ):
        self.settings = settings
        self.collection_name = settings.qdrant_collection
        self.embedding_service = embedding_service or EmbeddingService(settings.qdrant_embedding_model)
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.settings.qdrant_url:
                raise QdrantUnavailable("QDRANT_URL não configurada.")
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key,
                timeout=int(self.settings.qdrant_timeout),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def buscar_no_qdrant(self, embedding: list[float], limite: int = 3) -> list[Any]:
        try:
            resultados = await self.client.query_points(
                collection_name=self.collection_name,
                query=embedding,
                limit=limite,
                with_payload=True,
            )
        except QdrantUnavailable:
            raise
        except Exception as exc:
            raise QdrantUnavailable(f"Falha na busca no Qdrant: {type(exc).__name__}") from exc
        return resultados.points

    async def buscar_faq(self, pergunta: str, limite: int | None = None) -> list[dict[str, Any]]:
        started = time.perf_counter()
        embedding = await self.embedding_service.agerar_embedding(pergunta)
        resultados = await self.buscar_no_qdrant(embedding=embedding, limite=limite or self.settings.qdrant_top_k)
        RAG_LATENCY.labels(operation="qdrant_search").observe(time.perf_counter() - started)

        faqs_encontrados = []
        for resultado in resultados:
            payload = resultado.payload or {}
            faqs_encontrados.append(
                {
                    "faq_id": payload.get("faq_id"),
                    "titulo": payload.get("titulo"),
                    "intencao": payload.get("intencao"),
                    "perfis_relacionados": payload.get("perfis_relacionados"),
                    "resposta_canonica": payload.get("resposta_canonica"),
                    "palavras_chave": payload.get("palavras_chave"),
                    "tipo": payload.get("tipo"),
                    "score": resultado.score,
                }
            )
        return faqs_encontrados

    def melhor_resposta(self, faqs: list[dict[str, Any]], perfil: str | None) -> dict[str, Any] | None:
        """Primeiro FAQ (ordem de score) acima do mínimo, com resposta, e
        compatível com o perfil do usuário."""
        for faq in faqs:
            if (faq.get("score") or 0.0) < self.settings.qdrant_min_score:
                continue
            if not str(faq.get("resposta_canonica") or "").strip():
                continue
            if not self._perfil_compativel(faq.get("perfis_relacionados"), perfil):
                continue
            return faq
        return None

    @staticmethod
    def _perfil_compativel(perfis_relacionados: Any, perfil: str | None) -> bool:
        """Sem perfis cadastrados = FAQ vale para todos. Se houver lista, o
        perfil do usuário precisa estar nela. Valores que não são um perfil
        conhecido (ex.: "todos") não restringem, para não esconder respostas
        por causa de rótulo livre no payload."""
        if not perfis_relacionados:
            return True
        itens = perfis_relacionados if isinstance(perfis_relacionados, list) else [perfis_relacionados]
        conhecidos = {normalize_profile(str(item)) for item in itens} - {None}
        if not conhecidos:
            return True
        perfil_usuario = normalize_profile(perfil)
        if perfil_usuario in conhecidos:
            return True
        # "sindico" genérico no payload vale para síndico residencial E comercial.
        genericos = {str(item).strip().lower() for item in itens}
        return bool(perfil_usuario and perfil_usuario.startswith("SINDICO") and genericos & {"sindico", "síndico"})

    async def verificar_collection(self) -> dict[str, Any]:
        if not await self.client.collection_exists(self.collection_name):
            return {"collection": self.collection_name, "existe": False, "points_count": 0}
        info = await self.client.get_collection(self.collection_name)
        return {"collection": self.collection_name, "existe": True, "points_count": info.points_count}

    async def health(self) -> str:
        try:
            info = await self.verificar_collection()
        except Exception:
            logger.warning("qdrant_health_falhou", exc_info=True)
            return "error"
        return "ok" if info["existe"] else "error"
