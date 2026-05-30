"""FastAPI app served by Databricks Apps. Serves React static + REST + SSE."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

import mlflow
import orjson
import structlog
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from backend.agent.agent import build_agent
from backend.agent.config import Settings, get_settings
from backend.agent.tools import bind_sql
from backend.auth import get_user_context
from backend.schemas import (
    ChatRequest,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    SessionDetailResponse,
    SessionListResponse,
    UserContext,
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    mlflow.set_experiment(settings.mlflow_experiment)
    mlflow.langchain.autolog(disable=False, log_traces=True, log_models=False)
    agent = build_agent()
    # Opens the Lakebase connection pool when CC_CHECKPOINTER=lakebase (fails
    # fast if the DB is unreachable); no-op for memory/none.
    await agent.aopen()
    app.state.agent = agent
    log.info(
        "claimscopilot_started",
        version=settings.agent_version, env=settings.app_env, region=settings.region,
        checkpointer=settings.checkpointer,
    )
    yield
    await agent.aclose()
    log.info("claimscopilot_shutdown")


app = FastAPI(
    title="ClaimsCopilot",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/api/docs", openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- /api/health ------------------------------------------------------------

@app.get("/api/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok" if settings.enabled else "degraded",
        agent_version=settings.agent_version,
        region=settings.region,
        upstream={
            "chat_primary": settings.chat_endpoint_primary,
            "embed_en": settings.embed_endpoint_en,
            "embed_ml": settings.embed_endpoint_multilingual,
            "vs_endpoint": settings.vs_endpoint,
        },
    )


# ---- /api/readiness ---------------------------------------------------------

@app.get("/api/readiness")
async def readiness(settings: Settings = Depends(get_settings)):
    """Deep dependency probe (serving endpoints, warehouse, UC catalog/schemas,
    Vector Search, MLflow) so the UI can show a 'run setup' banner instead of
    failing silently. On-demand (not on the cheap /api/health path)."""
    from backend.readiness import check_readiness
    return await asyncio.to_thread(check_readiness, settings)


# ---- /api/chat (SSE) --------------------------------------------------------

@app.post("/api/chat")
async def chat(
    body: ChatRequest,
    user: Annotated[UserContext, Depends(get_user_context)],
    settings: Settings = Depends(get_settings),
):
    if not settings.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ClaimsCopilot is temporarily unavailable.",
        )

    session_id = body.session_id or str(uuid.uuid4())
    agent = app.state.agent

    async def event_stream():
        try:
            async for ev in agent.run_stream(
                user=user,
                session_id=session_id,
                claim_id=body.claim_id,
                message=body.message,
                language=body.language,
                confirmations=body.confirmations,
            ):
                yield {
                    "event": ev.get("event", "message"),
                    "data": orjson.dumps(ev, default=str).decode(),
                }
        except Exception as e:  # noqa: BLE001
            log.exception("chat_stream_failure")
            yield {
                "event": "error",
                "data": orjson.dumps({
                    "event": "error",
                    "code": "INTERNAL",
                    "message": str(e),
                    "recoverable": False,
                }).decode(),
            }

    return EventSourceResponse(event_stream(), headers={"x-session-id": session_id})


# ---- /api/sessions ----------------------------------------------------------

@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    user: Annotated[UserContext, Depends(get_user_context)],
    settings: Settings = Depends(get_settings),
) -> SessionListResponse:
    from databricks.sdk import WorkspaceClient
    ws = WorkspaceClient()  # App SP creds (DATABRICKS_CLIENT_ID/SECRET)
    statement, params = bind_sql(
        f"SELECT session_id, claim_id, started_at, last_activity_at, status, "
        f"COALESCE(metadata['title'], CONCAT('Session ', SUBSTR(session_id,1,8))) AS title, "
        f"(SELECT COUNT(*) FROM {settings.message_table} m WHERE m.session_id=s.session_id) AS message_count "
        f"FROM {settings.session_table} s "
        f"WHERE user_id = ? "
        f"ORDER BY last_activity_at DESC LIMIT 50",
        [user.user_id],
    )
    # Offload the blocking SDK call off the event loop (see agent.tools).
    resp = await asyncio.to_thread(
        ws.statement_execution.execute_statement,
        statement=statement, warehouse_id=settings.databricks_warehouse_id,
        parameters=params, wait_timeout="15s",
    )
    rows = (resp.result.data_array or []) if resp.result else []
    sessions = [{
        "session_id": r[0], "claim_id": r[1],
        "started_at": r[2], "last_activity_at": r[3],
        "status": r[4], "title": r[5], "message_count": int(r[6] or 0),
    } for r in rows]
    return SessionListResponse.model_validate({"sessions": sessions})


@app.get("/api/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    user: Annotated[UserContext, Depends(get_user_context)],
    settings: Settings = Depends(get_settings),
):
    from databricks.sdk import WorkspaceClient
    ws = WorkspaceClient()  # App SP creds (DATABRICKS_CLIENT_ID/SECRET)
    s_stmt, s_params = bind_sql(
        f"SELECT session_id, claim_id, started_at, last_activity_at, status "
        f"FROM {settings.session_table} WHERE session_id = ? AND user_id = ?",
        [session_id, user.user_id],
    )
    s_resp = await asyncio.to_thread(
        ws.statement_execution.execute_statement,
        statement=s_stmt, warehouse_id=settings.databricks_warehouse_id,
        parameters=s_params, wait_timeout="15s",
    )
    srows = (s_resp.result.data_array or []) if s_resp.result else []
    if not srows:
        raise HTTPException(status_code=404, detail="Session not found")
    s = srows[0]
    m_stmt, m_params = bind_sql(
        f"SELECT message_id, role, content, language, citations, trace_id, created_at "
        f"FROM {settings.message_table} WHERE session_id = ? ORDER BY turn_index ASC",
        [session_id],
    )
    m_resp = await asyncio.to_thread(
        ws.statement_execution.execute_statement,
        statement=m_stmt, warehouse_id=settings.databricks_warehouse_id,
        parameters=m_params, wait_timeout="15s",
    )
    mrows = (m_resp.result.data_array or []) if m_resp.result else []
    messages = [{
        "message_id": r[0], "role": r[1], "content": r[2], "language": r[3],
        "citations": json.loads(r[4]) if r[4] else [],
        "trace_id": r[5], "created_at": r[6],
    } for r in mrows]
    return SessionDetailResponse.model_validate({
        "session": {
            "session_id": s[0], "claim_id": s[1],
            "started_at": s[2], "last_activity_at": s[3],
            "status": s[4],
            "title": f"Claim {s[1] or session_id[:8]}",
            "message_count": len(messages),
        },
        "messages": messages,
    })


# ---- /api/feedback ----------------------------------------------------------

@app.post("/api/feedback", response_model=FeedbackResponse)
async def feedback(
    body: FeedbackRequest,
    user: Annotated[UserContext, Depends(get_user_context)],
    settings: Settings = Depends(get_settings),
) -> FeedbackResponse:
    from databricks.sdk import WorkspaceClient
    ws = WorkspaceClient()  # App SP creds (DATABRICKS_CLIENT_ID/SECRET)
    fb_id = str(uuid.uuid4())
    statement, params = bind_sql(
        f"INSERT INTO {settings.feedback_table} "
        "(feedback_id, session_id, message_id, user_id, thumbs, rating, "
        " reason_codes, free_text, created_at) "
        "VALUES (?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP())",
        [
            fb_id, body.session_id,
            body.message_id or "", user.user_id,
            body.thumbs or "", str(body.rating or ""),
            json.dumps(body.reason_codes), body.free_text or "",
        ],
    )
    await asyncio.to_thread(
        ws.statement_execution.execute_statement,
        statement=statement,
        warehouse_id=settings.databricks_warehouse_id,
        parameters=params,
        wait_timeout="15s",
    )
    return FeedbackResponse(feedback_id=fb_id)


# ---- Static assets (React build) -------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    log.warning("static_dir_missing", path=str(STATIC_DIR))
