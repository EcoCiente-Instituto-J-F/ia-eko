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
