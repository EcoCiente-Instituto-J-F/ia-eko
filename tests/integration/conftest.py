from __future__ import annotations

import pytest

from src.core.config import Settings


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    settings = Settings.from_env()
    if not settings.run_integration_tests:
        pytest.skip("RUN_INTEGRATION_TESTS=false")
    settings.validate_test_isolation()
    return settings
