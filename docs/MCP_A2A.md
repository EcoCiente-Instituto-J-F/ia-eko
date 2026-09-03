# MCP e A2A

## MCP

- **Servidor:** `src/integrations/mcp/server.py`
- **Cliente:** `src/integrations/mcp/client.py`
- **Tools:** `consultar_guia_ecociente`, `listar_agendamentos_coleta` e `buscar_proxima_coleta`.
- **Conhecimento local:** `consultar_guia_ecociente(pergunta, limite)` retorna trechos de `data/FAQ_KNOWLEDGE_BASE.md` sem LLM.
- **Calendário:** as duas tools de calendário consomem exclusivamente os endpoints GET realmente implementados pela `ds-calendario-api`.
- **Segurança:** token, `usuario_id` e `perfil` não fazem parte do schema das tools; entram por contexto autenticado de infraestrutura.

O servidor usa a linha v2 do SDK (`MCPServer`) e o cliente v2 (`Client`). O agente Coletas usa o cliente MCP em processo; detalhes HTTP ficam isolados no `CalendarApiClient`. Consulte `docs/MCP.md` e `docs/CALENDAR_API_ENDPOINTS.md`.

## A2A

- **Agent Card:** `src/integrations/a2a/agent_card.py`.
- **Servidor:** `src/integrations/a2a/server.py`.
- **Cliente:** `src/integrations/a2a/client.py`.
- **Capacidade exposta:** `educacao_ambiental`.
- **Transporte:** JSON-RPC A2A 1.0.
- **Execução:** um agente externo descobre o Agent Card, envia uma `Message`; o executor consulta o RAG EcoCiente e devolve uma `Message` A2A.

Isso é interoperabilidade entre sistemas/agentes externos pelo protocolo A2A, e não apenas um nó do LangGraph chamando outro nó.
