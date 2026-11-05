"""Eval do Educacional contra o Qdrant e o LLM reais (gasta tokens).

Para cada pergunta: busca só no guia educativo, chama o agente e confere que
(1) o trecho recuperado traz o termo esperado, (2) a resposta não é
`nao_encontrado_na_base` e (3) há evidência. Rode: python -m evals.educador.run
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from src.agents.factory import AgentSuite
from src.agents.output_parsing import extract_json_object
from src.core.config import settings
from src.services.qdrant_service import QdrantFaqService
from src.services.rag_service import KIND_EDUCATIVO, RAGService

DATASET = Path(__file__).with_name("dataset.jsonl")


async def main() -> int:
    casos = [json.loads(linha) for linha in DATASET.read_text(encoding="utf-8").splitlines() if linha.strip()]
    qdrant = QdrantFaqService(settings)
    rag = RAGService(settings, qdrant=qdrant)
    suite = AgentSuite(settings)
    falhas = 0
    try:
        for caso in casos:
            contexto, _ = await rag.search(caso["mensagem"], kinds=frozenset({KIND_EDUCATIVO}))
            recuperou = caso["termo_esperado"].lower() in contexto.lower()
            prompt = f"PERGUNTA_ORIGINAL={caso['mensagem']}\nCONTEXTO_RAG:\n{contexto}\n"
            dados = extract_json_object(await suite.invoke("educacional", prompt)) or {}
            respondeu = dados.get("intencao") != "nao_encontrado_na_base"
            fundamentou = bool(dados.get("evidencias"))
            ok = recuperou and respondeu and fundamentou
            falhas += not ok
            print(f"{'OK   ' if ok else 'FALHA'} {caso['id']} recuperou={recuperou} respondeu={respondeu} evidencias={fundamentou}")
    finally:
        await qdrant.close()
    print(f"{len(casos) - falhas}/{len(casos)} aprovados")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
