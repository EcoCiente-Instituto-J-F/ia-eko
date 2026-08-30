from __future__ import annotations

import asyncio

from mcp import Client

from src.integrations.mcp.server import mcp


async def consultar_via_mcp(pergunta: str) -> object:
    """Demonstra cliente MCP v2 em processo, sem depender de rede nem de LLM."""
    async with Client(mcp) as client:
        return await client.call_tool(
            "consultar_guia_ecociente",
            {"pergunta": pergunta, "limite": 3},
        )


async def _main() -> None:
    result = await consultar_via_mcp("Como separar resíduos recicláveis?")
    print(result)


if __name__ == "__main__":
    asyncio.run(_main())
