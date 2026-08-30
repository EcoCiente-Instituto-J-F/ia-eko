from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from src.core.config import Settings


def build_agent_card(settings: Settings) -> AgentCard:
    skill = AgentSkill(
        id="educacao_ambiental",
        name="Educação ambiental EcoCiente",
        description="Responde orientações ambientais fundamentadas na base RAG do EcoCiente.",
        tags=["reciclagem", "residuos", "compostagem", "sustentabilidade"],
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        examples=["Como separar embalagens recicláveis?", "O que posso colocar na compostagem?"],
    )
    return AgentCard(
        name="EcoCiente Educational Agent",
        description="Agente A2A público de demonstração para educação ambiental.",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=settings.a2a_public_url,
            )
        ],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )
