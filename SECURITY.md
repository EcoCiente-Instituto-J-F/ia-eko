# Security Policy

EcoCiente IA handles data about real people: residents, building managers and cooperatives,
their recycling activity and their relationships. We take reports about it seriously.

## Supported versions

Only the latest code on the `main` branch receives security fixes. There are no maintained
release branches.

| Version | Supported |
| --- | --- |
| `main` (latest) | Yes |
| Older commits, forks and personal branches | No |

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a security problem.**

Report it privately through GitHub:
[**Report a vulnerability**](https://github.com/EcoCiente-Instituto-J-F/ia-eko/security/advisories/new)
(repository → *Security* → *Advisories* → *Report a vulnerability*).

Please include:

- what an attacker can do and which data or profile is affected;
- the steps, request or chat message that reproduces it (use fake data, never real people's data);
- the commit or date of the code you tested and your configuration (`APP_ENV`, `LLM_PROVIDER`,
  `STORAGE_MODE`, `FAQ_BACKEND`);
- any idea you have for a fix.

What happens next:

1. We acknowledge the report and confirm whether we can reproduce it.
2. We work on a fix privately and may ask you for more details.
3. We publish the fix and a GitHub security advisory. If you want, we credit you in it.

Please give us a reasonable time to fix the problem before you disclose it publicly.

## Scope

In scope — this repository and how it behaves:

- Reading or changing another user's or another condominium's data, through the HTTP API or
  through the chat (for example, a message that makes an agent's tool query someone else).
- Bypassing authentication or authorization: the bearer-token check, the profile × action rules
  in `src/security/policies.py`, the tool scope in `src/security/tool_scope.py`, the quota and
  rate limits.
- Prompt injection that leads to data exposure, an unauthorized action or disclosure of secrets
  or system prompts.
- Personal data (CPF, e-mail, phone, tokens) leaking into logs, traces, metrics or answers.
- Test identity headers (`X-Usuario-Id`, `X-Perfil`…) being accepted outside `APP_ENV=test`.
- Injection into SQL or Cypher queries.
- Secrets committed to the repository.

Out of scope:

- An LLM answer that is wrong, rude or off-topic without exposing data or performing an
  unauthorized action — please open a normal issue instead.
- Vulnerabilities in third-party services (the authentication API, the calendar API, Ollama,
  Groq, Gemini, DeepSeek, Langfuse, Neo4j Aura, Qdrant Cloud). Report those to their maintainers.
- Default credentials in `docker-compose.langfuse.yml` and in `.env.example`: they are
  development-only values, marked `CHANGEME`.
- Denial of service by sending large volumes of traffic to your own deployment.

## How the system protects data

A short map, so you know where to look:

| Layer | Where | What it does |
| --- | --- | --- |
| Authentication | `src/api/dependencies.py`, `src/security/authentication.py` | Validates the bearer token with the external authentication API. Test headers only work with `APP_ENV=test` and `ALLOW_TEST_IDENTITY_HEADERS=true`. |
| Authorization | `src/security/policies.py` | Profile × agent and profile × action rules, checked before a message is routed. |
| Tool scope | `src/security/tool_scope.py` | Every tool call of the `analytics` and `grafo` agents is rewritten to the authenticated user's own `usuario_id` and `condominio_id`, or denied. Graph queries only walk nodes of the user's condominium. |
| Input guardrail | `src/security/guardrails.py`, input judge prompt | Blocks prompt injection, requests for third-party personal data and ranking fraud. |
| Output judge | `src/agents/graph.py` (`juiz_saida`) | Censors or rejects claims not supported by the evidence the specialist retrieved, and third-party identifiable data. |
| Output sanitization | `src/security/guardrails.py` | Redacts CPF, e-mail and secrets from the final answer. |
| Tracing | `src/observability/tracing.py` | Masks personal data before traces are sent to Langfuse or LangSmith (`TRACING_MASK_PII=true`). |
| Limits | `src/services/quota_service.py`, `src/services/session_lock.py` | Daily quota, per-minute rate limit and one message at a time per session. |

## Deployment checklist

If you run EcoCiente IA outside a developer machine:

- Set `APP_ENV=production` and keep `ALLOW_TEST_IDENTITY_HEADERS=false`.
- Set `AUTH_API_URL`; without it every authenticated request is refused.
- Keep secrets (`POSTGRES_URL`, `NEO4J_PASSWORD`, LLM API keys, Langfuse keys) in a secret store
  or a Kubernetes `Secret`, never in the `ConfigMap`, the image or the repository.
- Give the API a PostgreSQL role with read-only permissions on the analytics tables.
- Replace every `CHANGEME` value in `docker-compose.langfuse.yml` (`SALT`, `ENCRYPTION_KEY`,
  database and MinIO passwords) before exposing Langfuse.
- Keep `TRACING_MASK_PII=true`.
- Do not expose `/metrics` and `/api/v1/costs` to the public internet; they are meant for
  internal monitoring.
