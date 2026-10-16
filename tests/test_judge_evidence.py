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
