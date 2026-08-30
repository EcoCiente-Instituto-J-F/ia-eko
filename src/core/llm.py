from __future__ import annotations

from typing import Any

from src.core.config import Settings


class ProviderConfigurationError(RuntimeError):
    pass


def build_chat_model(settings: Settings) -> Any | None:
    """Cria o chat model. `mock` não cria modelo e nunca chama API externa."""
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return None
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            temperature=0.1,
        )
    if provider == "groq":
        if not settings.groq_api_key:
            raise ProviderConfigurationError("GROQ_API_KEY é obrigatória quando LLM_PROVIDER=groq.")
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Instale requirements-optional.txt para usar Groq."
            ) from exc
        return ChatGroq(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            temperature=0.1,
        )
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ProviderConfigurationError("GEMINI_API_KEY é obrigatória quando LLM_PROVIDER=gemini.")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Instale requirements-optional.txt para usar Gemini."
            ) from exc
        return ChatGoogleGenerativeAI(
            google_api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            temperature=0.1,
        )
    raise ProviderConfigurationError(
        f"LLM_PROVIDER={provider!r} inválido. Use ollama, mock, groq ou gemini."
    )


def build_embeddings(settings: Settings) -> Any:
    provider = settings.embedding_provider.lower()
    if provider == "mock":
        from src.services.rag_service import HashEmbeddings

        return HashEmbeddings()
    if provider in {"ollama", "local"}:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
        )
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise ProviderConfigurationError(
                "GEMINI_API_KEY é obrigatória quando EMBEDDING_PROVIDER=gemini."
            )
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Instale requirements-optional.txt para usar embeddings Gemini."
            ) from exc
        return GoogleGenerativeAIEmbeddings(
            google_api_key=settings.gemini_api_key,
            model=settings.embedding_model,
        )
    raise ProviderConfigurationError(
        f"EMBEDDING_PROVIDER={provider!r} inválido. Use ollama/local, mock ou gemini."
    )
