"""TestClient smoke for /api/health — the cheapest check that the FastAPI
app boots cleanly without contacting Databricks. The agent build is
patched out so the lifespan doesn't try to wire up MLflow autolog."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    @asynccontextmanager
    async def _no_op_lifespan(app):
        app.state.agent = object()
        yield

    with patch("backend.main.lifespan", _no_op_lifespan):
        import importlib

        import backend.main as main_mod

        importlib.reload(main_mod)
        with TestClient(main_mod.app) as c:
            yield c


def test_health_returns_ok_shape(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "agent_version" in body
    assert body["region"] in {"EMEA", "APAC", "AMER"}
    assert set(body["upstream"]).issuperset(
        {"chat_primary", "embed_en", "embed_ml", "vs_endpoint"}
    )
