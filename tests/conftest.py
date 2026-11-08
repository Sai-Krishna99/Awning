from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from awning.config import Settings, load_settings
from awning.context import AppContext, build_context
from awning.web.app import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        {
            "PAYPAL_CLIENT_ID": "test-client",
            "PAYPAL_CLIENT_SECRET": "test-secret",
            "ANTHROPIC_API_KEY": "test-key",
            "AWNING_DB_PATH": str(tmp_path / "test.db"),
        }
    )


@pytest.fixture
def ctx(settings: Settings) -> AppContext:
    return build_context(settings)


@pytest.fixture
def client(ctx: AppContext) -> TestClient:
    return TestClient(create_app(ctx))
