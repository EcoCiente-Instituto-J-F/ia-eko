from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from src.shared.text import fold


@dataclass(frozen=True, slots=True)
class CursoCatalogo:
    id: int
    titulo: str
    descricao: str


class CourseCatalogError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CourseCatalogClient:
    _TEMAS = (
        "recicl", "compost", "plast", "vidr", "papel", "papelao",
        "metal", "sustent", "organ", "residu", "triag", "coleta",
    )

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    @staticmethod
    def eh_consulta_catalogo(mensagem: str) -> bool:
        texto = fold(mensagem)
        if not re.search(r"\b(curso|cursos)\b", texto):
            return False
        if any(
            trecho in texto
            for trecho in ("meu progresso", "minhas notas", "meu desempenho", "minha nota",
                          "meus cursos concluidos", "curso que conclui", "meus certificados")
        ):
            return False
        return any(
            termo in texto
            for termo in ("recomend", "indic", "sugir", "sugest",
                          "disponiv", "catalogo", "quais cursos",
                          "listar cursos", "lista de cursos",
                          "cursos de ", "curso de ", "tem curso", "oferece cursos")
        )

    async def listar_cursos(self, token: str) -> list[CursoCatalogo]:
        if not token or not token.strip():
            raise CourseCatalogError("unauthorized")

        try:
            resposta = await self._client.get(
                f"{self.base_url}/api/cursos",
                headers={"Authorization": f"Bearer {token.strip()}"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as erro:
            raise CourseCatalogError("unavailable") from erro

        if resposta.status_code == 401:
            raise CourseCatalogError("unauthorized")
        if resposta.status_code == 403:
            raise CourseCatalogError("forbidden")
        if resposta.status_code < 200 or resposta.status_code >= 300:
            raise CourseCatalogError("unavailable" if resposta.status_code >= 500 else "protocol_error")

        try:
            payload = resposta.json()
        except ValueError as erro:
            raise CourseCatalogError("protocol_error") from erro

        if not isinstance(payload, list):
            raise CourseCatalogError("protocol_error")

        resultado: list[CursoCatalogo] = []
        for item in payload:
            if not isinstance(item, dict):
                raise CourseCatalogError("protocol_error")
            identificador = item.get("id")
            titulo = item.get("titulo")
            descricao = item.get("descricao")
            if type(identificador) is not int or identificador <= 0:
                raise CourseCatalogError("protocol_error")
            if not isinstance(titulo, str) or not titulo.strip():
                raise CourseCatalogError("protocol_error")
            if descricao is not None and not isinstance(descricao, str):
                raise CourseCatalogError("protocol_error")
            resultado.append(
                CursoCatalogo(
                    id=identificador,
                    titulo=titulo.strip(),
                    descricao=(descricao or "").strip(),
                )
            )
        return resultado

    @classmethod
    def recomendar(
        cls, mensagem: str, cursos: list[CursoCatalogo]
    ) -> tuple[str, list[CursoCatalogo]]:
        if not cursos:
            return (
                "Não há cursos cadastrados no catálogo do EcoCiente neste momento.",
                [],
            )

        pergunta = fold(mensagem)
        temas = [tema for tema in cls._TEMAS if tema in pergunta]
        classificados = sorted(
            cursos,
            key=lambda curso: (
                -sum(
                    (5 if tema in fold(curso.titulo) else 0)
                    + (2 if tema in fold(curso.descricao) else 0)
                    for tema in temas
                ),
                fold(curso.titulo),
                curso.id,
            ),
        )
        if temas:
            classificados = [
                curso for curso in classificados
                if any(
                    tema in fold(curso.titulo) or tema in fold(curso.descricao)
                    for tema in temas
                )
            ]
            if not classificados:
                return (
                    "Consultei o catálogo do EcoCiente, mas não encontrei um curso cadastrado especificamente sobre esse tema. Veja os cursos disponíveis na Biblioteca do aplicativo.",
                    [],
                )

        escolhidos = classificados[:3]
        linhas = [
            f"{indice}. {curso.titulo} (curso #{curso.id})"
            + (f" — {curso.descricao[:180]}" if curso.descricao else "")
            for indice, curso in enumerate(escolhidos, 1)
        ]
        return (
            "Encontrei estas opções no catálogo real de cursos do EcoCiente:\n"
            + "\n".join(linhas)
            + "\nAbra a Biblioteca do EcoCiente para acessar os cursos.",
            escolhidos,
        )

    async def close(self) -> None:
        await self._client.aclose()
