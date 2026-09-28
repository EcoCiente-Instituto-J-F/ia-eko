from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

# Regra do projeto: o .env é carregado exclusivamente aqui.
load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    value = _get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _int(name: str, default: int) -> int:
    value = _get(name)
    return int(value) if value is not None else default


def _float(name: str, default: float) -> float:
    value = _get(name)
    return float(value) if value is not None else default


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
    groq_api_key: str | None = None
    gemini_api_key: str | None = None

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

    neo4j_uri: str| None = None
    neo4j_username: str| None = None
    neo4j_password: str| None = None
    neo4j_database: str| None = None
    aura_instanceid: str | None = None
    aura_instancename: str | None = None

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

    auth_api_url: str | None = None
    auth_api_timeout: float = 8.0
    auth_api_retries: int = 2
    calendar_api_url: str | None = None
    calendar_api_timeout: float = 10.0
    calendar_api_retries: int = 2
    calendar_test_bearer_token: str | None = None
    run_calendar_integration_tests: bool = False
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
        if self.calendar_test_bearer_token and self.environment != "test":
            raise ValueError("CALENDAR_TEST_BEARER_TOKEN só pode ser usado com APP_ENV=test.")
        if self.run_calendar_integration_tests and self.environment != "test":
            raise ValueError("RUN_CALENDAR_INTEGRATION_TESTS exige APP_ENV=test.")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=_get("APP_NAME", "EcoCiente IA API") or "EcoCiente IA API",
            app_version=_get("APP_VERSION", "1.1.0") or "1.1.0",
            environment=(_get("APP_ENV", "development") or "development").lower(),
            host=_get("HOST", "127.0.0.1") or "127.0.0.1",
            port=_int("PORT", 8000),
            log_level=_get("LOG_LEVEL", "INFO") or "INFO",
            llm_provider=(_get("LLM_PROVIDER", "ollama") or "ollama").lower(),
            ollama_base_url=_get("OLLAMA_BASE_URL", "http://localhost:11434") or "http://localhost:11434",
            ollama_model=_get("OLLAMA_MODEL", "qwen3:4b") or "qwen3:4b",
            groq_model=_get("GROQ_MODEL", "llama-3.3-70b-versatile") or "llama-3.3-70b-versatile",
            gemini_model=_get("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash",
            groq_api_key=_get("GROQ_API_KEY"),
            gemini_api_key=_get("GEMINI_API_KEY"),
            embedding_provider=(_get("EMBEDDING_PROVIDER", "ollama") or "ollama").lower(),
            embedding_model=_get("EMBEDDING_MODEL", "nomic-embed-text") or "nomic-embed-text",
            postgres_url=_get("POSTGRES_URL"),
            postgres_pool_min=_int("POSTGRES_POOL_MIN", 1),
            postgres_pool_max=_int("POSTGRES_POOL_MAX", 8),
            postgres_connect_timeout=_int("POSTGRES_CONNECT_TIMEOUT", 3),
            mongodb_uri=_get("MONGODB_URI", "mongodb://localhost:27017") or "mongodb://localhost:27017",
            mongodb_database=_get("MONGODB_DATABASE", "ecociente") or "ecociente",
            mongodb_timeout_ms=_int("MONGODB_TIMEOUT_MS", 2500),
            redis_url=_get("REDIS_URL", "redis://localhost:6379/0") or "redis://localhost:6379/0",
            redis_socket_timeout=_float("REDIS_SOCKET_TIMEOUT", 2.5),
            session_ttl_seconds=_int("SESSION_TTL_SECONDS", 1800),
            storage_mode=(_get("STORAGE_MODE", "external") or "external").lower(),
            allow_storage_fallback=_bool("ALLOW_STORAGE_FALLBACK", True),
            memory_max_messages=_int("MEMORY_MAX_MESSAGES", 20),
            memory_keep_recent_messages=_int("MEMORY_KEEP_RECENT_MESSAGES", 6),
            neo4j_uri=_get("NEO4J_URI", "bolt://localhost:7687") ,
            neo4j_username=_get("NEO4J_USERNAME", "neo4j") ,
            neo4j_password=_get("NEO4J_PASSWORD", "neo4j") ,
            neo4j_database=_get("NEO4J_DATABASE", "neo4j") ,
            aura_instanceid=_get("AURA_INSTANCEID", "99999xxx") ,
            aura_instancename=_get("AURA_INSTANCENAME", "My instance") ,
            knowledge_base_path=_get("KNOWLEDGE_BASE_PATH", "data/FAQ_KNOWLEDGE_BASE.md") or "data/FAQ_KNOWLEDGE_BASE.md",
            enable_external_source=_bool("ENABLE_EXTERNAL_SOURCE", True),
            external_source_url=_get("EXTERNAL_SOURCE_URL", "https://sinir.gov.br/") or "https://sinir.gov.br/",
            external_source_title=_get("EXTERNAL_SOURCE_TITLE", "SINIR — Sistema Nacional de Informações sobre a Gestão dos Resíduos Sólidos") or "SINIR — Sistema Nacional de Informações sobre a Gestão dos Resíduos Sólidos",
            external_timeout_seconds=_int("EXTERNAL_TIMEOUT_SECONDS", 8),
            rag_top_k=_int("RAG_TOP_K", 4),
            rag_chunk_size=_int("RAG_CHUNK_SIZE", 1000),
            rag_chunk_overlap=_int("RAG_CHUNK_OVERLAP", 150),
            faq_backend=(_get("FAQ_BACKEND", "rag") or "rag").lower(),
            qdrant_url=_get("QDRANT_URL"),
            qdrant_api_key=_get("QDRANT_API_KEY"),
            qdrant_collection=_get("QDRANT_COLLECTION", "faq") or "faq",
            qdrant_embedding_model=_get("QDRANT_EMBEDDING_MODEL", "intfloat/multilingual-e5-large") or "intfloat/multilingual-e5-large",
            qdrant_min_score=_float("QDRANT_MIN_SCORE", 0.80),
            qdrant_top_k=_int("QDRANT_TOP_K", 3),
            qdrant_timeout=_float("QDRANT_TIMEOUT", 10.0),
            faq_llm_fallback=_bool("FAQ_LLM_FALLBACK", False),
            judge_max_corrections=_int("JUDGE_MAX_CORRECTIONS", 1),
            a2a_public_url=_get("A2A_PUBLIC_URL", "http://127.0.0.1:8000/a2a/jsonrpc/") or "http://127.0.0.1:8000/a2a/jsonrpc/",
            auth_api_url=_get("AUTH_API_URL"),
            auth_api_timeout=_float("AUTH_API_TIMEOUT", 8.0),
            auth_api_retries=_int("AUTH_API_RETRIES", 2),
            calendar_api_url=_get("CALENDAR_API_BASE_URL"),
            calendar_api_timeout=_float("CALENDAR_API_TIMEOUT_SECONDS", 10.0),
            calendar_api_retries=_int("CALENDAR_API_RETRIES", 2),
            calendar_test_bearer_token=_get("CALENDAR_TEST_BEARER_TOKEN"),
            run_calendar_integration_tests=_bool("RUN_CALENDAR_INTEGRATION_TESTS", False),
            allow_test_identity_headers=_bool("ALLOW_TEST_IDENTITY_HEADERS", False),
            tracing_provider=(_get("TRACING_PROVIDER", "none") or "none").lower(),
            tracing_mask_pii=_bool("TRACING_MASK_PII", True),
            tracing_in_mock=_bool("TRACING_IN_MOCK", False),
            langfuse_public_key=_get("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=_get("LANGFUSE_SECRET_KEY"),
            langfuse_host=_get("LANGFUSE_HOST", "http://localhost:3000") or "http://localhost:3000",
            langsmith_api_key=_get("LANGSMITH_API_KEY"),
            langsmith_project=_get("LANGSMITH_PROJECT", "ecociente-ia") or "ecociente-ia",
            quota_enabled=_bool("QUOTA_ENABLED", True),
            quota_timezone=_get("QUOTA_TIMEZONE", "America/Sao_Paulo") or "America/Sao_Paulo",
            quota_usuario_comum=_int("QUOTA_USUARIO_COMUM", 20),
            quota_morador_residencial=_int("QUOTA_MORADOR_RESIDENCIAL", 40),
            quota_usuario_comercial=_int("QUOTA_USUARIO_COMERCIAL", 50),
            quota_cooperativa=_int("QUOTA_COOPERATIVA", 80),
            quota_sindico_residencial=_int("QUOTA_SINDICO_RESIDENCIAL", 100),
            quota_sindico_comercial=_int("QUOTA_SINDICO_COMERCIAL", 100),
            quota_no_compaction_profiles=_get("QUOTA_NO_COMPACTION_PROFILES", "USUARIO_COMUM") or "",
            rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 10),
            session_lock_ttl_seconds=_float("SESSION_LOCK_TTL_SECONDS", 30.0),
            session_lock_wait_seconds=_float("SESSION_LOCK_WAIT_SECONDS", 45.0),
            run_integration_tests=_bool("RUN_INTEGRATION_TESTS", False),
            test_postgres_url=_get("TEST_POSTGRES_URL"),
            test_mongodb_uri=_get("TEST_MONGODB_URI"),
            test_mongodb_database=_get("TEST_MONGODB_DATABASE", "ecociente_test") or "ecociente_test",
            test_redis_url=_get("TEST_REDIS_URL"),
            test_redis_prefix=_get("TEST_REDIS_PREFIX", "ecociente:test:") or "ecociente:test:",

        )

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
