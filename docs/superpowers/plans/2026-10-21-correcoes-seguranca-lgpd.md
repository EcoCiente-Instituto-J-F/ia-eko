# Correções de segurança/LGPD (M4, M12, M13, M14, M17)

Origem: `docs/AUDITORIA_PONYTAIL.md`, seção 3.4, prioridade 1. Um item por task, um teste por item.

## Regras globais
- Sem commits nem git de escrita; o usuário commita.
- Nenhum subagente mata processos (taskkill/kill/Stop-Process/pkill).
- Correção mínima, sem dependência nova. Suíte completa no fim (~4 min).

## Task 1 — M17: `DELETE /memory` cria a lápide mesmo sem documento
`src/services/session_service.py` (`delete_user_memory`)
- Mongo: `update_one(..., upsert=True)`. A lápide fica com `revision=1` e `texto=""`. Assim, o `insert_one` de uma consolidação com `expected_revision=0` cai em `DuplicateKeyError`, que já retorna `False`.
- Memória: se não houver doc, criar `{"usuario_id", "texto": "", "revision": 1, "updated_at"}`.
- Teste (`tests/test_long_term_memory.py`): fazer `delete_user_memory(9)` num usuário sem memória. Depois, `save_user_memory(9, "x", expected_revision=0)` deve retornar `False` e o texto deve continuar `""`.

## Task 2 — M14: pergunta ao agente A2A externo sai mascarada por completo
`src/integrations/a2a/tool.py`
- `ask_external_agent(url, mask_text(sanitize_output(pergunta)), ...)`. `mask_text` (de `src.observability.tracing`) cobre telefone, CNPJ, RG, JWT e Bearer. `sanitize_output` continua cobrindo as chaves de API.
- O comentário e a docstring passam a refletir isso.
- Teste (`tests/test_a2a.py`): com um `ask_external_agent` falso que captura a pergunta, enviar telefone `(11) 91234-5678`, CNPJ formatado e um JWT. Nenhum deles pode chegar ao falso.

## Task 3 — M4: Coletas só consulta o escopo do próprio usuário
`src/agents/coleta/agent.py` (`run` / `_filters_from_message`)
- Um id de condomínio ou cooperativa digitado e diferente do `user.condominio_id`/`user.cooperativa_id` (ou o usuário sem esse escopo) leva a recusar com a mensagem "Só consigo consultar coletas do seu próprio condomínio/cooperativa.", sem chamar o MCP.
- Um id igual ao do usuário segue normalmente.
- Teste (`tests/test_external_integrations.py`, com `FakeCalendarMcp`): síndico do condomínio 1 pergunta "próxima coleta do condomínio 2". Resultado esperado: recusa e nenhuma chamada registrada. Com "condomínio 1", a chamada acontece.

## Task 4 — M12: `/api/v1/costs` exige identidade de síndico
`src/api/routes/observability.py`
- `user: UserContext = Depends(get_user_context)`. Perfis diferentes de `SINDICO_RESIDENCIAL`/`SINDICO_COMERCIAL` recebem 403. `/metrics` fica como está: é raspado pelo Prometheus na rede interna.
- Testes (`tests/test_cost.py`): sem identidade → 401; morador → 403; o teste existente passa a enviar os headers de teste de síndico.
- **Decisão sua:** escolhi síndico porque o projeto não tem perfil de administrador. Alternativa: um token de operador por env (`COSTS_API_TOKEN`).

## Task 5 — M13: servidor A2A com teto e guardrail
`src/integrations/a2a/server.py` (`EcoEducationalExecutor.execute`) e `src/core/config.py`
- Pergunta vazia, acima de 2000 caracteres ou reprovada em `validate_input` recebe uma resposta fixa de recusa, sem RAG e sem embedding.
- Flag `A2A_SERVER_ENABLED` (padrão `true`, para manter o requisito do trabalho). Com `false`, `add_a2a_endpoints` não é chamado em `main.py`. Entra em `.env.example` e `k8s/configmap.yaml`.
- Fora de escopo: rate limit por IP, que exigiria a cota por IP, um código novo e maior. Fica documentado como risco residual em `SECURITY.md`.
- Testes (`tests/test_a2a.py`): uma injeção e uma pergunta de 2001 caracteres voltam com a recusa, e o RAG falso não é chamado. Com a flag `false`, o Agent Card responde 404.

## Fechamento
- Marcar M4, M12, M13, M14 e M17 como corrigidos em `docs/AUDITORIA_PONYTAIL.md`.
- Suíte completa e revisão final de toda a mudança.
