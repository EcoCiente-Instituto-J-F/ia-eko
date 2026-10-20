from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_message_text, get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc


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
            card = await A2ACardResolver(http, base_url).get_agent_card()
            expected = _origin(base_url)
            for interface in card.supported_interfaces:
                if _origin(interface.url) != expected:
                    raise ValueError(f"Agent Card aponta para outro endereço: {interface.url}")
            client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
            text = ""
            async for response in client.send_message(
                SendMessageRequest(message=new_text_message(message, role=Role.ROLE_USER))
            ):
                if response.HasField("message"):
                    text = get_message_text(response.message)
                elif response.HasField("task"):
                    state = response.task.status.state
                    if state != TaskState.TASK_STATE_COMPLETED:
                        raise ValueError(f"Agente externo não concluiu a tarefa: {TaskState.Name(state)}")
                    text = get_stream_response_text(response)
        return text.strip()
    finally:
        if owned:
            await http.aclose()
