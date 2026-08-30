from prometheus_client import Counter, Histogram

REQUESTS_TOTAL = Counter(
    "ecociente_http_requests_total", "Total de requisições HTTP", ["method", "route", "status"]
)
REQUEST_ERRORS = Counter(
    "ecociente_http_request_errors_total", "Requisições HTTP com erro", ["method", "route"]
)
REQUEST_LATENCY = Histogram(
    "ecociente_http_request_duration_seconds", "Latência HTTP", ["method", "route"]
)
AGENT_LATENCY = Histogram(
    "ecociente_agent_duration_seconds", "Latência por agente/nó", ["agent"]
)
RAG_LATENCY = Histogram("ecociente_rag_duration_seconds", "Latência do RAG", ["operation"])
TOOL_LATENCY = Histogram("ecociente_tool_duration_seconds", "Latência de ferramentas", ["tool"])
DB_LATENCY = Histogram(
    "ecociente_db_duration_seconds", "Latência de banco/cache", ["backend", "operation"]
)
LLM_LATENCY = Histogram(
    "ecociente_llm_duration_seconds", "Latência do LLM", ["provider", "agent"]
)
AGENTS_CALLED = Histogram(
    "ecociente_agents_called_per_chat", "Quantidade de agentes/nós chamados por /chat"
)
GUARDRAIL_BLOCKED = Counter(
    "ecociente_guardrail_blocked_total", "Entradas bloqueadas pelo guardrail", ["reason"]
)
JUDGE_REJECTED = Counter(
    "ecociente_judge_rejected_total", "Respostas reprovadas pelo juiz de saída", ["reason"]
)


def safe_label(value: str, max_len: int = 60) -> str:
    value = (value or "unknown").strip().lower().replace(" ", "_")
    return value[:max_len] or "unknown"
