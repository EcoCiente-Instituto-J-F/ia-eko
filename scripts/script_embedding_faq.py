from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastembed import TextEmbedding
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.services.qdrant_service import COLLECTION_NAME, obter_cliente_qdrant

ARQUIVO = PROJECT_ROOT / "data" / "FAQ_KNOWLEDGE_BASE.md"
TOTAL_FAQS = 64
MODEL_NAME = "intfloat/multilingual-e5-large"


def extrair_campo(bloco: str, nome: str, proximo_nome: str | None = None) -> str:
    if proximo_nome:
        padrao_campo = (
            rf"\*\*{re.escape(nome)}:\*\*\s*(.*?)"
            rf"(?=\s*\*\*{re.escape(proximo_nome)}:\*\*|\Z)"
        )
    else:
        padrao_campo = rf"\*\*{re.escape(nome)}:\*\*\s*(.*?)(?=\Z)"

    resultado = re.search(padrao_campo, bloco, re.DOTALL)
    if resultado:
        return " ".join(resultado.group(1).strip().split())
    return ""


def carregar_faqs() -> list[dict]:
    texto = ARQUIVO.read_text(encoding="utf-8")
    padrao = r"#### (FAQ-\d+)\s*[—-]\s*(.*?)(?=\n#### FAQ-\d+\s*[—-]|\n#{1,3}\s|\Z)"
    blocos = re.findall(padrao, texto, re.DOTALL)
    faqs = []

    for id_faq, bloco in blocos:
        linhas = bloco.strip().split("\n")
        titulo = linhas[0].strip()
        palavras_chave_texto = extrair_campo(bloco, "Palavras-chave")
        faqs.append(
            {
                "id": id_faq,
                "titulo": titulo,
                "intencao": extrair_campo(bloco, "Intenção", "Perfis relacionados"),
                "perfis_relacionados": extrair_campo(
                    bloco, "Perfis relacionados", "Resposta canônica"
                ),
                "resposta_canonica": extrair_campo(
                    bloco, "Resposta canônica", "Palavras-chave"
                ),
                "palavras_chave": [
                    palavra.strip()
                    for palavra in palavras_chave_texto.split(",")
                    if palavra.strip()
                ],
            }
        )

    if len(faqs) != TOTAL_FAQS:
        raise RuntimeError(
            f"Esperava {TOTAL_FAQS} FAQs, mas encontrei {len(faqs)} em {ARQUIVO}."
        )

    return faqs


def main() -> None:
    client = obter_cliente_qdrant()
    faqs = carregar_faqs()
    print(f"FAQs encontrados: {len(faqs)}")

    documentos = [
        (
            f"passage: {faq['titulo']}\n"
            f"Intenção: {faq['intencao']}\n"
            f"Perfis relacionados: {faq['perfis_relacionados']}\n"
            f"Resposta: {faq['resposta_canonica']}\n"
            f"Palavras-chave: {', '.join(faq['palavras_chave'])}"
        )
        for faq in faqs
    ]

    print("Gerando embeddings...")
    modelo = TextEmbedding(model_name=MODEL_NAME)
    embeddings = list(modelo.embed(documentos))
    print(f"Embeddings gerados: {len(embeddings)}")
    print(f"Dimensão do vetor: {len(embeddings[0])}")

    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=len(embeddings[0]),
                distance=Distance.COSINE,
            ),
        )
        print("Collection criada.")

    points = [
        PointStruct(
            id=int(faq["id"].split("-")[1]),
            vector=embedding.tolist(),
            payload={
                "faq_id": faq["id"],
                "titulo": faq["titulo"],
                "intencao": faq["intencao"],
                "perfis_relacionados": faq["perfis_relacionados"],
                "resposta_canonica": faq["resposta_canonica"],
                "palavras_chave": faq["palavras_chave"],
                "tipo": "faq",
            },
        )
        for faq, embedding in zip(faqs, embeddings)
    ]

    print("Enviando FAQs para o Qdrant...")
    client.upsert(collection_name=COLLECTION_NAME, points=points)
    info = client.get_collection(COLLECTION_NAME)

    print("\nProcesso concluído.")
    print(f"FAQs enviados/atualizados: {len(points)}")
    print(f"Total de points na collection: {info.points_count}")


if __name__ == "__main__":
    main()
