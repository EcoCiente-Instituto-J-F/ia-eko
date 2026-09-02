# MCP no EcoCiente

## Responsabilidades

```text
Usuário autenticado
  ↓
FastAPI / LangGraph
  ↓
Agente Coletas
  ↓
CalendarMcpClient
  ↓
MCP Server EcoCiente
  ↓
listar_agendamentos_coleta | buscar_proxima_coleta
  ↓
CalendarApiClient
  ↓
ds-calendario-api :9800
```

### CalendarApiClient

Cuida exclusivamente de HTTP: base URL, timeout, retry de GET idempotente, `Authorization`, query params, status HTTP, desserialização Pydantic e indisponibilidade.

### MCP Server

Expõe capacidades semânticas para agentes. As tools de calendário são:

| Tool | REST | Resultado |
|---|---|---|
| `listar_agendamentos_coleta` | `GET /api/v1/agendamentos` | lista paginada estruturada |
| `buscar_proxima_coleta` | `GET /api/v1/agendamentos/proxima` | `{ok, found, agendamento, error}` |

A tool `consultar_guia_ecociente` foi preservada.

### Segurança do JWT

`token`, `usuario_id` e `perfil` não existem no input schema das tools de calendário. O Bearer token também não entra no estado/checkpoint do LangGraph: o `ChatService` coloca a credencial em uma `ContextVar` de request e envia ao grafo apenas `UserContext.without_token()`. O `CalendarMcpClient` abre a chamada MCP dentro desse contexto seguro; a tool recupera o token da infraestrutura e o repassa ao `CalendarApiClient`.

Em produção, se não houver token real, a chamada falha explicitamente. Em testes, `CALENDAR_TEST_BEARER_TOKEN` só é aceito quando `APP_ENV=test`.

### Erros

A camada HTTP diferencia:

- 200 vazio: consulta válida sem resultados;
- 404 em `/proxima`: `found=false`;
- 400: filtros rejeitados;
- 401: autenticação rejeitada;
- 403: perfil sem autorização;
- 5xx/timeout/connection error: serviço indisponível;
- resposta incompatível: erro de protocolo.

As tools convertem esses casos para resultados estruturados seguros, permitindo ao agente responder de forma diferente para ausência de coleta, falha de autenticação, falta de permissão e indisponibilidade.

### Endpoints de escrita

Não existem tools para criar, confirmar, reagendar ou excluir porque a API Java fornecida não implementa esses endpoints. O agente informa essa limitação sem tentar URLs planejadas no `Contrato_Api.md`.
