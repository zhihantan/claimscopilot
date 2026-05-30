"""Pydantic v2 models for every request and response surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---- Auth / identity --------------------------------------------------------


class UserContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    email: str
    display_name: Optional[str] = None
    role: Literal["ADJUSTER_L1", "ADJUSTER_L2", "ADJUSTER_L3", "TEAM_LEAD", "QA", "ADMIN"]
    country: str
    obo_token: str  # NEVER serialize; excluded below
    workspace_host: str

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude={"obo_token"})


# ---- /api/chat --------------------------------------------------------------


class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    claim_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=4000)
    language: Literal["en", "es", "ja"] = "en"
    confirmations: list[str] = Field(
        default_factory=list,
        description="Confirmation tokens emitted by the UI for write-tool gating.",
    )


class CitationRef(BaseModel):
    kind: Literal["policy", "kb", "claim"]
    label: str
    ref: str  # e.g. "WORD-GB-MOBILE-FULL-V2025-04#sec=3.2"


class ToolCallSummary(BaseModel):
    tool_call_id: str
    tool_name: str
    args_preview: str
    latency_ms: int
    error: Optional[str] = None


# SSE event payloads (one model per event type)


class SSESessionStart(BaseModel):
    event: Literal["session.start"] = "session.start"
    trace_id: str
    model: str
    ts: datetime


class PlanItem(BaseModel):
    tool: str
    args: dict[str, Any]
    why: str


class SSEPlan(BaseModel):
    event: Literal["plan"] = "plan"
    plan: list[PlanItem]
    stop_if_enough: bool


class SSEToolStart(BaseModel):
    event: Literal["tool.start"] = "tool.start"
    call_id: str
    tool: str
    args: dict[str, Any]


class SSEToolEnd(BaseModel):
    event: Literal["tool.end"] = "tool.end"
    call_id: str
    tool: str
    result_preview: str
    latency_ms: int
    error: Optional[str] = None


class SSEReflect(BaseModel):
    event: Literal["reflect"] = "reflect"
    decision: Literal["more", "done"]
    note: str


class SSEToken(BaseModel):
    event: Literal["token"] = "token"
    delta: str


class SSECitation(BaseModel):
    event: Literal["citation"] = "citation"
    citation: CitationRef


class SSEError(BaseModel):
    event: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool


class SSEDone(BaseModel):
    event: Literal["done"] = "done"
    trace_id: str
    latency_ms_total: int
    cost_usd: float
    fallback_step: int
    decision_class: Literal[
        "APPROVE", "PARTIAL_APPROVE", "DENY", "REQUEST_DOCS",
        "UPDATE_STATUS", "UNDETERMINED"
    ]
    confidence: Literal["LOW", "MED", "HIGH"]


# ---- /api/sessions ----------------------------------------------------------


class SessionSummary(BaseModel):
    session_id: str
    claim_id: Optional[str]
    title: str
    started_at: datetime
    last_activity_at: datetime
    message_count: int
    status: Literal["OPEN", "CLOSED", "ABANDONED"]


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class MessageRecord(BaseModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    language: Optional[str]
    citations: list[CitationRef] = Field(default_factory=list)
    trace_id: Optional[str] = None
    created_at: datetime


class SessionDetailResponse(BaseModel):
    session: SessionSummary
    messages: list[MessageRecord]


# ---- /api/feedback ----------------------------------------------------------


class FeedbackRequest(BaseModel):
    session_id: str
    message_id: Optional[str] = None
    thumbs: Optional[Literal["UP", "DOWN"]] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    reason_codes: list[str] = Field(default_factory=list)
    free_text: Optional[str] = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    feedback_id: str


# ---- /api/health ------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    agent_version: str
    region: str
    upstream: dict[str, str]
