from qdrant_client import QdrantClient

from src.core.config import settings
from src.services.embedding_service import EmbeddingService

COLLECTION_NAME = settings.qdrant_collection


def obter_cliente_qdrant() -> QdrantClient:
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise RuntimeError(
            "Configure QDRANT_URL e QDRANT_API_KEY no arquivo .env antes de usar o Qdrant."
        )

    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
    )

def buscar_no_qdrant(
    embedding: list[float],
    limite: int = 3
) -> list:
    client = obter_cliente_qdrant()
    resultados = client.query_points(
        collection_name=COLLECTION_NAME,
        query=embedding,
        limit=limite,
        with_payload=True
    )

    return resultados.points


def buscar_faq(
    pergunta: str,
    limite: int = 3
) -> list[dict]:
    embedding_service = EmbeddingService()
    embedding = embedding_service.gerar_embedding(pergunta)
    
    resultados = buscar_no_qdrant(
        embedding=embedding,
        limite=limite
    )

    faqs_encontrados = []

    for resultado in resultados:

        faq = {
            "faq_id": resultado.payload.get("faq_id"),
            "titulo": resultado.payload.get("titulo"),
            "intencao": resultado.payload.get("intencao"),
            "perfis_relacionados": resultado.payload.get(
                "perfis_relacionados"
            ),
            "resposta_canonica": resultado.payload.get(
                "resposta_canonica"
            ),
            "palavras_chave": resultado.payload.get(
                "palavras_chave"
            ),
            "tipo": resultado.payload.get("tipo"),
            "score": resultado.score
        }

        faqs_encontrados.append(faq)

    return faqs_encontrados


def verificar_collection() -> dict:
    client = obter_cliente_qdrant()
    if not client.collection_exists(COLLECTION_NAME):
        return {
            "collection": COLLECTION_NAME,
            "existe": False,
            "points_count": 0
        }

    info = client.get_collection(COLLECTION_NAME)

    return {
        "collection": COLLECTION_NAME,
        "existe": True,
        "points_count": info.points_count
    }
