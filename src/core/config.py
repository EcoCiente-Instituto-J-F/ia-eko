from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass(slots=True)
class Settings:
    app_name: str = "EcoCiente IA API"
    app_version: str = "1.0.0"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000

    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str | None = None
    gemini_api_key: str | None = None

    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "ecociente"
    redis_url: str = "redis://localhost:6379/0"
    postgres_url: str | None = None
    session_ttl_seconds: int = 1800
    storage_mode: str = "external"
    allow_storage_fallback: bool = True

    knowledge_base_path: str = "data/FAQ_KNOWLEDGE_BASE.md"
    enable_external_source: bool = True
    external_source_url: str = "https://sinir.gov.br/"
    external_source_title: str = "SINIR — Sistema Nacional de Informações sobre a Gestão dos Resíduos Sólidos"
    external_timeout_seconds: int = 8
    rag_top_k: int = 4
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 150

    judge_max_corrections: int = 1
    a2a_public_url: str = "http://127.0.0.1:8000/a2a/jsonrpc/"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("APP_NAME", "EcoCiente IA API"),
            app_version=os.getenv("APP_VERSION", "1.0.0"),
            environment=os.getenv("ENVIRONMENT", "development"),
            host=os.getenv("HOST", "127.0.0.1"),
            port=_int("PORT", 8000),
            llm_provider=os.getenv("LLM_PROVIDER", "ollama").lower(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
            groq_model=os.getenv("GROQ_MODEL", os.getenv("GROQ_LLAMA", "llama-3.3-70b-versatile")),
            gemini_model=os.getenv("GEMINI_MODEL", os.getenv("GEMINI_FLASH", "gemini-2.5-flash")),
            groq_api_key=os.getenv("GROQ_API_KEY"),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            embedding_provider=os.getenv("EMBEDDING_PROVIDER", "ollama").lower(),
            embedding_model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
            mongodb_uri=os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "mongodb://localhost:27017",
            mongodb_database=os.getenv("MONGODB_DATABASE", "ecociente"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            postgres_url=(os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL") or os.getenv("POSTGRE_URI")),
            session_ttl_seconds=_int("SESSION_TTL_SECONDS", 1800),
            storage_mode=os.getenv("STORAGE_MODE", "external").lower(),
            allow_storage_fallback=_bool("ALLOW_STORAGE_FALLBACK", True),
            knowledge_base_path=os.getenv("KNOWLEDGE_BASE_PATH", "data/FAQ_KNOWLEDGE_BASE.md"),
            enable_external_source=_bool("ENABLE_EXTERNAL_SOURCE", True),
            external_source_url=os.getenv("EXTERNAL_SOURCE_URL", "https://sinir.gov.br/"),
            external_source_title=os.getenv("EXTERNAL_SOURCE_TITLE", "SINIR — Sistema Nacional de Informações sobre a Gestão dos Resíduos Sólidos"),
            external_timeout_seconds=_int("EXTERNAL_TIMEOUT_SECONDS", 8),
            rag_top_k=_int("RAG_TOP_K", 4),
            rag_chunk_size=_int("RAG_CHUNK_SIZE", 1000),
            rag_chunk_overlap=_int("RAG_CHUNK_OVERLAP", 150),
            judge_max_corrections=_int("JUDGE_MAX_CORRECTIONS", 1),
            a2a_public_url=os.getenv("A2A_PUBLIC_URL", "http://127.0.0.1:8000/a2a/jsonrpc/"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def with_overrides(self, **changes: object) -> "Settings":
        return replace(self, **changes)

    @property
    def knowledge_base_file(self) -> Path:
        path = Path(self.knowledge_base_path)
        if path.is_absolute():
            return path
        return Path(__file__).resolve().parents[2] / path


settings = Settings.from_env()