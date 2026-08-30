# MCP e A2A

## MCP

- **Servidor:** `src/integrations/mcp/server.py`
- **Cliente:** `src/integrations/mcp/client.py`
- **Tool:** `consultar_guia_ecociente(pergunta, limite)`
- **Entrada:** pergunta textual e limite 1–5.
- **Saída:** trechos reais de `data/FAQ_KNOWLEDGE_BASE.md`, com `title`, `source`, `excerpt` e flag `evidence_found`.
- **LLM:** nenhum; a integração funciona mesmo sem API generativa.

O servidor usa a linha v2 do SDK (`MCPServer`) e o cliente v2 (`Client`).

## A2A

- **Agent Card:** `src/integrations/a2a/agent_card.py`.
- **Servidor:** `src/integrations/a2a/server.py`.
- **Cliente:** `src/integrations/a2a/client.py`.
- **Capacidade exposta:** `educacao_ambiental`.
- **Transporte:** JSON-RPC A2A 1.0.
- **Execução:** um agente externo descobre o Agent Card, envia uma `Message`; o executor consulta o RAG EcoCiente e devolve uma `Message` A2A.

Isso é interoperabilidade entre sistemas/agentes externos pelo protocolo A2A, e não apenas um nó do LangGraph chamando outro nó.
