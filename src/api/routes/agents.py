from fastapi import APIRouter

from src.api.schemas.agent import AgentInfo

router = APIRouter(prefix="/api/v1", tags=["agents"])

AGENTS = [
    AgentInfo(name="guardrail_entrada", description="Validação determinística e Juiz de Entrada contra injeção, privacidade e fraude."),
    AgentInfo(name="memoria", description="Recupera contexto da sessão e memória consolidada de longo prazo."),
    AgentInfo(name="orquestrador", description="Classifica a intenção e encaminha ao especialista correto."),
    AgentInfo(name="faq", description="RAG sobre regras, funcionalidades e políticas do EcoCiente."),
    AgentInfo(name="analytics", description="Consulta dados analíticos autorizados via ferramentas PostgreSQL e projeções Redis."),
    AgentInfo(name="educacional", description="RAG sobre reciclagem, separação, resíduos, compostagem e sustentabilidade."),
    AgentInfo(name="coletas", description="Consulta e atualiza agenda exclusivamente pela API externa de calendário, respeitando o perfil autenticado."),
    AgentInfo(name="juiz_saida", description="Verifica fundamentação, privacidade, uso de ferramentas e alucinação antes da resposta."),
]


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents() -> list[AgentInfo]:
    return AGENTS
