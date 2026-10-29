from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings

from src.core.config import Settings


class ProviderConfigurationError(RuntimeError):
    pass


# provedor → (model_provider do LangChain, campo do modelo, campo da chave)
_CHAT_PROVIDERS = {
    "ollama": ("ollama", "ollama_model", None),
    "groq": ("groq", "groq_model", "groq_api_key"),
    "gemini": ("google_genai", "gemini_model", "gemini_api_key"),
    "deepseek": ("deepseek", "deepseek_model", "deepseek_api_key"),
}
# Gemini usa o nome explícito do campo (o alias api_key varia entre versões do pacote)
_KEY_KWARG = {"gemini": "google_api_key"}


def build_chat_model(settings: Settings) -> Any | None:
    """Cria o chat model. `mock` não cria modelo e nunca chama API externa."""
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return None
    if provider not in _CHAT_PROVIDERS:
        raise ProviderConfigurationError(
            f"LLM_PROVIDER={provider!r} inválido. Use ollama, mock, groq, gemini ou deepseek."
        )
    model_provider, model_field, key_field = _CHAT_PROVIDERS[provider]
    kwargs: dict[str, Any] = {"temperature": 0.1}
    if key_field is None:
        kwargs["base_url"] = settings.ollama_base_url
    else:
        if not getattr(settings, key_field):
            raise ProviderConfigurationError(f"{key_field.upper()} é obrigatória quando LLM_PROVIDER={provider}.")
        kwargs[_KEY_KWARG.get(provider, "api_key")] = getattr(settings, key_field)
    try:
        return init_chat_model(getattr(settings, model_field), model_provider=model_provider, **kwargs)
    except ImportError as exc:
        raise ProviderConfigurationError(f"Instale requirements-optional.txt para usar {provider}.") from exc


def build_embeddings(settings: Settings) -> Any:
    provider = settings.embedding_provider.lower()
    if provider == "mock":
        from src.services.rag_service import HashEmbeddings

        return HashEmbeddings()
    if provider == "local":  # alias de ollama
        provider = "ollama"
    lc_provider = provider
    if provider == "ollama":
        kwargs: dict[str, Any] = {"base_url": settings.ollama_base_url}
    elif provider == "gemini":
        if not settings.gemini_api_key:
            raise ProviderConfigurationError("GEMINI_API_KEY é obrigatória quando EMBEDDING_PROVIDER=gemini.")
        lc_provider, kwargs = "google_genai", {"google_api_key": settings.gemini_api_key}
    else:
        raise ProviderConfigurationError(
            f"EMBEDDING_PROVIDER={provider!r} inválido. Use ollama/local, mock ou gemini."
        )
    try:
        return init_embeddings(settings.embedding_model, provider=lc_provider, **kwargs)
    except ImportError as exc:
        raise ProviderConfigurationError(f"Instale requirements-optional.txt para usar embeddings {provider}.") from exc
