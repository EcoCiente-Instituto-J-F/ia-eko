from __future__ import annotations

import asyncio

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest


async def ask_external_agent(
    base_url: str,
    message: str,
    *,
    timeout: float = 8.0,
    httpx_client: httpx.AsyncClient | None = None,
) -> str:
    """Descobre o Agent Card, envia a pergunta pelo protocolo A2A e devolve o texto da resposta."""
    owned = httpx_client is None
    http = httpx_client or httpx.AsyncClient(timeout=timeout)
    try:
        async with asyncio.timeout(timeout):
            client = await create_client(base_url, client_config=ClientConfig(httpx_client=http))
            outputs: list[str] = []
            async for chunk in client.send_message(
                SendMessageRequest(message=new_text_message(message, role=Role.ROLE_USER))
            ):
                text = get_stream_response_text(chunk)
                if text:
                    outputs.append(text)
        return "\n".join(outputs).strip()
    finally:
        if owned:
            await http.aclose()


async def _main() -> None:
    print(await ask_external_agent("http://127.0.0.1:8000", "Como separar recicláveis?"))


if __name__ == "__main__":
    asyncio.run(_main())
