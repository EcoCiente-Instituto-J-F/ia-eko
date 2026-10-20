from __future__ import annotations

import math
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

from dotenv import load_dotenv

# Regra do projeto: o .env é carregado exclusivamente aqui.
load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


_TRUE = {"1", "true", "yes", "on", "sim"}
_PARSE = {"bool": lambda v: v.strip().lower() in _TRUE, "int": int, "float": float}
# Variáveis cujo nome não é o campo em maiúsculas.
_ENV_NAMES = {
    "environment": "APP_ENV",
    "calendar_api_url": "CALENDAR_API_BASE_URL",
    "calendar_api_timeout": "CALENDAR_API_TIMEOUT_SECONDS",
}
_LOWER = {"environment", "llm_provider", "embedding_provider", "storage_mode", "faq_backend", "tracing_provider"}


@dataclass(slots=True)
class Settings:
    app_name: str = "EcoCiente IA API"
    app_version: str = "1.1.0"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_model: str = "gemini-2.5-flash"
    deepseek_model: str = "deepseek-chat"
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    deepseek_api_key: str | None = None

    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"

    postgres_url: str | None = None
    postgres_pool_min: int = 1
    postgres_pool_max: int = 8
    postgres_connect_timeout: int = 3

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "ecociente"
    mongodb_timeout_ms: int = 2500

    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout: float = 2.5

    session_ttl_seconds: int = 1800
    storage_mode: str = "external"
    allow_storage_fallback: bool = True
    memory_max_messages: int = 20
    memory_keep_recent_messages: int = 6
    # Memória de longo prazo por usuário (sobrevive entre conversas). Consolidada
    # em segundo plano a cada N mensagens do usuário e ao encerrar a sessão.
    long_term_memory_enabled: bool = True
    long_term_memory_every_turns: int = 1
    long_term_memory_max_chars: int = 2000

    neo4j_uri: str | None = "bolt://localhost:7687"
    neo4j_username: str | None = "neo4j"
    neo4j_password: str | None = "neo4j"
    neo4j_database: str | None = "neo4j"

    knowledge_base_path: str = "data/FAQ_KNOWLEDGE_BASE.md"
    enable_external_source: bool = True
    external_source_url: str = "https://sinir.gov.br/"
    external_source_title: str = "SINIR — Sistema Nacional de Informações sobre a Gestão dos Resíduos Sólidos"
    external_timeout_seconds: int = 8
    rag_top_k: int = 4
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 150

    # FAQ por busca vetorial no Qdrant: responde com a `resposta_canonica` do
    # payload, sem LLM. "rag" = fluxo antigo (FAISS + LLM).
    faq_backend: str = "rag"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "faq"
    qdrant_embedding_model: str = "intfloat/multilingual-e5-large"
    qdrant_min_score: float = 0.80
    qdrant_top_k: int = 3
    qdrant_timeout: float = 10.0
    # Sem resultado acima do score mínimo: usar o RAG+LLM antigo (true) ou
    # responder que a base não tem a resposta (false, nenhum LLM chamado).
    faq_llm_fallback: bool = False

    judge_max_corrections: int = 1
    a2a_public_url: str = "http://127.0.0.1:8000/a2a/jsonrpc/"
    # Agente externo consultado pelo especialista educacional via A2A. None = desligado.
    a2a_remote_agent_url: str | None = None
    a2a_remote_timeout_seconds: float = 8.0

    auth_api_url: str | None = None
    auth_api_timeout: float = 8.0
    auth_api_retries: int = 2
    calendar_api_url: str | None = None
    calendar_api_timeout: float = 10.0
    calendar_api_retries: int = 2
    calendar_test_bearer_token: str | None = None
    allow_test_identity_headers: bool = False

    # Tracing de LLM (Langfuse/LangSmith). "none" = desligado.
    tracing_provider: str = "none"
    tracing_mask_pii: bool = True
    tracing_in_mock: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://localhost:3000"
    langsmith_api_key: str | None = None
    langsmith_project: str = "ecociente-ia"

    # Limites de uso. Cota diária = respostas do /chat por usuário por dia
    # (fuso QUOTA_TIMEZONE). 0 ou negativo = sem limite para o perfil.
    quota_enabled: bool = True
    quota_timezone: str = "America/Sao_Paulo"
    quota_usuario_comum: int = 20
    quota_morador_residencial: int = 40
    quota_usuario_comercial: int = 50
    quota_cooperativa: int = 80
    quota_sindico_residencial: int = 100
    quota_sindico_comercial: int = 100
    # Perfis cuja sessão é encerrada antes de disparar o resumo de memória
    # (evita a chamada extra de LLM). Separados por vírgula.
    quota_no_compaction_profiles: str = "USUARIO_COMUM"
    # Anti-rajada: requisições por minuto por usuário, qualquer perfil.
    rate_limit_per_minute: int = 10

    # Custo / ROI (src/observability/cost.py). Valores monetários em R$, sempre
    # vindos do ambiente: 0 = sem custo de API (Ollama). Nada aqui é preço de
    # provedor; quem usa Groq/Gemini/DeepSeek informa o preço vigente.
    llm_price_input_per_1m_brl: float = 0.0
    llm_price_output_per_1m_brl: float = 0.0
    # Premissa de uso para projetar os cenários (solicitações por usuário/semana).
    cost_requests_per_user_week: float = 5.0
    # Infra não é linear: um valor por cenário, informado pelo grupo.
    cost_infra_weekly_brl_100: float = 0.0
    cost_infra_weekly_brl_1000: float = 0.0
    # Benefício de uma resolução (ex.: custo evitado de atendimento humano).
    cost_benefit_per_resolution_brl: float = 0.0

    # Lock da sessão entre réplicas (Redis SET NX PX). O TTL é renovado
    # enquanto a resposta é gerada; se o pod morrer, a sessão é liberada
    # em até SESSION_LOCK_TTL_SECONDS. WAIT = quanto uma segunda mensagem da
    # mesma sessão espera a primeira terminar antes de receber 409.
    session_lock_ttl_seconds: float = 30.0
    session_lock_wait_seconds: float = 45.0

    # Integração real. Nunca devem apontar para produção quando APP_ENV=test.
    run_integration_tests: bool = False
    test_postgres_url: str | None = None
    test_mongodb_uri: str | None = None
    test_mongodb_database: str = "ecociente_test"
    test_redis_url: str | None = None
    test_redis_prefix: str = "ecociente:test:"  


    def __post_init__(self) -> None:
        if self.memory_max_messages < 1:
            raise ValueError("MEMORY_MAX_MESSAGES deve ser maior que zero.")
        if self.memory_keep_recent_messages < 1:
            raise ValueError("MEMORY_KEEP_RECENT_MESSAGES deve ser maior que zero.")
        if self.memory_keep_recent_messages >= self.memory_max_messages:
            raise ValueError("MEMORY_KEEP_RECENT_MESSAGES deve ser menor que MEMORY_MAX_MESSAGES.")
        if self.long_term_memory_every_turns < 1:
            raise ValueError("LONG_TERM_MEMORY_EVERY_TURNS deve ser maior que zero.")
        if self.long_term_memory_max_chars < 200:
            raise ValueError("LONG_TERM_MEMORY_MAX_CHARS deve ser pelo menos 200.")
        for name in (
            "llm_price_input_per_1m_brl",
            "llm_price_output_per_1m_brl",
            "cost_requests_per_user_week",
            "cost_infra_weekly_brl_100",
            "cost_infra_weekly_brl_1000",
            "cost_benefit_per_resolution_brl",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name.upper()} deve ser finito e não negativo.")
        if self.calendar_test_bearer_token and self.environment != "test":
            raise ValueError("CALENDAR_TEST_BEARER_TOKEN só pode ser usado com APP_ENV=test.")

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = _get(_ENV_NAMES.get(f.name, f.name.upper()))
            if raw is None:
                continue
            value = _PARSE.get(f.type, str)(raw)
            values[f.name] = value.lower() if f.name in _LOWER else value
        return cls(**values)

    def with_overrides(self, **changes: object) -> "Settings":
        return replace(self, **changes)

    @property
    def knowledge_base_file(self) -> Path:
        path = Path(self.knowledge_base_path)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parents[2] / path

    def validate_test_isolation(self) -> None:
        if not self.run_integration_tests:
            return
        if self.environment != "test":
            raise RuntimeError("RUN_INTEGRATION_TESTS exige APP_ENV=test.")
        if self.test_postgres_url and self.postgres_url and self.test_postgres_url == self.postgres_url:
            raise RuntimeError("TEST_POSTGRES_URL não pode ser igual a POSTGRES_URL.")
        if self.test_mongodb_database == self.mongodb_database:
            raise RuntimeError("TEST_MONGODB_DATABASE deve ser diferente de MONGODB_DATABASE.")
        if self.test_redis_prefix.strip() in {"", "ranking:", "session:"}:
            raise RuntimeError("TEST_REDIS_PREFIX precisa ser um namespace exclusivo de testes.")


settings = Settings.from_env()
