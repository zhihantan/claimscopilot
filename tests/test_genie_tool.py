"""Tests for the Genie NL->SQL agent tool (mocking the Databricks SDK genie
client — no live workspace)."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from backend.agent import tools as tools_mod
from backend.agent.tools import QueryGenieSpaceIn
from backend.schemas import UserContext


@contextmanager
def _noop_span(*_a, **_k):
    class _S:
        def set_attribute(self, *_x, **_y):
            return None

    yield _S()


@pytest.fixture
def user() -> UserContext:
    return UserContext(
        user_id="a@example.com", email="a@example.com", display_name="A",
        role="ADJUSTER_L2", country="GB", obo_token="t",
        workspace_host="https://example.cloud.databricks.com",
    )


def _fake_ws(*, sql="SELECT avg(paid_amount) AS avg_paid FROM claims",
             desc="Average paid amount", rows=None, cols=("avg_paid",), raise_on=None):
    rows = [["123.45"]] if rows is None else rows

    class _Genie:
        def start_conversation_and_wait(self, space_id, content):
            if raise_on == "start":
                raise RuntimeError("genie boom")
            return NS(attachments=[NS(query=NS(query=sql, description=desc), text=None)],
                      conversation_id="conv-1", id="msg-1")

        def get_message_query_result(self, space_id, conversation_id, message_id):
            return NS(statement_response=NS(
                result=NS(data_array=rows),
                manifest=NS(schema=NS(columns=[NS(name=c) for c in cols]))))

    return NS(genie=_Genie())


async def test_genie_happy_path(monkeypatch, user):
    monkeypatch.setattr(tools_mod, "_ws_client", lambda u: _fake_ws())
    monkeypatch.setattr(tools_mod.mlflow, "start_span", _noop_span)
    out = await tools_mod.query_genie_space(
        QueryGenieSpaceIn(question="average paid for liquid damage in GB?", space_id="space-1"),
        user,
    )
    assert out["sql"].startswith("SELECT avg")
    assert out["summary"] == "Average paid amount"
    assert out["columns"] == ["avg_paid"]
    assert out["rows"] == [["123.45"]]
    assert out["row_count"] == 1


async def test_genie_not_configured_returns_envelope(monkeypatch, user):
    monkeypatch.setattr(tools_mod.mlflow, "start_span", _noop_span)
    # No space_id arg and CC_GENIE_SPACE_ID unset (conftest doesn't set it).
    out = await tools_mod.query_genie_space(QueryGenieSpaceIn(question="anything?"), user)
    assert out["error"]["code"] == "GENIE_NOT_CONFIGURED"


async def test_genie_error_is_caught(monkeypatch, user):
    monkeypatch.setattr(tools_mod, "_ws_client", lambda u: _fake_ws(raise_on="start"))
    monkeypatch.setattr(tools_mod.mlflow, "start_span", _noop_span)
    out = await tools_mod.query_genie_space(
        QueryGenieSpaceIn(question="what is the average?", space_id="space-1"), user)
    assert out["error"]["code"] == "GENIE_ERROR"


def test_genie_hidden_from_planner_when_unconfigured():
    # With no CC_GENIE_SPACE_ID, the planner must not be offered the tool.
    names = {s["tool"] for s in tools_mod.get_tool_specs_for_planner()}
    assert "query_genie_space" not in names
    # But it is always registered/callable.
    assert "query_genie_space" in tools_mod.ALL_TOOLS
