# Arquitetura EcoCiente IA

## Princípios

A refatoração prioriza alta coesão, baixo acoplamento e simplicidade. A referência Open-Notebook foi usada para lifecycle e organização por responsabilidades, não como template literal.

## Lifecycle FastAPI

`src/api/main.py` é o único entrypoint. O lifespan:

1. inicializa o pool PostgreSQL compartilhado;
2. inicializa MongoDB e Redis por meio do `SessionService`;
3. inicializa RAG;
4. constrói serviços e o runtime LangGraph;
5. publica dependências em `app.state`;
6. fecha integrações, RAG e bancos no shutdown.

Nenhuma tool PostgreSQL abre conexão independente por consulta; ela usa `src.database.postgres.postgres_db`.

## Camadas reais

- `api`: HTTP e DI.
- `agents`: comportamento dos especialistas e workflow.
- `prompts`: conteúdo de sistema fora da infraestrutura.
- `services`: casos de uso reutilizáveis.
- `database`: lifecycle dos clientes/pools.
- `integrations`: APIs e protocolos externos.
- `security`: autenticação/autorização/guardrails.
- `tools/shared`: tools que atravessam mais de um fluxo.

Não foram criadas interfaces/factories/repositories artificiais quando uma implementação direta já era suficiente.

## Analytics

O antigo módulo PostgreSQL monolítico foi dividido por responsabilidade:

- `recycling.py`: volume, período, status e postagens;
- `trust.py`: trust score;
- `quizzes.py`: desempenho de quizzes;
- `simulation.py`: ritmos e projeções calculadas sobre dados reais;
- `common.py`: filtros temporais e tratamento de falhas de banco.

Ranking Redis é compartilhado em `src/tools/shared/rankings.py`.

## Memória conversacional

A memória fica no mesmo documento MongoDB da sessão. O fluxo do LangGraph é determinístico:

```text
START → guardrail_entrada → memoria → verificar_compactacao
                                      ├─ <=20 → orquestrador
                                      └─ >20 → resumir_memoria → orquestrador
```

A contagem considera somente `user` e `assistant`. Até `MEMORY_MAX_MESSAGES=20`, o histórico permanece integral. Ao ultrapassar o limite, `MemorySummarizerService` recebe o resumo anterior e somente a parte antiga que sairá da janela; o novo resumo substitui o anterior e `MEMORY_KEEP_RECENT_MESSAGES=6` mensagens continuam integrais. O prompt está em `src/prompts/shared/memory.py`.

A sessão persiste `memory_summary`, `messages` e `memory_revision`. A revisão implementa compare-and-set no MongoDB para impedir overwrite por compactações concorrentes; um lock por sessão preserva a ordem dentro do processo. Falhas do resumidor são registradas e não alteram resumo nem mensagens. A memória da sessão é isolada por `session_id` + `usuario_id`. Acima dela existe a **memória de longo prazo do usuário**: um documento por `usuario_id` na coleção `memoria_usuarios`, sem TTL, com fatos duráveis ditos pelo próprio usuário (preferências, objetivos, contexto, pendências). Depois de cada resposta, em segundo plano, `ChatService.remember` junta as mensagens da sessão ainda não consolidadas (`memoria_longa_ate`) e, a cada `LONG_TERM_MEMORY_EVERY_TURNS` mensagens do usuário ou ao encerrar a sessão, pede ao LLM a memória atualizada (`USER_MEMORY_PROMPT`). Mensagens bloqueadas pelo guardrail de entrada (regex ou juiz LLM) são marcadas como `bloqueada` e nunca entram na consolidação; o resultado passa por `sanitize_output` e `validate_input`, e a gravação é compare-and-set por `revision`. A memória longa entra no topo do contexto de todos os agentes, rotulada no prompt como fatos declarados pelo usuário, nunca instruções, e o juiz de saída a recebe como `memoria_usuario`; o usuário a consulta e apaga em `GET`/`DELETE /api/v1/memory`.

Limitações conhecidas:

- O filtro contra instruções maliciosas é por regex (`validate_input`); uma instrução parafraseada ou em outro idioma pode passar e virar memória. O alcance é só o próprio usuário, a memória é limitada em tamanho e o usuário pode apagá-la em `DELETE /api/v1/memory`.
- A compactação da sessão descarta mensagens antigas sem olhar `memoria_longa_ate`; se uma consolidação falhar várias vezes seguidas, mensagens ainda não consolidadas podem se perder (raro com `LONG_TERM_MEMORY_EVERY_TURNS=1`).
- `DELETE /api/v1/memory` apaga a memória longa, mas não as mensagens da sessão ativa (que expiram em 30 min) nem traces já enviados ao Langfuse/LangSmith quando o tracing está ligado; o span de consolidação em segundo plano não leva o id do usuário.
- Consolidações do mesmo usuário são serializadas só dentro do processo; com várias réplicas, a gravação compare-and-set evita sobrescrita, mas pode haver chamadas de LLM repetidas.
