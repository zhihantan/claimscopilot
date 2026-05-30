"""Offline tests for the Lakebase durable-checkpointer wiring.

psycopg / langgraph-postgres and a live Lakebase are not available in this env,
so we cover the parts that don't need them: checkpointer-mode routing, OBO-token
redaction, conninfo building, and the fail-fast when the SDK lacks the Lakebase
API. The pool/saver path itself is validated at deploy.
"""

from __future__ import annotations

import sys
import types

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend.agent import agent as agent_mod
from backend.agent import lakebase as lb
from backend.agent.config import get_settings
from backend.schemas import UserContext


def _settings(mode: str):
    return get_settings().model_copy(update={"checkpointer": mode})


def test_make_checkpointer_modes():
    assert isinstance(agent_mod.make_checkpointer(_settings("memory")), MemorySaver)
    assert agent_mod.make_checkpointer(_settings("none")) is None
    # lakebase is opened asynchronously in aopen(); the sync factory returns None.
    assert agent_mod.make_checkpointer(_settings("lakebase")) is None


def test_redact_user_blanks_token_keeps_identity():
    u = UserContext(
        user_id="a@x.com", email="a@x.com", display_name="A", role="ADJUSTER_L2",
        country="GB", obo_token="secret-token",
        workspace_host="https://x.cloud.databricks.com",
    )
    r = agent_mod._redact_user(u)
    assert r.obo_token == agent_mod._OBO_REDACTED != "secret-token"
    assert (r.user_id, r.role, r.country) == ("a@x.com", "ADJUSTER_L2", "GB")


async def test_aopen_is_noop_for_memory_mode():
    agent = agent_mod.build_agent()  # default CC_CHECKPOINTER=memory
    assert isinstance(agent.checkpointer, MemorySaver)
    await agent.aopen()
    assert isinstance(agent.checkpointer, MemorySaver)
    assert agent._pool is None
    await agent.aclose()  # idempotent with no pool


def test_build_conninfo_happy_path_and_no_password():
    env = {
        "PGHOST": "ep.database.cloud.databricks.com", "PGUSER": "sp-client-id",
        "PGPORT": "5432", "PGDATABASE": "claimscopilot", "PGSSLMODE": "require",
        "ENDPOINT_NAME": "projects/x/branches/production/endpoints/primary",
    }
    ci = lb.build_conninfo(env)
    assert "host=ep.database.cloud.databricks.com" in ci
    assert "user=sp-client-id" in ci
    assert "dbname=claimscopilot" in ci
    assert "sslmode=require" in ci
    assert "password" not in ci  # token is injected per-connection, never in conninfo


def test_build_conninfo_raises_on_missing_env():
    with pytest.raises(RuntimeError, match="required env is missing"):
        lb.build_conninfo({"PGHOST": "h"})  # missing PGUSER + ENDPOINT_NAME


def _fake_sdk_module(ws_cls) -> types.ModuleType:
    mod = types.ModuleType("databricks.sdk")
    mod.WorkspaceClient = ws_cls
    return mod


def test_mint_credential_fails_fast_on_old_sdk(monkeypatch):
    monkeypatch.setenv("ENDPOINT_NAME", "projects/x/branches/production/endpoints/primary")

    class _OldWs:  # no `postgres` attribute — mirrors databricks-sdk 0.40
        pass

    monkeypatch.setitem(sys.modules, "databricks.sdk", _fake_sdk_module(_OldWs))
    with pytest.raises(RuntimeError, match="w.postgres"):
        lb._mint_credential_token()


def test_mint_credential_returns_token_on_new_sdk(monkeypatch):
    endpoint = "projects/x/branches/production/endpoints/primary"
    monkeypatch.setenv("ENDPOINT_NAME", endpoint)

    class _Cred:
        token = "tok-123"

    class _Postgres:
        def generate_database_credential(self, endpoint):
            assert endpoint == "projects/x/branches/production/endpoints/primary"
            return _Cred()

    class _NewWs:
        postgres = _Postgres()

    monkeypatch.setitem(sys.modules, "databricks.sdk", _fake_sdk_module(_NewWs))
    assert lb._mint_credential_token() == "tok-123"
