"""Agent tools. Each tool:
  - has a typed Pydantic input model;
  - returns a JSON-serializable dict;
  - applies a per-call timeout and the standard error envelope;
  - emits an MLflow span with structured tags.

Every tool that hits Databricks does so with the adjuster's OBO token.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

import backoff
import httpx
import mlflow
import structlog
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    ExecuteStatementRequestOnWaitTimeout,
    StatementParameterListItem,
)
from databricks.vector_search.client import VectorSearchClient
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from backend.agent.config import Settings, get_settings
from backend.schemas import UserContext

log = structlog.get_logger()


# ============================================================================
# Common infrastructure
# ============================================================================

class ToolError(RuntimeError):
    def __init__(self, code: str, message: str, retriable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retriable = retriable

    def envelope(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "retriable": self.retriable}}


def _error(code: str, message: str, retriable: bool = False) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "retriable": retriable}}


def bind_sql(sql: str, values: list[Any]) -> tuple[str, list[StatementParameterListItem]]:
    # databricks-sdk 0.40 needs named placeholders + typed items; raw `?` + dicts crash.
    parts = sql.split("?")
    if len(parts) - 1 != len(values):
        raise ValueError(
            f"placeholder/value count mismatch: {len(parts) - 1} placeholders, {len(values)} values"
        )
    out = [parts[0]]
    params: list[StatementParameterListItem] = []
    for i, v in enumerate(values):
        out.append(f":p{i}")
        out.append(parts[i + 1])
        params.append(
            StatementParameterListItem(
                name=f"p{i}",
                value=None if v is None else str(v),
            )
        )
    return "".join(out), params


@asynccontextmanager
async def _tool_span(tool_name: str, args: dict[str, Any], user: UserContext):
    """MLflow span with redacted args; preserves the audit graph."""
    with mlflow.start_span(name=f"tool.{tool_name}") as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("user.id", user.user_id)
        # Args are the public side (PII redaction is at the gateway).
        span.set_attribute("tool.args_preview", json.dumps(args, default=str)[:512])
        start = time.perf_counter()
        try:
            yield span
        finally:
            span.set_attribute("tool.latency_ms", int((time.perf_counter() - start) * 1000))


def _ws_client(user: UserContext) -> WorkspaceClient:
    # Use the App's service principal credentials (DATABRICKS_CLIENT_ID /
    # DATABRICKS_CLIENT_SECRET auto-injected by Databricks Apps). User OAuth
    # tokens on this workspace do not include the `sql` scope; the SP does
    # via its app.yaml `serving_endpoint` / `sql_warehouse` / `vector_search`
    # resources. The user's identity is still audited via UserContext fields.
    return WorkspaceClient()


def _vs_client(user: UserContext) -> VectorSearchClient:
    # Same rationale as _ws_client — use the App SP, not the user's OBO.
    return VectorSearchClient(disable_notice=True)


@backoff.on_exception(
    backoff.expo,
    (httpx.HTTPError, asyncio.TimeoutError),
    max_tries=3,
    jitter=backoff.full_jitter,
    base=0.25,
    max_value=1.0,
)
async def _exec_uc_function(
    user: UserContext,
    settings: Settings,
    func_name: str,
    args: list[Any],
) -> dict[str, Any]:
    """Call a UC SQL function via the SQL Statement Execution API."""
    placeholders = ",".join("?" for _ in args)
    qualified = settings.tool_function(func_name)
    statement, params = bind_sql(
        f"SELECT {qualified}({placeholders}) AS result", list(args)
    )

    ws = _ws_client(user)
    # The SDK's execute_statement is a *blocking* HTTP call. Running it directly
    # inside this coroutine would pin the asyncio event loop for up to
    # `wait_timeout` (and the surrounding asyncio.wait_for could not actually
    # cancel it) — under a SQL-warehouse stall the whole loop wedges and queue
    # depth grows. Offload to a worker thread so the loop stays responsive and
    # the caller's asyncio.wait_for can return on time.
    resp = await asyncio.to_thread(
        ws.statement_execution.execute_statement,
        statement=statement,
        warehouse_id=settings.databricks_warehouse_id,
        parameters=params,
        wait_timeout="30s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    if resp.status and resp.status.error:
        raise ToolError(
            code=f"UC_FN_{resp.status.error.error_code}",
            message=str(resp.status.error.message),
            retriable=False,
        )
    rows = (resp.result.data_array or []) if resp.result else []
    if not rows:
        return {}
    raw = rows[0][0]
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


# ============================================================================
# Input models
# ============================================================================

class _IdsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetPolicyTermsIn(_IdsBase):
    policy_id: str = Field(..., description="UC policy_id (UUID).")


class GetClaimIn(_IdsBase):
    claim_id: str


class GetClaimEventsIn(_IdsBase):
    claim_id: str
    lookback_days: int = Field(default=90, ge=1, le=730)


class GetClaimHistoryIn(_IdsBase):
    customer_id: str
    lookback_days: int = Field(default=730, ge=1, le=1825)


class GetDeviceIn(_IdsBase):
    device_id: str


class GetRepairOrderIn(_IdsBase):
    repair_order_id: str


class ComputeExcessIn(_IdsBase):
    claim_id: str


class EstimateRepairCostIn(_IdsBase):
    device_id: str
    repair_type: Literal["SCREEN_REPLACE", "BOARD_REPLACE", "BATTERY", "FULL_SWAP", "OTHER"]
    country: str = Field(..., min_length=2, max_length=2)


class CheckPartnerSlaIn(_IdsBase):
    partner_id: str
    claim_id: str


class SearchPolicyWordingsIn(_IdsBase):
    query: str = Field(..., min_length=2, max_length=400)
    language: Literal["en", "es", "ja"] = "en"
    coverage_type: Optional[str] = None
    wording_version: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=10)


class SearchAdjusterKbIn(_IdsBase):
    query: str = Field(..., min_length=2, max_length=400)
    language: Literal["en", "es", "ja"] = "en"
    top_k: int = Field(default=5, ge=1, le=10)


class SearchSimilarClaimsIn(_IdsBase):
    query: str = Field(..., min_length=2, max_length=400)
    language: Literal["en", "es", "ja"] = "en"
    coverage_type: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=10)


class QueryGenieSpaceIn(_IdsBase):
    question: str = Field(..., min_length=3, max_length=600,
                          description="Natural-language analytical question over the claims data.")
    space_id: Optional[str] = Field(default=None, description="Genie space id; defaults to CC_GENIE_SPACE_ID.")


class TranslateIn(_IdsBase):
    text: str = Field(..., min_length=1, max_length=4000)
    source_lang: str = "auto"
    target_lang: Literal["en", "es", "ja"]


class DraftCustomerCommIn(_IdsBase):
    claim_id: str
    decision: Literal["APPROVE", "PARTIAL_APPROVE", "DENY", "REQUEST_DOCS", "UPDATE_STATUS"]
    language: Literal["en", "es", "ja"]
    tone: Literal["empathetic", "neutral", "formal"] = "empathetic"
    extra_notes: Optional[str] = Field(default=None, max_length=600)


class LogDecisionRationaleIn(_IdsBase):
    claim_id: str
    session_id: str
    payload: dict[str, Any]


class EscalateToHumanIn(_IdsBase):
    claim_id: str
    reason: Literal[
        "vulnerability_signal", "siu_referral", "legal_question",
        "regulator_request", "model_uncertain", "data_conflict", "other",
    ]
    note: Optional[str] = Field(default=None, max_length=600)


# ============================================================================
# Tool implementations (read tools)
# ============================================================================

async def get_policy_terms(args: GetPolicyTermsIn, user: UserContext) -> dict[str, Any]:
    """Return canonical coverage terms for a policy. Read-only, deterministic."""
    settings = get_settings()
    async with _tool_span("get_policy_terms", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(user, settings, "get_policy_terms", [args.policy_id]),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "get_policy_terms timed out", retriable=True)


async def get_claim(args: GetClaimIn, user: UserContext) -> dict[str, Any]:
    """Return the canonical claim header for claim_id."""
    settings = get_settings()
    async with _tool_span("get_claim", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(user, settings, "get_claim", [args.claim_id]),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "get_claim timed out", retriable=True)


async def get_claim_events(args: GetClaimEventsIn, user: UserContext) -> dict[str, Any]:
    """Return claim event log within `lookback_days`."""
    settings = get_settings()
    async with _tool_span("get_claim_events", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(
                    user, settings, "get_claim_events",
                    [args.claim_id, args.lookback_days],
                ),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "get_claim_events timed out", retriable=True)


async def get_claim_history(args: GetClaimHistoryIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("get_claim_history", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(
                    user, settings, "get_claim_history",
                    [args.customer_id, args.lookback_days],
                ),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "get_claim_history timed out", retriable=True)


async def get_device(args: GetDeviceIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("get_device", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(user, settings, "get_device", [args.device_id]),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "get_device timed out", retriable=True)


async def get_repair_order(args: GetRepairOrderIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("get_repair_order", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(user, settings, "get_repair_order", [args.repair_order_id]),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "get_repair_order timed out", retriable=True)


async def compute_excess(args: ComputeExcessIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("compute_excess", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(user, settings, "compute_excess", [args.claim_id]),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "compute_excess timed out", retriable=True)


async def estimate_repair_cost(args: EstimateRepairCostIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("estimate_repair_cost", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(
                    user, settings, "estimate_repair_cost",
                    [args.device_id, args.repair_type, args.country],
                ),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "estimate_repair_cost timed out", retriable=True)


async def check_partner_sla(args: CheckPartnerSlaIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("check_partner_sla", args.model_dump(), user):
        try:
            return await asyncio.wait_for(
                _exec_uc_function(
                    user, settings, "check_partner_sla",
                    [args.partner_id, args.claim_id],
                ),
                timeout=settings.tool_timeout_s,
            )
        except ToolError as e:
            return e.envelope()
        except asyncio.TimeoutError:
            return _error("TOOL_TIMEOUT", "check_partner_sla timed out", retriable=True)


# ============================================================================
# Vector Search tools
# ============================================================================

def _vs_search(
    user: UserContext,
    index_name: str,
    query: str,
    columns: list[str],
    filters: Optional[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    settings = get_settings()
    vsc = _vs_client(user)
    index = vsc.get_index(endpoint_name=settings.vs_endpoint, index_name=index_name)
    raw = index.similarity_search(
        query_text=query,
        columns=columns,
        filters=filters or {},
        num_results=top_k,
        query_type="HYBRID",
    )
    rows = raw.get("result", {}).get("data_array", [])
    col_names = [c["name"] for c in raw.get("manifest", {}).get("columns", [])]
    return [dict(zip(col_names, row)) for row in rows]


async def search_policy_wordings(args: SearchPolicyWordingsIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("search_policy_wordings", args.model_dump(), user):
        filters: dict[str, Any] = {"language": args.language}
        if args.coverage_type:
            filters["product_code"] = args.coverage_type
        if args.wording_version:
            filters["version"] = args.wording_version
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    _vs_search,
                    user,
                    settings.vs_index_policy_wordings,
                    args.query,
                    ["chunk_id", "wording_doc_id", "version", "section_path",
                     "section_title", "text", "language"],
                    filters,
                    args.top_k,
                ),
                timeout=settings.vs_timeout_s,
            )
            return {
                "results": [
                    {
                        "citation": f"[POLICY §{r['section_path']} / wording {r['version']}]",
                        "ref": f"{r['wording_doc_id']}#sec={r['section_path']}",
                        "section_title": r["section_title"],
                        "text": r["text"],
                        "language": r["language"],
                    }
                    for r in rows
                ]
            }
        except asyncio.TimeoutError:
            return _error("VS_TIMEOUT", "policy wording search timed out", retriable=True)
        except Exception as e:
            return _error("VS_ERROR", f"policy wording search failed: {e}", retriable=True)


async def search_adjuster_kb(args: SearchAdjusterKbIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("search_adjuster_kb", args.model_dump(), user):
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    _vs_search,
                    user,
                    settings.vs_index_adjuster_kb,
                    args.query,
                    ["chunk_id", "article_id", "section_path", "text", "language"],
                    {"language": args.language},
                    args.top_k,
                ),
                timeout=settings.vs_timeout_s,
            )
            return {
                "results": [
                    {
                        "citation": f"[KB-{r['article_id']}]",
                        "ref": r["chunk_id"],
                        "section_path": r["section_path"],
                        "text": r["text"],
                        "language": r["language"],
                    }
                    for r in rows
                ]
            }
        except asyncio.TimeoutError:
            return _error("VS_TIMEOUT", "KB search timed out", retriable=True)
        except Exception as e:
            return _error("VS_ERROR", f"KB search failed: {e}", retriable=True)


async def search_similar_claims(args: SearchSimilarClaimsIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("search_similar_claims", args.model_dump(), user):
        filters: dict[str, Any] = {"language": args.language}
        if args.coverage_type:
            filters["claim_type"] = args.coverage_type
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    _vs_search,
                    user,
                    settings.vs_index_claim_narratives,
                    args.query,
                    ["claim_id_anon", "decision", "paid_band", "claim_type",
                     "product_code", "language", "narrative_anon"],
                    filters,
                    args.top_k,
                ),
                timeout=settings.vs_timeout_s,
            )
            return {
                "results": [
                    {
                        "citation": f"[CLAIM-{r['claim_id_anon'][:4]} similar]",
                        "ref": r["claim_id_anon"],
                        "decision": r["decision"],
                        "paid_band": r["paid_band"],
                        "narrative": r["narrative_anon"],
                    }
                    for r in rows
                ]
            }
        except asyncio.TimeoutError:
            return _error("VS_TIMEOUT", "similar-claim search timed out", retriable=True)
        except Exception as e:
            return _error("VS_ERROR", f"similar-claim search failed: {e}", retriable=True)


# ============================================================================
# Genie — natural-language analytics (NL->SQL) over the claims data
# ============================================================================

def _genie_ask(user: UserContext, space_id: str, question: str) -> dict[str, Any]:
    """Blocking: ask a Genie space a question, return the generated SQL +
    a result preview. Run via asyncio.to_thread (the SDK calls are blocking)."""
    ws = _ws_client(user)
    msg = ws.genie.start_conversation_and_wait(space_id=space_id, content=question)
    sql: Optional[str] = None
    summary: Optional[str] = None
    for att in (msg.attachments or []):
        q = getattr(att, "query", None)
        if q is not None:
            sql = getattr(q, "query", None) or sql
            summary = summary or getattr(q, "description", None)
        t = getattr(att, "text", None)
        if t is not None and getattr(t, "content", None):
            summary = t.content
    columns: list[str] = []
    rows: list[list[Any]] = []
    if sql:
        res = ws.genie.get_message_query_result(space_id, msg.conversation_id, msg.id)
        sr = getattr(res, "statement_response", None)
        if sr and sr.result and sr.result.data_array:
            rows = sr.result.data_array[:20]  # preview cap
        if sr and sr.manifest and sr.manifest.schema and sr.manifest.schema.columns:
            columns = [c.name for c in sr.manifest.schema.columns]
    return {"summary": summary, "sql": sql, "columns": columns,
            "rows": rows, "row_count": len(rows)}


async def query_genie_space(args: QueryGenieSpaceIn, user: UserContext) -> dict[str, Any]:
    """Ask a Genie space a natural-language analytical question (NL->SQL)."""
    settings = get_settings()
    space_id = args.space_id or settings.genie_space_id
    async with _tool_span("query_genie_space", {"space_id": space_id}, user):
        if not space_id:
            return _error("GENIE_NOT_CONFIGURED",
                          "No Genie space configured (set CC_GENIE_SPACE_ID).", retriable=False)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_genie_ask, user, space_id, args.question),
                timeout=settings.genie_timeout_s,
            )
        except asyncio.TimeoutError:
            return _error("GENIE_TIMEOUT", "Genie query timed out", retriable=True)
        except Exception as e:  # noqa: BLE001
            return _error("GENIE_ERROR", f"Genie query failed: {e}", retriable=True)


# ============================================================================
# Translation tool (FMAPI via AI Gateway)
# ============================================================================

async def _fmapi_complete(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    """Non-streaming chat completion via the /invocations REST API with endpoint
    fallback. Shared by the translate + draft tools; mirrors agent._llm_complete.

    `temperature` is accepted for signature stability but NOT sent — the primary
    model (databricks-claude-opus-4-8) returns 400 on it.
    """
    sys_parts = [m["content"] for m in messages if m.get("role") == "system" and m.get("content")]
    rest = [m for m in messages if m.get("role") != "system"]
    payload_msgs = ([{"role": "system", "content": "\n\n".join(sys_parts)}] if sys_parts else []) + rest

    cfg = WorkspaceClient().config
    host = cfg.host
    endpoints = settings.chat_endpoint_chain()
    last_detail = "no endpoints tried"
    for endpoint in endpoints:
        url = f"{host}/serving-endpoints/{endpoint}/invocations"
        try:
            async with httpx.AsyncClient(timeout=settings.translate_timeout_s) as client:
                resp = await client.post(
                    url,
                    json={"messages": payload_msgs, "max_tokens": max_tokens},
                    headers=cfg.authenticate(),
                )
            if resp.status_code >= 400:
                last_detail = f"{resp.status_code} @ {endpoint}: {resp.text[:400]}"
                log.warning("fmapi_tool_http_error", endpoint=endpoint,
                            status=resp.status_code, detail=resp.text[:400])
                continue
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last_detail = f"{endpoint}: {e}"
            log.warning("fmapi_tool_endpoint_failed", endpoint=endpoint, error=str(e)[:400])
            continue
    raise ToolError("FMAPI_ERROR", f"all chat endpoints failed: {last_detail}", retriable=True)


async def translate(args: TranslateIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("translate", {"source": args.source_lang, "target": args.target_lang}, user):
        try:
            text = await _fmapi_complete(
                settings,
                [
                    {"role": "system", "content": (
                        f"You are a translator. Translate the user content to {args.target_lang}. "
                        "Preserve numbers, brand names, IDs verbatim. Return only the translation."
                    )},
                    {"role": "user", "content": args.text},
                ],
                temperature=0.0, max_tokens=1024,
            )
            return {
                "text": text.strip(),
                "detected_source_lang": args.source_lang,
                "model_version": settings.chat_endpoint_primary,
            }
        except ToolError as e:
            return e.envelope()
        except Exception as e:  # noqa: BLE001
            return _error("TRANSLATE_ERROR", f"translate failed: {e}", retriable=True)


# ============================================================================
# Customer-comm drafter (Python tool that calls FMAPI with a template)
# ============================================================================

_DRAFT_SYSTEM = """\
You draft customer-facing messages on behalf of a claims adjuster at the Company.
Tone: {tone}. Target language: {language}. Claim decision class: {decision}.

Rules:
- Plain, clear, no marketing.
- Refer to the customer by their honorific and surname if available; otherwise use a neutral greeting.
- Never state amounts unless they are explicitly given in extra_notes.
- Never reveal internal terms (excess, peril, subrogation) without a short customer-friendly gloss.
- Ends with a clear next step.

Output strict JSON:
{{"subject":"...","body":"...","channel_hint":"EMAIL|SMS|IN_APP"}}
"""


async def draft_customer_comm(args: DraftCustomerCommIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("draft_customer_comm", {"decision": args.decision, "lang": args.language}, user):
        try:
            text = await _fmapi_complete(
                settings,
                [
                    {"role": "system", "content": _DRAFT_SYSTEM.format(
                        tone=args.tone, language=args.language, decision=args.decision,
                    )},
                    {"role": "user", "content": json.dumps({
                        "claim_id": args.claim_id, "extra_notes": args.extra_notes or "",
                    })},
                ],
                temperature=0.2, max_tokens=600,
            )
            parsed = json.loads(text)
            parsed["model_version"] = settings.chat_endpoint_primary
            return parsed
        except ToolError as e:
            return e.envelope()
        except json.JSONDecodeError:
            return _error("DRAFT_PARSE", "draft was not valid JSON", retriable=True)
        except Exception as e:  # noqa: BLE001
            return _error("DRAFT_ERROR", f"draft_customer_comm failed: {e}", retriable=True)


# ============================================================================
# Write tools (gated by confirmation in supervisor)
# ============================================================================

async def log_decision_rationale(args: LogDecisionRationaleIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("log_decision_rationale", {"claim_id": args.claim_id}, user):
        decision_log_id = str(uuid.uuid4())
        payload_json = json.dumps(args.payload, default=str)
        ws = _ws_client(user)
        statement = (
            f"INSERT INTO {settings.decision_log_table} "
            "(decision_log_id, claim_id, session_id, adjuster_id, agent_recommendation, "
            " agent_reasoning_md, cited_clauses, cited_kb, cited_claims_anon, "
            " adjuster_concurred, adjuster_final_decision, adjuster_override_reason, "
            " model, agent_version, trace_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, CURRENT_TIMESTAMP())"
        )
        try:
            p = args.payload
            statement, params = bind_sql(statement, [
                decision_log_id,
                args.claim_id,
                args.session_id,
                user.user_id,
                p.get("recommendation", "UNDETERMINED"),
                p.get("reasoning_md", ""),
                json.dumps(p.get("cited_clauses", [])),
                json.dumps(p.get("cited_kb", [])),
                json.dumps(p.get("cited_claims_anon", [])),
                p.get("model", settings.chat_endpoint_primary),
                settings.agent_version,
                p.get("trace_id", ""),
            ])
            # Offload the blocking SDK call so it can't wedge the event loop.
            await asyncio.to_thread(
                ws.statement_execution.execute_statement,
                statement=statement,
                warehouse_id=settings.databricks_warehouse_id,
                parameters=params,
                wait_timeout="30s",
            )
            return {"decision_log_id": decision_log_id}
        except Exception as e:
            return _error("WRITE_ERROR", f"log_decision_rationale failed: {e}", retriable=False)


async def escalate_to_human(args: EscalateToHumanIn, user: UserContext) -> dict[str, Any]:
    settings = get_settings()
    async with _tool_span("escalate_to_human", args.model_dump(), user):
        escalation_id = str(uuid.uuid4())
        queue = {
            "vulnerability_signal": "VULN_CARE",
            "siu_referral": "SIU",
            "legal_question": "LEGAL",
            "regulator_request": "COMPLAINTS",
            "model_uncertain": "L3_REVIEW",
            "data_conflict": "L3_REVIEW",
            "other": "L3_REVIEW",
        }[args.reason]
        ws = _ws_client(user)
        statement = (
            f"INSERT INTO {settings.catalog_ai}.{settings.schema_app}.escalation "
            "(escalation_id, claim_id, reason, queue, note, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP())"
        )
        try:
            statement, params = bind_sql(statement, [
                escalation_id,
                args.claim_id,
                args.reason,
                queue,
                args.note or "",
                user.user_id,
            ])
            # Offload the blocking SDK call so it can't wedge the event loop.
            await asyncio.to_thread(
                ws.statement_execution.execute_statement,
                statement=statement,
                warehouse_id=settings.databricks_warehouse_id,
                parameters=params,
                wait_timeout="30s",
            )
            return {"escalation_id": escalation_id, "queue": queue, "eta_minutes": 30}
        except Exception as e:
            return _error("WRITE_ERROR", f"escalate failed: {e}", retriable=False)


# ============================================================================
# Registry — single source of truth
# ============================================================================

READ_TOOLS = {
    "get_policy_terms": (get_policy_terms, GetPolicyTermsIn,
                         "Return canonical coverage terms for a policy."),
    "get_claim": (get_claim, GetClaimIn,
                  "Return canonical claim header (policy, device, incident, status, fraud_score, etc.)."),
    "get_claim_events": (get_claim_events, GetClaimEventsIn,
                         "Return claim event log within lookback_days."),
    "get_claim_history": (get_claim_history, GetClaimHistoryIn,
                          "Return all claims for a customer within lookback_days."),
    "get_device": (get_device, GetDeviceIn,
                   "Return device master record (make, model, replacement cost, warranty)."),
    "get_repair_order": (get_repair_order, GetRepairOrderIn,
                         "Return repair order (vendor, parts, labor, amounts)."),
    "compute_excess": (compute_excess, ComputeExcessIn,
                       "Compute deterministic excess due on the claim."),
    "estimate_repair_cost": (estimate_repair_cost, EstimateRepairCostIn,
                             "Return p25/p50/p75 of repair cost for device+type+country."),
    "check_partner_sla": (check_partner_sla, CheckPartnerSlaIn,
                          "Return SLA status of this claim against partner contract."),
    "search_policy_wordings": (search_policy_wordings, SearchPolicyWordingsIn,
                               "Semantic+keyword search over policy wording chunks."),
    "search_adjuster_kb": (search_adjuster_kb, SearchAdjusterKbIn,
                           "Semantic+keyword search over adjuster KB articles."),
    "search_similar_claims": (search_similar_claims, SearchSimilarClaimsIn,
                              "Semantic search over anonymized historic claim narratives."),
    "query_genie_space": (query_genie_space, QueryGenieSpaceIn,
                          "Ask a Genie space a natural-language ANALYTICAL question over the claims "
                          "data (NL->SQL) — aggregates, trends, comparisons not answerable by the "
                          "specific get_*/compute_* tools. Returns the generated SQL + a result preview."),
    "translate": (translate, TranslateIn,
                  "Translate text via FMAPI behind the AI Gateway."),
    "draft_customer_comm": (draft_customer_comm, DraftCustomerCommIn,
                            "Draft a customer-facing message in the specified language. NOT sent."),
}

WRITE_TOOLS = {
    "log_decision_rationale": (log_decision_rationale, LogDecisionRationaleIn,
                               "Persist agent's reasoning + recommended action. WRITE tool."),
    "escalate_to_human": (escalate_to_human, EscalateToHumanIn,
                          "Route claim to a human queue. WRITE tool."),
}

ALL_TOOLS = {**READ_TOOLS, **WRITE_TOOLS}


def get_tool_specs_for_planner() -> list[dict[str, Any]]:
    """Compact tool list emitted into the planner prompt. The Genie tool is
    offered only when a space is configured (CC_GENIE_SPACE_ID)."""
    settings = get_settings()
    specs: list[dict[str, Any]] = []
    for name, (_, cls, doc) in ALL_TOOLS.items():
        if name == "query_genie_space" and not settings.genie_space_id:
            continue
        specs.append({
            "tool": name,
            "input_schema": cls.model_json_schema(),
            "doc": doc,
            "write": name in WRITE_TOOLS,
        })
    return specs


def make_structured_tools(user: UserContext) -> list[StructuredTool]:
    """LangChain wrappers; useful when binding tools to a ChatModel."""
    out: list[StructuredTool] = []
    for name, (impl, schema, doc) in ALL_TOOLS.items():
        async def _runner(_impl=impl, _schema=schema, **kwargs):
            return await _impl(_schema(**kwargs), user)
        out.append(StructuredTool.from_function(
            coroutine=_runner, name=name, description=doc, args_schema=schema,
        ))
    return out
