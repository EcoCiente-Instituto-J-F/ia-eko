"""Vocabulário do grafo — fonte única para o ETL e para as tools do agente `grafo`.

Antes, as tools aceitavam `Material`, `Conteudo` e `POSSUI`, que o ETL nunca
criou, e não aceitavam `Curso`, `MORA_EM`, `APROVADO_EM` etc., que o ETL cria.
"""

NODE_LABELS: tuple[str, ...] = (
    "Usuario",
    "Condominio",
    "Torre",
    "Cooperativa",
    "CategoriaResiduo",
    "Curso",
    "Postagem",
)

RELATIONSHIP_TYPES: tuple[str, ...] = (
    "MORA_EM",
    "PERTENCE_A",
    "REPRESENTADA_POR",
    "CRIOU",
    "NO_CONDOMINIO",
    "NA_TORRE",
    "DA_CATEGORIA",
    "VALIDOU",
    "DENUNCIOU",
    "APROVADO_EM",
    "TEM_DIFICULDADE_EM",
    "RECOMENDADO_PARA",
)
