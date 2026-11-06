from __future__ import annotations

import httpx
import pytest

from src.integrations.education.client import CourseCatalogClient, CourseCatalogError, CursoCatalogo
from src.security.request_context import authenticated_request_context


@pytest.mark.asyncio
async def test_catalogo_usa_jwt_e_contrato_da_mobile_api():
    chamadas = []

    def responder(request: httpx.Request) -> httpx.Response:
        chamadas.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(
            200,
            json=[
                {"id": 3, "titulo": "Reciclagem de Plástico", "descricao": "Separe embalagens"},
                {"id": 5, "titulo": "Compostagem", "descricao": None},
            ],
        )

    catalogo = CourseCatalogClient("https://mobile.example/", transport=httpx.MockTransport(responder))
    try:
        cursos = await catalogo.listar_cursos("jwt_teste")
        assert [curso.id for curso in cursos] == [3, 5]
        assert cursos[1].descricao == ""
        assert chamadas == [("GET", "/api/cursos", "Bearer jwt_teste")]
    finally:
        await catalogo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [(401, "unauthorized"), (403, "forbidden"), (404, "protocol_error"), (503, "unavailable")],
)
async def test_catalogo_nao_finge_disponibilidade_quando_api_falha(codigo, esperado):
    catalogo = CourseCatalogClient(
        "https://mobile.example",
        transport=httpx.MockTransport(lambda _: httpx.Response(codigo)),
    )
    try:
        with pytest.raises(CourseCatalogError) as erro:
            await catalogo.listar_cursos("jwt_teste")
        assert erro.value.code == esperado
    finally:
        await catalogo.close()


@pytest.mark.asyncio
async def test_catalogo_sem_token_nao_faz_chamada():
    chamadas = []

    def registrar(request):
        chamadas.append(request)
        return httpx.Response(200, json=[])

    catalogo = CourseCatalogClient(
        "https://mobile.example",
        transport=httpx.MockTransport(registrar),
    )
    try:
        with pytest.raises(CourseCatalogError) as erro:
            await catalogo.listar_cursos("")
        assert erro.value.code == "unauthorized"
        assert not chamadas
    finally:
        await catalogo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"content": []}, [{"id": 0, "titulo": "X"}], [{"id": 2, "titulo": ""}]])
async def test_catalogo_nao_inventa_cursos_em_respostas_invalidas(payload):
    catalogo = CourseCatalogClient(
        "https://mobile.example",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    try:
        with pytest.raises(CourseCatalogError) as erro:
            await catalogo.listar_cursos("jwt_teste")
        assert erro.value.code == "protocol_error"
    finally:
        await catalogo.close()


def test_recomendacao_escolhe_apenas_cursos_reais_e_relacionados():
    cursos = [
        CursoCatalogo(1, "Fotografia", ""),
        CursoCatalogo(2, "Reciclagem de vidro", "Separação correta de vidro"),
        CursoCatalogo(3, "Introdução à compostagem", ""),
    ]
    resposta, selecionados = CourseCatalogClient.recomendar("Me recomende um curso de reciclagem", cursos)
    assert [curso.id for curso in selecionados] == [2]
    assert "Reciclagem de vidro" in resposta
    assert "curso #2" in resposta
    assert "Fotografia" not in resposta
    assert "curso #1" not in resposta


def test_catalogo_vazio_e_sem_correspondencia_nao_inventam_cursos():
    texto, selecionados = CourseCatalogClient.recomendar("Quais cursos?", [])
    assert "não há cursos" in texto.lower()
    assert selecionados == []
    texto, selecionados = CourseCatalogClient.recomendar(
        "Me recomende um curso de reciclagem", [CursoCatalogo(7, "Astrofísica", "Estrelas")]
    )
    assert selecionados == []
    assert "não encontrei" in texto.lower()


def test_roteamento_detecta_catalogo_mas_nao_dados_privados():
    assert CourseCatalogClient.eh_consulta_catalogo("Me recomende um curso de reciclagem")
    assert CourseCatalogClient.eh_consulta_catalogo("Quais cursos estão disponíveis?")
    assert not CourseCatalogClient.eh_consulta_catalogo("Como está meu progresso no curso de plástico?")
    assert not CourseCatalogClient.eh_consulta_catalogo("Quando será a próxima coleta?")


@pytest.mark.asyncio
async def test_educacional_consulta_catalogo_real_sem_jwt_no_estado():
    pytest.importorskip("langgraph")
    from src.agents.graph import EcoGraphRuntime
    from src.shared.context import UserContext

    requests = []

    def responder(request):
        requests.append((request.url.path, request.headers.get("authorization")))
        return httpx.Response(
            200,
            json=[{"id": 11, "titulo": "Curso de Reciclagem", "descricao": "Triagem de resíduos"}],
        )

    catalogo = CourseCatalogClient("https://mobile.example", transport=httpx.MockTransport(responder))
    graph = object.__new__(EcoGraphRuntime)
    graph.course_catalog = catalogo
    estado = {
        "mensagem": "Me recomende um curso de reciclagem",
        "agents_called": [],
        "latencies_ms": {},
        "user_context": UserContext(user_id=1, perfil="USUARIO_COMUM", condominio_id=None),
    }
    try:
        with authenticated_request_context("jwt_seguro"):
            resultado = await graph.educacional(estado)
        assert "Curso de Reciclagem" in resultado["candidate_answer"]
        assert "curso #11" in resultado["candidate_answer"]
        assert resultado["catalogo_verificado"] is True
        assert len(resultado["sources"]) == 1
        assert "jwt_seguro" not in str(resultado)
        assert requests == [("/api/cursos", "Bearer jwt_seguro")]
    finally:
        await catalogo.close()


@pytest.mark.asyncio
async def test_educacional_sem_sessao_nao_chama_catalogo():
    pytest.importorskip("langgraph")
    from src.agents.graph import EcoGraphRuntime

    requests = []

    def responder(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    catalogo = CourseCatalogClient("https://mobile.example", transport=httpx.MockTransport(responder))
    graph = object.__new__(EcoGraphRuntime)
    graph.course_catalog = catalogo
    try:
        with authenticated_request_context(None):
            resultado = await graph.educacional({"mensagem": "Me recomende um curso", "agents_called": [], "latencies_ms": {}})
        assert "sessão" in resultado["candidate_answer"].lower()
        assert requests == []
    finally:
        await catalogo.close()
