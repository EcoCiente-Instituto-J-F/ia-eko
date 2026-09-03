# Integração Calendar API

## Configuração

```env
CALENDAR_API_BASE_URL=http://localhost:9800
CALENDAR_API_TIMEOUT_SECONDS=10
CALENDAR_API_RETRIES=2
```

Somente testes reais controlados:

```env
APP_ENV=test
RUN_CALENDAR_INTEGRATION_TESTS=true
CALENDAR_TEST_BEARER_TOKEN=<token-de-teste>
```

Nunca use credencial de produção nesse teste.

## Fluxo

```text
"Qual minha próxima coleta?"
  ↓
Orquestrador → route=coletas
  ↓
CollectionAgent
  ↓
MCP buscar_proxima_coleta
  ↓
CalendarApiClient
  ↓
GET /api/v1/agendamentos/proxima
Authorization: Bearer <token fora do prompt>
  ↓
CalendarioResponseDto / 404
  ↓
resultado MCP estruturado
  ↓
resposta natural
```

Para listagens, o fluxo usa `listar_agendamentos_coleta` e `GET /api/v1/agendamentos`, preservando filtros e paginação da API Java.
