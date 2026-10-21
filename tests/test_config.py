import pytest

from src.core.config import Settings


def test_real_integration_tests_require_test_environment() -> None:
    settings = Settings(run_integration_tests=True, environment="development")
    with pytest.raises(RuntimeError, match="APP_ENV=test"):
        settings.validate_test_isolation()


def test_postgres_test_url_cannot_equal_runtime_url() -> None:
    settings = Settings(
        run_integration_tests=True,
        environment="test",
        postgres_url="postgresql://db/prod",
        test_postgres_url="postgresql://db/prod",
    )
    with pytest.raises(RuntimeError, match="TEST_POSTGRES_URL"):
        settings.validate_test_isolation()


def test_test_namespaces_must_differ_from_runtime() -> None:
    settings = Settings(
        run_integration_tests=True,
        environment="test",
        mongodb_database="same",
        test_mongodb_database="same",
    )
    with pytest.raises(RuntimeError, match="TEST_MONGODB_DATABASE"):
        settings.validate_test_isolation()


def test_from_env_reads_renamed_and_typed_vars(monkeypatch):
    monkeypatch.setenv("APP_ENV", "TEST")
    monkeypatch.setenv("CALENDAR_API_BASE_URL", "http://cal")
    monkeypatch.setenv("CALENDAR_API_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "7")
    monkeypatch.setenv("QUOTA_ENABLED", "nao")
    monkeypatch.setenv("LLM_PROVIDER", "MOCK")
    monkeypatch.setenv("QDRANT_URL", "")
    s = Settings.from_env()
    assert (s.environment, s.calendar_api_url, s.calendar_api_timeout) == ("test", "http://cal", 2.5)
    assert (s.rate_limit_per_minute, s.quota_enabled, s.llm_provider, s.qdrant_url) == (7, False, "mock", None)
