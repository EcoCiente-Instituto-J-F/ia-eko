from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.embedding_service import EmbeddingService
from src.services.qdrant_service import buscar_no_qdrant, obter_cliente_qdrant

PERGUNTA_TESTE = "Como separar corretamente os materiais recicláveis?"


def main() -> None:
    obter_cliente_qdrant()
    print(f"Pergunta: {PERGUNTA_TESTE}")
    print("\nGerando embedding da pergunta...")

    embedding_service = EmbeddingService()
    embedding = embedding_service.gerar_embedding(PERGUNTA_TESTE)

    print("Embedding gerado.")
    print(f"Dimensão do vetor: {len(embedding)}")
    print("\nConsultando o Qdrant...")

    resultados = buscar_no_qdrant(embedding=embedding, limite=3)
    if not resultados:
        print("Nenhum FAQ encontrado.")
        return

    print(f"\nFAQs encontrados: {len(resultados)}\n")
    for resultado in resultados:
        print("=" * 70)
        print(f"FAQ: {resultado.payload.get('faq_id')}")
        print(f"Título: {resultado.payload.get('titulo')}")
        print(f"Intenção: {resultado.payload.get('intencao')}")
        print(f"Score: {resultado.score}")
        print("-" * 70)
        print("Resposta canônica:", resultado.payload.get("resposta_canonica"))
    print("=" * 70)


if __name__ == "__main__":
    main()
