"""Lakebase-backed durable checkpointer for the ClaimsCopilot agent graph.

When `CC_CHECKPOINTER=lakebase`, the graph compiles with LangGraph's
`AsyncPostgresSaver` so per-super-step state survives a container restart and is
shared across uvicorn workers — closing the "in-process MemorySaver is lost on
restart" gap.

Connection follows the official Databricks Apps + Lakebase pattern: a psycopg3
async pool whose connection class mints a fresh OAuth credential on every new
physical connection (`w.postgres.generate_database_credential`), so tokens never
go stale and no background refresh task is needed. `max_lifetime=2700` recycles
connections ~15 min before the 1-hour token expires.

Heavy deps (`psycopg`, `psycopg_pool`, `langgraph.checkpoint.postgres`) are
imported lazily inside `open_lakebase_saver`, so importing this module — and
running the default "memory" mode — never requires them.

ENABLEMENT CHECKLIST (cannot be validated from the offline build env — do at deploy):
  1. Create a Lakebase project/endpoint (see scripts/create_lakebase.sh).
  2. Attach it to the App as a database resource (injects PGHOST/PGUSER/PGPORT/
     PGDATABASE) and set ENDPOINT_NAME + CC_CHECKPOINTER=lakebase in app.yaml.
  3. Bump `databricks-sdk` to a version exposing `w.postgres` (see requirements.txt).
  4. Ensure PGUSER (the App SP) can CREATE in PGDATABASE so `saver.setup()` can
     create the checkpoint tables on first boot.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from backend.agent.config import Settings

# Env vars the Databricks Apps runtime injects when a Lakebase database resource
# is attached (PGUSER = the App service principal's client id). ENDPOINT_NAME is
# set explicitly in app.yaml (resource path of the read-write endpoint).
_REQUIRED_ENV = ("PGHOST", "PGUSER", "ENDPOINT_NAME")


def _mint_credential_token() -> str:
    """Blocking: mint a short-lived Lakebase OAuth credential via the App SP.

    Kept tiny and side-effect-isolated so it can be offloaded with
    `asyncio.to_thread` and stubbed in tests.
    """
    from databricks.sdk import WorkspaceClient

    endpoint = os.environ["ENDPOINT_NAME"]
    w = WorkspaceClient()
    postgres = getattr(w, "postgres", None)
    if postgres is None:  # databricks-sdk too old for the Lakebase API
        raise RuntimeError(
            "databricks-sdk does not expose `w.postgres.generate_database_credential`; "
            "bump databricks-sdk to a Lakebase-capable version (see requirements.txt) "
            "to use CC_CHECKPOINTER=lakebase."
        )
    return postgres.generate_database_credential(endpoint=endpoint).token


def build_conninfo(env: dict[str, str] | None = None) -> str:
    """Build a libpq conninfo string from the injected PG* env (no password —
    that is supplied per-connection by the OAuth connection class)."""
    env = env if env is not None else dict(os.environ)
    missing = [k for k in _REQUIRED_ENV if not env.get(k)]
    if missing:
        raise RuntimeError(
            f"CC_CHECKPOINTER=lakebase but required env is missing: {missing}. "
            "Attach a Lakebase database resource to the App and set ENDPOINT_NAME."
        )
    host = env["PGHOST"]
    user = env["PGUSER"]
    port = env.get("PGPORT", "5432")
    database = env.get("PGDATABASE", "databricks_postgres")
    sslmode = env.get("PGSSLMODE", "require")
    return f"dbname={database} user={user} host={host} port={port} sslmode={sslmode}"


async def open_lakebase_saver(settings: Settings) -> tuple[Any, Any]:
    """Open a token-refreshing psycopg pool and wrap it in an AsyncPostgresSaver.

    Returns (saver, pool). The caller owns the pool and must `await pool.close()`
    on shutdown. Raises (fail-fast) if env is missing or the DB is unreachable —
    we'd rather refuse to start than silently accept turns that lose state.
    """
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    class _OAuthAsyncConnection(psycopg.AsyncConnection):
        """psycopg connection that injects a fresh Lakebase token on connect."""

        @classmethod
        async def connect(cls, conninfo: str = "", **kwargs):
            # generate_database_credential is a blocking SDK call — offload so it
            # doesn't pin the event loop (same rule as the agent's tools).
            kwargs["password"] = await asyncio.to_thread(_mint_credential_token)
            return await super().connect(conninfo, **kwargs)

    conninfo = build_conninfo()
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        connection_class=_OAuthAsyncConnection,
        min_size=1,
        max_size=settings.lakebase_pool_max_size,
        max_lifetime=2700,  # 45 min — recycle before the 1-hour OAuth token expires
        open=False,         # opened explicitly below so we fail fast if unreachable
        # LangGraph's Postgres saver requires autocommit + dict rows; prepare_threshold=0
        # avoids server-side prepared-statement issues behind connection poolers.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    await pool.open(wait=True, timeout=30.0)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # idempotent: creates checkpoint tables/migrations
    return saver, pool
