"""Regression for failure-mode #2: a slow/blocking Databricks SDK call inside
an async tool must NOT wedge the asyncio event loop.

Before the fix, `ws.statement_execution.execute_statement(...)` ran directly
inside the coroutine, so a stalled SQL warehouse pinned the loop for the whole
`wait_timeout` and the wrapping `asyncio.wait_for` could not cancel it. The fix
offloads the blocking call via `asyncio.to_thread`. We assert the loop keeps
making progress (a concurrent ticker advances) while the tool's blocking call
is in flight.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import pytest

from backend.agent import tools as tools_mod
from backend.agent.tools import GetClaimIn
from backend.schemas import UserContext

BLOCK_SECONDS = 0.4


@contextmanager
def _noop_span(*_a, **_k):
    class _S:
        def set_attribute(self, *_x, **_y):
            return None

    yield _S()


class _FakeStatus:
    error = None


class _FakeResult:
    data_array = [['{"claim_id": "CLM-GB-1042", "status": "OPEN"}']]


class _FakeResp:
    status = _FakeStatus()
    result = _FakeResult()


class _FakeStmtExec:
    def execute_statement(self, **kwargs):
        # Simulate a slow/stalled SQL warehouse: a *blocking* sync call.
        time.sleep(BLOCK_SECONDS)
        return _FakeResp()


class _FakeWs:
    statement_execution = _FakeStmtExec()


@pytest.fixture
def user() -> UserContext:
    return UserContext(
        user_id="a@example.com", email="a@example.com", display_name="A",
        role="ADJUSTER_L2", country="GB", obo_token="tok",
        workspace_host="https://example.cloud.databricks.com",
    )


async def test_blocking_sql_does_not_wedge_event_loop(monkeypatch, user):
    monkeypatch.setattr(tools_mod, "_ws_client", lambda u: _FakeWs())
    monkeypatch.setattr(tools_mod.mlflow, "start_span", _noop_span)

    ticks = 0

    async def ticker():
        nonlocal ticks
        for _ in range(200):
            await asyncio.sleep(0.01)
            ticks += 1

    tick_task = asyncio.create_task(ticker())
    result = await tools_mod.get_claim(GetClaimIn(claim_id="CLM-GB-1042"), user)
    tick_task.cancel()

    # If the loop had been blocked for the whole BLOCK_SECONDS, the ticker would
    # not have advanced. With to_thread it keeps ticking (~40 ticks in 0.4s).
    assert ticks >= 10, f"event loop appears blocked: only {ticks} ticks elapsed"
    assert result.get("claim_id") == "CLM-GB-1042"


async def test_concurrent_tools_run_in_parallel(monkeypatch, user):
    """Offloading blocking SDK calls to threads lets concurrent tool calls
    overlap. If they still ran on the loop, four 0.4s calls would serialize to
    ~1.6s; threaded they finish in ~0.4-0.6s."""
    monkeypatch.setattr(tools_mod, "_ws_client", lambda u: _FakeWs())
    monkeypatch.setattr(tools_mod.mlflow, "start_span", _noop_span)

    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[tools_mod.get_claim(GetClaimIn(claim_id="CLM-GB-1042"), user) for _ in range(4)]
    )
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"tools serialized on the event loop: {elapsed:.2f}s for 4x{BLOCK_SECONDS}s"
    assert all(r.get("claim_id") == "CLM-GB-1042" for r in results)
