from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from customer_support_agent.api.app_factory import create_app
from customer_support_agent.core.settings import Settings
from customer_support_agent.repositories.sqlite import init_db


@pytest.fixture()
def test_settings(tmp_path: Path) -> Settings:
    """Minimal settings pointing at a temp directory — no real API keys."""
    return Settings(
        workspace_dir=tmp_path,
        groq_api_key="",
        google_api_key="",
        openai_api_key="",
        enable_local_embeddings=False,
    )


@pytest.fixture()
def patched_db(test_settings: Settings):
    """Patch the DB layer to an isolated temp SQLite file and init schema."""
    with patch(
        "customer_support_agent.repositories.sqlite.base.get_settings",
        return_value=test_settings,
    ):
        init_db()
        yield test_settings


@pytest.fixture()
def api_client(test_settings: Settings) -> TestClient:
    """TestClient backed by a fully isolated temp database."""
    with patch(
        "customer_support_agent.repositories.sqlite.base.get_settings",
        return_value=test_settings,
    ):
        app = create_app(settings=test_settings)
        with TestClient(app) as client:
            yield client


@pytest.fixture(autouse=True)
def _clear_copilot_cache() -> None:
    """Reset lru_cache on get_copilot between tests to prevent state bleed."""
    from customer_support_agent.api.dependencies import get_copilot

    get_copilot.cache_clear()
    yield
    get_copilot.cache_clear()
