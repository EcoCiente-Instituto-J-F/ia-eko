from fastembed import TextEmbedding

class EmbeddingService:
    def __init__(self):
        self.model = TextEmbedding(
            model_name="intfloat/multilingual-e5-large"
        )

    def gerar_embedding(self, texto: str) -> list[float]:
        texto = texto.strip()

        if not texto:
            raise ValueError("O texto não pode estar vazio")

        texto_consulta = f"query: {texto}"

        embedding = list(
            self.model.embed([texto_consulta])
        )[0]

        return embedding.tolist()
