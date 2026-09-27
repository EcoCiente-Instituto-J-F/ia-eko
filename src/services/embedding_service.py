from __future__ import annotations

import asyncio
import threading
from typing import Any


class EmbeddingService:
    """Embeddings locais com fastembed (ONNX, sem chamada de API).

    O modelo é carregado no primeiro uso, e não no import/construção: o
    `multilingual-e5-large` tem ~2 GB e seria baixado/carregado mesmo quando o
    FAQ não usa Qdrant. O mesmo modelo precisa ter sido usado para indexar a
    coleção — vetores de modelos diferentes não são comparáveis.
    """

    def __init__(self, model_name: str = "intfloat/multilingual-e5-large"):
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def gerar_embedding(self, texto: str) -> list[float]:
        texto = texto.strip()

        if not texto:
            raise ValueError("O texto não pode estar vazio")

        # Modelos e5 exigem o prefixo "query: " na consulta ("passage: " nos documentos).
        texto_consulta = f"query: {texto}"

        embedding = list(
            self.model.embed([texto_consulta])
        )[0]

        return embedding.tolist()

    async def agerar_embedding(self, texto: str) -> list[float]:
        """Versão para o event loop: a inferência ONNX é CPU e bloqueante."""
        return await asyncio.to_thread(self.gerar_embedding, texto)
