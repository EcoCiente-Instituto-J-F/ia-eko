from __future__ import annotations

import pytest

from src.core.config import Settings


@pytest.fixture()
def client():
    # Import tardio: testes de contrato/segurança continuam executáveis mesmo
    # quando o ambiente de validação não instalou as dependências do runtime.
    pytest.importorskip("langgraph", reason="langgraph não está instalado neste ambiente de validação")
    from fastapi.testclient import TestClient
    from src.api.main import create_app

    settings = Settings.from_env().with_overrides(
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        storage_mode="memory",
        allow_storage_fallback=True,
        enable_external_source=False,
        postgres_url=None,
        allow_test_identity_headers=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
