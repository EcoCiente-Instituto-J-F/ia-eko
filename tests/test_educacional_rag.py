"""Educacional: guia de reciclagem, filtro por fonte, JSON -> texto e query de continuação."""

import asyncio
from pathlib import Path

from src.agents.graph import EcoGraphRuntime
from src.agents.output_parsing import render_specialist_answer
from src.core.config import settings
from src.prompts.agents.educacional import EDUCADOR_PROMPT_COMPLETO
from src.services.rag_service import KIND_EDUCATIVO, RAGService, dividir_em_trechos

TITULOS = [
    "Isopor",
    "Caixa de pizza",
    "Vidro quebrado",
    "Pilhas e baterias",
    "Óleo de cozinha usado",
    "Compostagem em apartamento",
    "Embalagem longa vida",
    "Papel e papelão",
]


def test_guia_educacional_cobre_os_temas_do_eval() -> None:
    texto = settings.educational_base_file.read_text(encoding="utf-8")
    for titulo in TITULOS:
        assert f"## {titulo}" in texto, titulo


def test_cada_secao_do_guia_cabe_em_um_chunk() -> None:
    texto = settings.educational_base_file.read_text(encoding="utf-8")
    secoes = texto.split("\n## ")[1:]
    assert len(secoes) >= 15
    assert all(len(s) <= settings.rag_chunk_size for s in secoes)
    assert dividir_em_trechos(texto, title="Guia", source="x", kind=KIND_EDUCATIVO, settings=settings)


def test_render_extrai_texto_do_json_do_especialista() -> None:
    bruto = (
        '{"dominio":"educador","intencao":"buscar_item","resposta":"Sim, vidro limpo é reciclável.",'
        '"recomendacao":"Retire as tampas.","esclarecer":"","categoria":"vidro","curso_id":null,"evidencias":["Guia"]}'
    )
    assert render_specialist_answer(bruto) == "Sim, vidro limpo é reciclável.\n\nRetire as tampas."
    assert render_specialist_answer("```json\n" + bruto + "\n```") == render_specialist_answer(bruto)


def test_render_usa_campos_do_analytics_e_pergunta_de_esclarecimento() -> None:
    assert render_specialist_answer('{"resposta_estruturada":"12 postagens","insight":"Subiu."}') == "12 postagens\n\nSubiu."
    assert render_specialist_answer('{"resposta":"","esclarecer":"É plástico ou vidro?"}') == "É plástico ou vidro?"


def test_render_nao_json_e_json_sem_texto_passam_como_estao() -> None:
    assert render_specialist_answer("Texto simples.") == "Texto simples."
    assert render_specialist_answer('{"dominio":"educador"}') == '{"dominio":"educador"}'


def test_render_nao_repete_campo_contido_em_outro() -> None:
    assert render_specialist_answer('{"resposta":"Lave a lata.","recomendacao":"Lave a lata."}') == "Lave a lata."


def test_query_de_continuacao_herda_a_pergunta_anterior() -> None:
    memoria = {"recent_messages": [{"role": "user", "content": "Posso reciclar pizza?"}, {"role": "assistant", "content": "x"}]}
    assert EcoGraphRuntime.rag_query("e o vidro?", memoria) == "Posso reciclar pizza? e o vidro?"
    longa = "Como separar vidro quebrado com segurança em casa?"
    assert EcoGraphRuntime.rag_query(longa, memoria) == longa
    assert EcoGraphRuntime.rag_query("e o vidro?", {}) == "e o vidro?"


def test_prompt_do_educador_tem_todos_os_shots_e_sem_placeholder_invalido() -> None:
    assert "consultar_progresso_aula" in EDUCADOR_PROMPT_COMPLETO.split("EXEMPLOS ILUSTRATIVOS")[1]
    assert 'curso_id":[' not in EDUCADOR_PROMPT_COMPLETO
    assert "nao_encontrado_na_base" in EDUCADOR_PROMPT_COMPLETO


def test_busca_em_memoria_filtra_por_kind(tmp_path: Path) -> None:
    kb = tmp_path / "kb.md"
    edu = tmp_path / "edu.md"
    kb.write_text("# App\n\nO cadastro do EcoCiente exige e-mail e senha para todos os perfis.", encoding="utf-8")
    edu.write_text("# Guia\n\n## Vidro\n\nGarrafas de vidro limpas vão para a reciclagem.", encoding="utf-8")
    cfg = settings.with_overrides(
        faq_backend="rag",
        embedding_provider="mock",
        enable_external_source=False,
        knowledge_base_path=str(kb),
        educational_base_path=str(edu),
    )
    rag = RAGService(cfg)

    async def run():
        await rag.start()
        todos, _ = await rag.search("cadastro e vidro", k=4)
        so_edu, fontes = await rag.search("cadastro e vidro", k=4, kinds=frozenset({KIND_EDUCATIVO}))
        return todos, so_edu, fontes

    todos, so_edu, fontes = asyncio.run(run())
    assert "cadastro" in todos.lower()
    assert "cadastro" not in so_edu.lower() and "vidro" in so_edu.lower()
    assert fontes and all(f.title == "Guia Educacional EcoCiente" for f in fontes)
