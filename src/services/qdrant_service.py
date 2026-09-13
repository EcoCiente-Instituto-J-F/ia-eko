from dotenv import load_dotenv
from qdrant_client import QdrantClient
from embedding_service import EmbeddingService

COLLECTION_NAME = "faq"

client = QdrantClient(
    url="",
    api_key=""
)

embedding_service = EmbeddingService()

def buscar_no_qdrant(
    embedding: list[float],
    limite: int = 3
) -> list:

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