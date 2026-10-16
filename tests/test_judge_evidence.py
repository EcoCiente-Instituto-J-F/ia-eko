from __future__ import annotations

from types import SimpleNamespace

from src.agents.factory import EVIDENCE_MAX_CHARS, tool_evidence


def test_tool_evidence_keeps_only_tool_messages():
    messages = [
        SimpleNamespace(type="human", content="quantos pontos?"),
        SimpleNamespace(type="ai", content="vou consultar"),
        SimpleNamespace(type="tool", name="resumo_reciclagem_morador", content="pontos_estimados=120"),
        SimpleNamespace(type="tool", name="ranking", content=[{"posicao": 3}]),
        SimpleNamespace(type="ai", content="Você fez 120 pontos."),
    ]
    evidence = tool_evidence(messages)
    assert "[resumo_reciclagem_morador]\npontos_estimados=120" in evidence
    assert '"posicao": 3' in evidence
    assert "vou consultar" not in evidence
    assert "quantos pontos" not in evidence


def test_tool_evidence_is_capped():
    messages = [SimpleNamespace(type="tool", name="t", content="x" * (EVIDENCE_MAX_CHARS * 2))]
    assert len(tool_evidence(messages)) == EVIDENCE_MAX_CHARS


def test_tool_evidence_empty_without_tools():
    assert tool_evidence([SimpleNamespace(type="ai", content="oi")]) == ""


def _recording(outputs: dict[str, str], seen: dict[str, list[str]]):
    async def invoke(agent_name: str, prompt: str) -> str:
        seen.setdefault(agent_name, []).append(prompt)
        return outputs.get(agent_name, "")

    return invoke


def _post(client, mensagem: str):
    return client.post(
        "/api/v1/chat",
        headers={"X-Usuario-Id": "77", "X-Perfil": "morador_residencial"},
        json={"mensagem": mensagem},
    )


def test_judge_receives_rag_context_as_evidence(client):
    seen: dict[str, list[str]] = {}
    client.app.state.graph.agents.invoke = _recording(
        {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "educador"}',
            "educacional": "Separe o vidro.",
            "juiz_saida": '{"status": "aprovado"}',
        },
        seen,
    )
    assert _post(client, "Como separar resíduos recicláveis?").status_code == 200
    judge_prompt = seen["juiz_saida"][-1]
    assert '"rascunho_texto": "Separe o vidro."' in judge_prompt
    assert '"evidencias": "[Fonte: FAQ EcoCiente' in judge_prompt
    assert "ESPECIALISTA_JSON=" not in judge_prompt


def test_judge_receives_analytics_tool_output_as_evidence(client):
    seen: dict[str, list[str]] = {}
    agents = client.app.state.graph.agents
    agents.invoke = _recording(
        {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "analytics"}',
            "juiz_saida": '{"status": "aprovado"}',
        },
        seen,
    )

    async def run_analytics(prompt: str) -> tuple[str, str]:
        return "Você fez 120 pontos.", "[resumo_reciclagem_morador]\npontos_estimados=120"

    agents.run_analytics = run_analytics
    assert _post(client, "Quantos pontos eu fiz?").status_code == 200
    assert "pontos_estimados=120" in seen["juiz_saida"][-1]


def test_judge_gets_empty_evidence_when_no_tool_was_called(client):
    seen: dict[str, list[str]] = {}
    agents = client.app.state.graph.agents
    agents.invoke = _recording(
        {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "analytics"}',
            "juiz_saida": '{"status": "aprovado"}',
        },
        seen,
    )

    async def run_analytics(prompt: str) -> tuple[str, str]:
        return "Você fez 120 pontos.", ""

    agents.run_analytics = run_analytics
    assert _post(client, "Quantos pontos eu fiz?").status_code == 200
    assert '"evidencias": ""' in seen["juiz_saida"][-1]


def test_correction_rejudges_with_fresh_rag_evidence(client):
    seen: dict[str, list[str]] = {}
    answers = iter(['{"status": "bloqueado", "motivo_interno": "sem base"}', '{"status": "aprovado"}'])

    async def invoke(agent_name: str, prompt: str) -> str:
        seen.setdefault(agent_name, []).append(prompt)
        if agent_name == "juiz_saida":
            return next(answers)
        return {
            "juiz_entrada": '{"status": "aprovado"}',
            "orquestrador": '{"route": "educador"}',
            "educacional": "Separe o vidro.",
        }.get(agent_name, "")

    contexts = iter([("CONTEXTO_PRIMEIRA_BUSCA", []), ("CONTEXTO_SEGUNDA_BUSCA", [])])

    async def search(query, k=None):
        return next(contexts)

    client.app.state.graph.rag.search = search
    client.app.state.graph.agents.invoke = invoke
    body = _post(client, "Como separar resíduos recicláveis?").json()
    assert "correcao" in body["agents_called"]
    assert len(seen["juiz_saida"]) == 2
    assert "CONTEXTO_PRIMEIRA_BUSCA" in seen["juiz_saida"][0]
    assert "CONTEXTO_SEGUNDA_BUSCA" in seen["juiz_saida"][1]
    assert "CONTEXTO_PRIMEIRA_BUSCA" not in seen["juiz_saida"][1]


def test_judge_prompt_defines_evidence_as_factual_limit():
    from src.prompts.shared.judges import JUIZ_SAIDA_PROMPT_COMPLETO

    assert "### PAPEL\n### PAPEL" not in JUIZ_SAIDA_PROMPT_COMPLETO
    assert "`especialista_json.evidencias`" in JUIZ_SAIDA_PROMPT_COMPLETO


def test_judge_few_shots_follow_the_evidence_contract():
    from src.prompts.shared.judges import JUIZ_SAIDA_PROMPT_COMPLETO

    assert JUIZ_SAIDA_PROMPT_COMPLETO.count('"evidencias":') >= 5
    assert '"evidencias":""' in JUIZ_SAIDA_PROMPT_COMPLETO
    assert "20% acima" not in JUIZ_SAIDA_PROMPT_COMPLETO
    assert "dados_metrificados" not in JUIZ_SAIDA_PROMPT_COMPLETO
