from __future__ import annotations

import re

from mcp.server import MCPServer

from src.core.config import settings
mcp = MCPServer("EcoCiente Knowledge MCP")


def _kb_sections() -> list[tuple[str, str]]:
    path = settings.knowledge_base_file
    text = path.read_text(encoding="utf-8")
    sections: list[tuple[str, str]] = []
    title = "FAQ EcoCiente"
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer:
                sections.append((title, "\n".join(buffer).strip()))
            title = line.lstrip("#").strip() or title
            buffer = []
        else:
            buffer.append(line)
    if buffer:
        sections.append((title, "\n".join(buffer).strip()))
    return sections


@mcp.tool()
def consultar_guia_ecociente(pergunta: str, limite: int = 3) -> dict:
    """Consulta trechos da base oficial local do EcoCiente sem executar LLM."""
    limite = max(1, min(limite, 5))
    terms = {t for t in re.findall(r"[\wÀ-ÿ]+", pergunta.lower()) if len(t) >= 4}
    ranked: list[tuple[int, str, str]] = []
    for title, body in _kb_sections():
        haystack = f"{title} {body}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            ranked.append((score, title, body))
    ranked.sort(key=lambda item: item[0], reverse=True)
    results = [
        {
            "title": title,
            "source": settings.knowledge_base_path,
            "excerpt": " ".join(body.split())[:900],
        }
        for _, title, body in ranked[:limite]
    ]
    return {
        "query": pergunta,
        "results": results,
        "evidence_found": bool(results),
    }


if __name__ == "__main__":
    mcp.run()
