# Mapeamento da ds-calendario-api

Fonte principal: código Java anexado, especialmente `AgendamentoController`, DTOs, services, repositories e security. O `Contrato_Api.md` foi usado apenas para comparação.

## Endpoints implementados

| Método | Endpoint | Implementado no controller | Documentado no contrato | Auth | Parâmetros | Response | Status relevantes | MCP Tool |
|---|---|---:|---:|---|---|---|---|---|
| GET | `/api/v1/agendamentos` | Sim | Não com este path; contrato cita `/calendario` | Bearer JWT | `status`, `dataInicio`, `dataFim`, `possuiRecorrencia`, `condominioId`, `cooperativaId`, `page`, `size`, `sort` | `Page<CalendarioResponseDto>` | 200, 400, 401, 403, 500 | `listar_agendamentos_coleta` |
| GET | `/api/v1/agendamentos/proxima` | Sim | Não com este path; contrato cita `/calendario/proxima` | Bearer JWT | `dataInicio`, `dataFim`, `possuiRecorrencia`, `condominioId`, `cooperativaId` | `CalendarioResponseDto` | 200, 400, 401, 403, 404, 500 | `buscar_proxima_coleta` |

### Paginação

`GET /api/v1/agendamentos` usa `@PageableDefault(size = 10, sort = "dataInicio", direction = ASC)`. O cliente Python envia por padrão `page=0`, `size=10`, `sort=dataInicio,asc`.

O JSON do Spring `Page` é modelado nos campos estáveis usados pela integração: `content`, `totalElements`, `totalPages`, `size`, `number`, `first`, `last`, `numberOfElements` e `empty`.

### CalendarioResponseDto

```json
{
  "id": 1,
  "condominioId": 10,
  "cooperativaId": 20,
  "dataInicio": "2026-08-30T09:00:00",
  "dataFim": "2026-08-30T10:00:00",
  "statusAgendamento": "AGENDADO",
  "possuiRecorrencia": true
}
```

### Enum de status real

- `AGENDADO`
- `CONFIRMADO`
- `RECUSADO`
- `CANCELADO`
- `REALIZADO`

## Endpoints somente documentados / planejados

Nenhum dos endpoints abaixo possui `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping` ou `@DeleteMapping` correspondente no código fornecido. Portanto **não existem tools MCP funcionais para eles**.

| Método | Endpoint no Contrato_Api.md | Status |
|---|---|---|
| GET | `/calendario` | Planejado / path divergente do controller real |
| GET | `/calendario/proxima` | Planejado / path divergente do controller real |
| GET | `/agendamentos/{id}` | Planejado / não implementado |
| GET | `/agendamentos/historico` | Planejado / não implementado |
| POST | `/agendamentos` | Planejado / não implementado |
| PUT | `/agendamentos/{id}` | Planejado / não implementado |
| PATCH | `/agendamentos/{id}/status` | Planejado / não implementado |
| PATCH | `/agendamentos/{id}/confirmacao` | Planejado / não implementado |
| GET | `/recorrencias` | Planejado / não implementado |
| POST | `/recorrencias` | Planejado / não implementado |
| PUT | `/recorrencias/{id}` | Planejado / não implementado |
| DELETE | `/recorrencias/{id}` | Planejado / não implementado |

## Autenticação e perfis

A API usa `Authorization: Bearer <JWT>`. `JwtService` exige os claims:

- `usuarioId` (`Integer`)
- `perfil` (`String`)

Perfis aceitos pelo enum Java:

- `SINDICO`
- `COOPERATIVA`

O `SecurityConfig` libera apenas health/OpenAPI/Swagger e exige autenticação nas demais rotas. O token nunca é argumento MCP e não é enviado ao LLM; ele é propagado do `UserContext` autenticado para o `CalendarApiClient` por contexto de infraestrutura.

## Regras de escopo observadas

A API Java é a fonte da verdade do escopo:

- `SINDICO`: consultas usam o `usuarioId` do JWT e o vínculo `condominios.sindico_usuario_id`.
- `COOPERATIVA`: consultas usam o `usuarioId` do JWT e o vínculo `cooperativas.usuario_id`.
- filtros de `condominioId`/`cooperativaId` refinam a consulta conforme o perfil; não substituem a identidade autenticada.

## Discrepância no OpenAPI/anotações

A descrição `@Operation` de `GET /api/v1/agendamentos/proxima` afirma que é possível filtrar por status, porém o método Java não declara `@RequestParam status`. A integração segue a assinatura real do controller e **não expõe `status` nessa tool**.

## Defeito encontrado na API Java

`AgendamentoColetaRepository.buscarProximoAgendamentoPorCooperativa` contém uma inconsistência real:

```sql
AND (:cooperativaId IS NULL OR ac.condominio_id = :condominioId)
```

mas o método declara apenas `@Param("condominioId") Integer condominioId`; não existe parâmetro `cooperativaId` nessa assinatura. Isso pode impedir a criação/execução correta da query de próxima coleta para o perfil `COOPERATIVA`.

O cliente Python **não cria workaround silencioso**. A correção deve ser feita na própria `ds-calendario-api`, que é a fonte da verdade.
