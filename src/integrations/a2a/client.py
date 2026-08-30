from __future__ import annotations

import asyncio

from a2a.client import create_client
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role


async def ask_external_agent(base_url: str, message: str) -> list[str]:
    """Descobre o Agent Card no servidor A2A e envia uma mensagem usando o SDK oficial."""
    client = await create_client(base_url)
    request = new_text_message(message, role=Role.ROLE_USER)
    outputs: list[str] = []
    async for chunk in client.send_message(request):
        text = get_stream_response_text(chunk)
        if text:
            outputs.append(text)
    return outputs


async def _main() -> None:
    replies = await ask_external_agent("http://127.0.0.1:8000", "Como separar recicláveis?")
    print("\n".join(replies))


if __name__ == "__main__":
    asyncio.run(_main())
