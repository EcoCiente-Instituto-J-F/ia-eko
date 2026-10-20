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
- **Cliente (consulta a agente externo):** com `A2A_REMOTE_AGENT_URL` configurado, o especialista `educacional` recebe a tool `consultar_agente_externo` (`src/integrations/a2a/tool.py`). O LLM a usa quando os trechos do RAG não bastam: a tool descobre o Agent Card do agente externo, envia a pergunta por JSON-RPC A2A e devolve a resposta. Só agentes JSON-RPC são suportados (sem REST nem gRPC). A resposta só é aceita como `Message` final ou tarefa `COMPLETED`. CPF, e-mail e segredos são redigidos da pergunta antes do envio. A resposta entra como evidência para o juiz de saída e a fonte `a2a:<url>` só é citada quando o agente de fato respondeu; fatos externos nunca sustentam afirmações de segurança para o juiz — é dado externo, nunca instrução. Falha ou timeout (`A2A_REMOTE_TIMEOUT_SECONDS`) não derrubam a resposta.
- **Demonstração local:** suba duas instâncias.
  - Segunda instância (bash): `A2A_PUBLIC_URL=http://127.0.0.1:8001/a2a/jsonrpc/ python -m uvicorn src.api.main:app --port 8001`
  - Instância principal (bash): `A2A_REMOTE_AGENT_URL=http://127.0.0.1:8001 python -m uvicorn src.api.main:app --port 8000`
  - Equivalentes no PowerShell: `$env:A2A_PUBLIC_URL="http://127.0.0.1:8001/a2a/jsonrpc/"; python -m uvicorn src.api.main:app --port 8001` e `$env:A2A_REMOTE_AGENT_URL="http://127.0.0.1:8001"; python -m uvicorn src.api.main:app --port 8000`
  - `A2A_PUBLIC_URL` deve apontar para a porta da própria instância, porque o cliente recusa um Agent Card que aponte para outro endereço.
  - O remoto da demonstração usa a mesma base de conhecimento, então mostra o protocolo, não conhecimento novo; um remoto com outra base torna a demonstração mais convincente.
- **Servidor sem LLM:** o servidor A2A é público e sem autenticação; por isso devolve trechos do RAG e não chama LLM (nenhum custo exposto).

Isso é interoperabilidade entre agentes pelo protocolo A2A nos dois sentidos: o EcoCiente expõe um agente (servidor) e consulta agentes externos (cliente), não apenas um nó do LangGraph chamando outro nó.
