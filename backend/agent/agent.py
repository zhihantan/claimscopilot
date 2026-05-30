"""LangGraph supervisor + ReAct worker for ClaimsCopilot.

Exposes:
  - ClaimsCopilotAgent.run_stream(user, request, session, ...)  -> async generator of SSE events
  - build_agent()  factory (mostly for eval; the runtime instantiates per-request)

Streaming model:
  The agent emits typed dicts (see backend.schemas) that the FastAPI layer
  converts to SSE frames. Streaming covers the SYNTHESIZE node only; PLAN and
  REFLECT are short, non-streamed JSON calls.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Optional

import httpx
import mlflow
import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from backend.agent import tools as tools_mod
from backend.agent.config import Settings, get_settings
from backend.agent.prompts import (
    PLAN_SYSTEM_PROMPT,
    REFLECT_SYSTEM_PROMPT,
    REFUSAL_AUTO_DECISION,
    REFUSAL_LEGAL_ADVICE,
    REFUSAL_UNSUPPORTED_LANG,
    REFUSAL_VULNERABILITY,
    SYNTHESIZE_FEWSHOTS,
    SYNTHESIZE_SYSTEM_PROMPT,
)
from backend.schemas import (
    CitationRef,
    SSECitation,
    SSEDone,
    SSEError,
    SSEPlan,
    SSEReflect,
    SSESessionStart,
    SSEToken,
    SSEToolEnd,
    SSEToolStart,
    PlanItem,
    UserContext,
)

log = structlog.get_logger()


_DECISION_TAG_RE = re.compile(
    r'<decision\s+class="(APPROVE|PARTIAL_APPROVE|DENY|REQUEST_DOCS|UPDATE_STATUS|UNDETERMINED)"\s+'
    r'confidence="(LOW|MED|HIGH)"\s*/>',
)
_CITATION_POLICY_RE = re.compile(r"\[POLICY\s+§([\w./-]+)\s+/\s+wording\s+([\w.-]+)\]")
_CITATION_KB_RE = re.compile(r"\[KB-([\w-]+)\]")
_CITATION_CLAIM_RE = re.compile(r"\[CLAIM-([\w-]+)\s+similar\]")


# ---- Graph state ------------------------------------------------------------

class AgentState(TypedDict, total=False):
    user: UserContext
    session_id: str
    claim_id: Optional[str]
    language: str
    user_message: str
    confirmations: list[str]

    # working memory
    claim_summary: dict[str, Any]
    plan: list[PlanItem]
    tool_results: list[dict[str, Any]]
    reflect_cycles: int

    # outputs
    final_text: str
    decision_class: str
    confidence: str
    citations: list[CitationRef]
    cost_usd: float
    fallback_step: int
    trace_id: str
    fatal_error: Optional[str]


# ---- Vulnerability classifier (small, fast, deterministic) ------------------

_VULNERABILITY_TRIGGERS = {
    # Patterns are anchored at a word boundary on the LEFT only. We
    # intentionally omit a trailing `\b` so stem-form triggers (e.g.
    # `suicid`, `bereav`, `fallec`) match all conjugations and derivatives
    # (`suicidal`, `suicidio`, `falleció`, `fallecimiento`).
    "en": [
        r"\b(suicid|self[\s-]?harm|kill myself)",
        r"\b(bereav|deceased|passed away|funeral)",
        r"\b(ombuds(man|person)|f\.?o\.?s\.?|regulator|sue you)",
        r"\b(can'?t (pay|afford)|hardship|homeless)",
        r"\b(scared|afraid|domestic (abuse|violence))",
    ],
    "es": [
        r"\b(suicid|hacerme daño|matar(me| mi))",
        r"\b(fallec|funeral|duelo)",
        r"\b(defensor del pueblo|regulador|demand(ar|are))",
        r"\b(no puedo pagar|dificultad económica)",
    ],
    "ja": [
        r"(自殺|自傷|自分を傷)",
        r"(死別|逝去|葬儀)",
        r"(オンブズマン|規制当局|訴える)",
        r"(支払えない|生活が苦しい|困窮)",
    ],
}


def detect_vulnerability(text: str, language: str) -> bool:
    pats = _VULNERABILITY_TRIGGERS.get(language, _VULNERABILITY_TRIGGERS["en"])
    return any(re.search(p, text, flags=re.IGNORECASE) for p in pats)


# ---- LLM invocation (chat completions via AI Gateway) -----------------------

@dataclass
class LLMResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    fallback_step: int
    cost_usd: float


def _consolidate_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse multiple `system` messages into one leading system message.

    Databricks' Claude FMAPI maps OpenAI system-role messages onto Anthropic's
    single top-level `system` field and returns 400 Bad Request when more than
    one system message is present (the synthesize prompt used four). Merge them
    in order into a single leading system message and keep the rest unchanged.
    """
    system_parts = [
        m["content"] for m in messages
        if m.get("role") == "system" and m.get("content")
    ]
    rest = [m for m in messages if m.get("role") != "system"]
    out: list[dict[str, str]] = []
    if system_parts:
        out.append({"role": "system", "content": "\n\n".join(system_parts)})
    out.extend(rest)
    return out


async def _llm_complete(
    user: UserContext,
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    json_mode: bool = False,
    stream: bool = False,
) -> AsyncIterator[str] | LLMResult:
    """Call a Databricks chat serving endpoint via the /invocations REST API,
    with a model fallback chain for BOTH non-streaming and streaming.

    IMPORTANT: `temperature` is intentionally NOT sent. The primary model
    `databricks-claude-opus-4-8` is a thinking model that returns
    `400 BAD_REQUEST: Model ... does not support the temperature parameter`;
    omitting it works for every configured endpoint (temperature is optional on
    the others). The `temperature` arg is kept for signature stability.
    """
    from databricks.sdk import WorkspaceClient as _SpWs

    endpoints = settings.chat_endpoint_chain()
    sp_cfg = _SpWs().config
    host = sp_cfg.host  # SDK-normalized, includes https://

    # Merge multiple system messages into one (harmless; some endpoints 400 on >1).
    messages = _consolidate_messages(messages)

    # json_mode: FMAPI has no OpenAI `response_format`, so ask for raw JSON in the
    # (single) system message.
    if json_mode and messages:
        directive = "\n\nReply with ONLY a single JSON object — no prose, no markdown fences."
        if messages[0].get("role") == "system":
            messages = [
                {**messages[0], "content": messages[0]["content"] + directive},
                *messages[1:],
            ]
        else:
            messages = [{"role": "system", "content": directive.strip()}, *messages]

    last_exc: Exception | None = None
    for step, endpoint in enumerate(endpoints):
        url = f"{host}/serving-endpoints/{endpoint}/invocations"
        body: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, "stream": stream}
        headers = sp_cfg.authenticate()
        try:
            if not stream:
                async with httpx.AsyncClient(timeout=settings.turn_hard_timeout_s) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    if resp.status_code >= 400:
                        last_exc = RuntimeError(f"FMAPI {resp.status_code} @ {endpoint}: {resp.text[:800]}")
                        log.warning("fmapi_http_error", endpoint=endpoint, step=step,
                                    status=resp.status_code, detail=resp.text[:800])
                        continue
                    data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                pt = int(usage.get("prompt_tokens") or 0)
                ct = int(usage.get("completion_tokens") or 0) or max(1, len(content) // 4)
                return LLMResult(
                    content=content, model=endpoint, prompt_tokens=pt, completion_tokens=ct,
                    fallback_step=step, cost_usd=_estimate_cost_usd(pt, ct, settings),
                )

            # Streaming: open the connection HERE and check status so a 4xx falls
            # through to the next endpoint before we hand the generator back.
            client = httpx.AsyncClient(timeout=settings.turn_hard_timeout_s)
            cm = client.stream("POST", url, json=body, headers=headers)
            resp = await cm.__aenter__()
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", "replace")[:800]
                await cm.__aexit__(None, None, None)
                await client.aclose()
                last_exc = RuntimeError(f"FMAPI {resp.status_code} @ {endpoint}: {detail}")
                log.warning("fmapi_http_error", endpoint=endpoint, step=step,
                            status=resp.status_code, detail=detail)
                continue

            async def _gen(_cm=cm, _client=client, _resp=resp) -> AsyncIterator[str]:
                try:
                    async for line in _resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                finally:
                    await _cm.__aexit__(None, None, None)
                    await _client.aclose()

            return _gen()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            log.warning("llm_endpoint_failed", endpoint=endpoint, step=step, error=str(e)[:800])
            continue
    raise RuntimeError(f"All LLM endpoints failed: {last_exc}")


def _estimate_cost_usd(prompt_tokens: int, completion_tokens: int, settings: Settings) -> float:
    return round(
        prompt_tokens / 1_000_000 * settings.price_in_usd_per_mtok_primary
        + completion_tokens / 1_000_000 * settings.price_out_usd_per_mtok_primary,
        6,
    )


# ---- Node: load claim summary ----------------------------------------------

async def node_load_claim(state: AgentState) -> AgentState:
    if not state.get("claim_id"):
        state["claim_summary"] = {}
        return state
    user = state["user"]
    out = await tools_mod.get_claim(
        tools_mod.GetClaimIn(claim_id=state["claim_id"]), user
    )
    state["claim_summary"] = out
    return state


# ---- Node: vulnerability gate ----------------------------------------------

async def node_vulnerability_gate(state: AgentState) -> AgentState:
    text_blobs = [state.get("user_message", "")]
    cs = state.get("claim_summary") or {}
    if isinstance(cs, dict):
        for field_ in ("incident_description_raw", "incident_description_en"):
            if cs.get(field_):
                text_blobs.append(str(cs[field_]))
    lang = state.get("language", "en")
    flag = any(detect_vulnerability(t, lang) for t in text_blobs if t)
    if flag:
        await tools_mod.escalate_to_human(
            tools_mod.EscalateToHumanIn(
                claim_id=state["claim_id"] or "unknown",
                reason="vulnerability_signal",
                note="Auto-detected by agent vulnerability gate",
            ),
            state["user"],
        )
        state["final_text"] = REFUSAL_VULNERABILITY.get(lang, REFUSAL_VULNERABILITY["en"])
        state["decision_class"] = "UNDETERMINED"
        state["confidence"] = "LOW"
        state["fatal_error"] = "VULNERABILITY_REFUSAL"
    return state


# ---- Node: plan -------------------------------------------------------------

async def node_plan(state: AgentState) -> AgentState:
    settings = get_settings()
    if state.get("fatal_error"):
        return state
    user = state["user"]
    tool_specs = tools_mod.get_tool_specs_for_planner()
    sys = PLAN_SYSTEM_PROMPT
    usr = json.dumps({
        "tools": tool_specs,
        "claim_id": state.get("claim_id"),
        "claim_summary": state.get("claim_summary"),
        "language": state["language"],
        "adjuster_question": state["user_message"],
    }, default=str)
    result = await _llm_complete(
        user, settings,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        temperature=0.1, max_tokens=512, json_mode=True,
    )
    state["cost_usd"] = state.get("cost_usd", 0.0) + result.cost_usd
    state["fallback_step"] = max(state.get("fallback_step", 0), result.fallback_step)
    try:
        parsed = json.loads(result.content)
        plan_items = [PlanItem(**p) for p in parsed.get("plan", [])]
    except Exception as e:  # noqa: BLE001
        log.warning("plan_parse_failed", error=str(e))
        plan_items = []
    # Reject unknown tool names and write tools without confirmation
    confirmed = set(state.get("confirmations") or [])
    safe_plan: list[PlanItem] = []
    for item in plan_items:
        if item.tool not in tools_mod.ALL_TOOLS:
            log.warning("unknown_tool_proposed", tool=item.tool)
            continue
        if item.tool in tools_mod.WRITE_TOOLS and item.tool not in confirmed:
            log.info("write_tool_blocked_no_confirm", tool=item.tool)
            continue
        safe_plan.append(item)
    state["plan"] = safe_plan
    return state


# ---- Node: execute tools ----------------------------------------------------

async def node_execute(state: AgentState) -> AgentState:
    if state.get("fatal_error"):
        return state
    user = state["user"]
    settings = get_settings()
    plan = state.get("plan", [])
    results: list[dict[str, Any]] = state.get("tool_results", []) or []
    # Execute only the NOT-yet-run tail of the plan, bounded by the per-turn
    # budget. Each executed item appends exactly one result, so len(results) is
    # the count already executed — slicing from there avoids re-running the head
    # when reflect appends more tools across cycles.
    already = len(results)
    remaining = settings.max_tool_calls_per_turn - already
    if remaining <= 0:
        state["tool_results"] = results
        return state
    for item in plan[already: already + remaining]:
        impl, schema, _ = tools_mod.ALL_TOOLS[item.tool]
        try:
            args_obj = schema(**item.args)
        except Exception as e:  # noqa: BLE001
            results.append({
                "tool": item.tool, "args": item.args,
                "result": {"error": {"code": "ARG_VALIDATION", "message": str(e), "retriable": False}},
                "call_id": str(uuid.uuid4()),
            })
            continue
        call_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        try:
            res = await impl(args_obj, user)
        except Exception as e:  # noqa: BLE001
            res = {"error": {"code": "TOOL_EXCEPTION", "message": str(e), "retriable": False}}
        results.append({
            "tool": item.tool,
            "args": item.args,
            "result": res,
            "call_id": call_id,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        })
    state["tool_results"] = results
    return state


# ---- Node: reflect ----------------------------------------------------------

async def node_reflect(state: AgentState) -> AgentState:
    settings = get_settings()
    if state.get("fatal_error"):
        return state
    user = state["user"]
    state["reflect_cycles"] = state.get("reflect_cycles", 0) + 1
    if state["reflect_cycles"] > settings.max_reflect_cycles:
        return state
    if len(state.get("tool_results", [])) >= settings.max_tool_calls_per_turn:
        return state
    sys = REFLECT_SYSTEM_PROMPT
    usr = json.dumps({
        "adjuster_question": state["user_message"],
        "executed_plan": [pi.model_dump() for pi in state.get("plan", [])],
        "tool_results": _redact_results(state.get("tool_results", [])),
    }, default=str)
    result = await _llm_complete(
        user, settings,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        temperature=0.0, max_tokens=256, json_mode=True,
    )
    state["cost_usd"] = state.get("cost_usd", 0.0) + result.cost_usd
    state["fallback_step"] = max(state.get("fallback_step", 0), result.fallback_step)
    try:
        parsed = json.loads(result.content)
    except Exception:  # noqa: BLE001
        parsed = {"decision": "done", "note": "reflect parse failed", "extra_plan": []}
    if parsed.get("decision") == "more":
        extras = [PlanItem(**p) for p in parsed.get("extra_plan", []) or []]
        confirmed = set(state.get("confirmations") or [])
        for item in extras:
            if item.tool not in tools_mod.ALL_TOOLS:
                continue
            if item.tool in tools_mod.WRITE_TOOLS and item.tool not in confirmed:
                continue
            state["plan"].append(item)
    return state


def _should_continue(state: AgentState) -> str:
    if state.get("fatal_error"):
        return "synthesize"
    settings = get_settings()
    if state.get("reflect_cycles", 0) >= settings.max_reflect_cycles:
        return "synthesize"
    if len(state.get("tool_results", [])) >= settings.max_tool_calls_per_turn:
        return "synthesize"
    # Re-execute only if new items were appended past the executed count
    executed = len(state.get("tool_results", []))
    if len(state.get("plan", [])) > executed:
        return "execute"
    return "synthesize"


# ---- Node: synthesize (streaming) ------------------------------------------

async def stream_synthesize(state: AgentState) -> AsyncIterator[dict]:
    """Generator that yields raw SSE event dicts."""
    settings = get_settings()
    user = state["user"]
    if state.get("fatal_error"):
        # Pass through the refusal text as a single 'token' event.
        for chunk in _chunks(state["final_text"], 40):
            yield SSEToken(delta=chunk).model_dump()
        return

    sys_prompt = SYNTHESIZE_SYSTEM_PROMPT.replace("{canary}", settings.system_canary)
    fewshot = "\n\n".join(
        f"### EXAMPLE\nADJUSTER: {ex['adjuster_query']}\nASSISTANT:\n{ex['answer']}"
        for ex in SYNTHESIZE_FEWSHOTS
    )
    tool_brief = json.dumps(
        _redact_results(state.get("tool_results", [])),
        default=str,
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "system", "content": f"FEW-SHOT EXAMPLES (style only):\n{fewshot}"},
        {"role": "system", "content": (
            f"ADJUSTER ROLE: {user.role}\nADJUSTER COUNTRY: {user.country}\n"
            f"LANGUAGE: {state['language']}\nCLAIM SUMMARY: {json.dumps(state.get('claim_summary') or {}, default=str)}"
        )},
        {"role": "system", "content": f"TOOL RESULTS (ground truth):\n{tool_brief}"},
        {"role": "user", "content":
            f"<<<UNTRUSTED:adjuster_message lang={state['language']}>>>\n"
            f"{state['user_message']}\n<<<END>>>"},
    ]

    stream_iter = await _llm_complete(
        user, settings,
        messages=messages,
        temperature=0.2, max_tokens=900, stream=True,
    )

    full: list[str] = []
    estimated_prompt_tokens = sum(len(m["content"]) for m in messages) // 4
    async for delta in stream_iter:  # type: ignore[union-attr]
        full.append(delta)
        yield SSEToken(delta=delta).model_dump()

    full_text = "".join(full)
    state["final_text"] = full_text

    # Decision tag
    m = _DECISION_TAG_RE.search(full_text)
    state["decision_class"] = m.group(1) if m else "UNDETERMINED"
    state["confidence"] = m.group(2) if m else "LOW"

    # Citation events (post-stream — keeps token stream simple)
    citations = _extract_citations(full_text, state.get("tool_results", []))
    state["citations"] = citations
    for cite in citations:
        yield SSECitation(citation=cite).model_dump()

    # Cost — completion tokens estimated from text length / 4
    completion_tokens = max(1, len(full_text) // 4)
    state["cost_usd"] = state.get("cost_usd", 0.0) + _estimate_cost_usd(
        estimated_prompt_tokens, completion_tokens, settings,
    )

    # Canary leak check
    if settings.system_canary and settings.system_canary in full_text:
        log.error("canary_leak_detected", session_id=state.get("session_id"))
        state["fatal_error"] = "CANARY_LEAK"


# ---- Citation + redaction helpers ------------------------------------------

def _redact_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cap text fields to keep prompt size under control."""
    out: list[dict[str, Any]] = []
    for r in results:
        rr = dict(r)
        raw = rr.get("result")
        if isinstance(raw, dict) and "results" in raw and isinstance(raw["results"], list):
            for hit in raw["results"]:
                if isinstance(hit.get("text"), str) and len(hit["text"]) > 600:
                    hit["text"] = hit["text"][:600] + "…"
                if isinstance(hit.get("narrative"), str) and len(hit["narrative"]) > 600:
                    hit["narrative"] = hit["narrative"][:600] + "…"
        out.append(rr)
    return out


def _extract_citations(text: str, tool_results: list[dict[str, Any]]) -> list[CitationRef]:
    """Verify each in-text citation against retrieved sources; drop unverified."""
    verified: list[CitationRef] = []
    seen: set[str] = set()
    # Build allowed set from tool results
    allowed_policy_refs = set()
    allowed_kb_refs = set()
    allowed_claim_refs = set()
    for tr in tool_results:
        res = tr.get("result", {})
        for hit in (res.get("results") or []):
            ref = hit.get("ref", "")
            if tr["tool"] == "search_policy_wordings":
                allowed_policy_refs.add(ref)
            elif tr["tool"] == "search_adjuster_kb":
                allowed_kb_refs.add(ref)
            elif tr["tool"] == "search_similar_claims":
                allowed_claim_refs.add(ref)
    for m in _CITATION_POLICY_RE.finditer(text):
        section, version = m.group(1), m.group(2)
        ref_match = next(
            (r for r in allowed_policy_refs if r.endswith(f"#sec={section}") and version in r),
            None,
        )
        if ref_match and ref_match not in seen:
            seen.add(ref_match)
            verified.append(CitationRef(kind="policy", label=m.group(0), ref=ref_match))
    for m in _CITATION_KB_RE.finditer(text):
        article = m.group(1)
        ref_match = next((r for r in allowed_kb_refs if r.startswith(article)), None)
        if ref_match and ref_match not in seen:
            seen.add(ref_match)
            verified.append(CitationRef(kind="kb", label=m.group(0), ref=ref_match))
    for m in _CITATION_CLAIM_RE.finditer(text):
        claim_anon = m.group(1)
        ref_match = next((r for r in allowed_claim_refs if r.startswith(claim_anon)), None)
        if ref_match and ref_match not in seen:
            seen.add(ref_match)
            verified.append(CitationRef(kind="claim", label=m.group(0), ref=ref_match))
    return verified


def _chunks(text: str, n: int):
    for i in range(0, len(text), n):
        yield text[i:i + n]


# ---- Graph ------------------------------------------------------------------

def _build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState)
    g.add_node("load_claim", node_load_claim)
    g.add_node("vuln_gate", node_vulnerability_gate)
    g.add_node("plan_step", node_plan)
    g.add_node("execute_step", node_execute)
    g.add_node("reflect_step", node_reflect)
    g.set_entry_point("load_claim")
    g.add_edge("load_claim", "vuln_gate")
    g.add_edge("vuln_gate", "plan_step")
    g.add_edge("plan_step", "execute_step")
    g.add_edge("execute_step", "reflect_step")
    g.add_conditional_edges("reflect_step", _should_continue, {
        "execute": "execute_step",
        "synthesize": END,
    })
    return g.compile(checkpointer=checkpointer)


def make_checkpointer(settings: Settings) -> BaseCheckpointSaver | None:
    """Return the SYNCHRONOUS checkpointer the graph compiles with at construction.

    Driving the graph through a checkpointer means LangGraph persists state after
    every super-step (keyed by thread_id == the turn's trace_id), so an
    interrupted turn can be resumed instead of being silently lost.

    - "memory": in-process MemorySaver. Removes the framework-level "we forgo
      persistence" gap and survives a client reconnect within a worker — but NOT
      a container restart (Apps containers are ephemeral + multi-worker).
    - "none": no checkpointing.
    - "lakebase": returns None here. The durable AsyncPostgresSaver needs a live
      connection, so it is opened asynchronously in ClaimsCopilotAgent.aopen()
      (see backend/agent/lakebase.py); until then the graph runs uncheckpointed.

    SECURITY: the OBO token is redacted from AgentState before it enters the
    graph (see _redact_user), so no checkpointer — durable or not — persists it.
    """
    if settings.checkpointer in ("none", "lakebase"):
        return None
    return MemorySaver()


# AgentState.user must stay a UserContext (nodes read .user_id/.role/.country),
# but the OBO token is unused by the agent (tools authenticate as the App SP)
# and would be expired on resume anyway — so we blank it before it can reach any
# checkpoint store.
_OBO_REDACTED = "__redacted__"


def _redact_user(user: UserContext) -> UserContext:
    return user.model_copy(update={"obo_token": _OBO_REDACTED})


@dataclass
class _StreamProgress:
    """Maps node-completion updates from graph.astream(stream_mode="updates")
    onto the SSE tool contract, emitting one tool.start before execution and a
    matching tool.end (same call_id) after — the correlation the frontend keys
    on. Tracks how many starts/ends have been emitted so multi-cycle reflect
    loops don't double-emit."""

    max_tools: int
    started: list[tuple[str, str]] = field(default_factory=list)  # (call_id, tool)
    n_ended: int = 0
    plan_emitted_len: int = -1

    def on_update(self, merged: AgentState, node_name: str):
        plan = merged.get("plan") or []
        results = merged.get("tool_results") or []

        # Plan card: once when planning completes, again only if reflect grew it.
        if node_name == "plan_step" and self.plan_emitted_len < 0:
            self.plan_emitted_len = len(plan)
            yield SSEPlan(plan=plan, stop_if_enough=True).model_dump()
        elif node_name == "reflect_step" and len(plan) > self.plan_emitted_len:
            self.plan_emitted_len = len(plan)
            yield SSEPlan(plan=plan, stop_if_enough=True).model_dump()

        # tool.start for newly-revealed plan items that will execute (budget cap).
        limit = min(len(plan), self.max_tools)
        while len(self.started) < limit:
            item = plan[len(self.started)]
            call_id = str(uuid.uuid4())
            self.started.append((call_id, item.tool))
            yield SSEToolStart(call_id=call_id, tool=item.tool, args=item.args).model_dump()

        # tool.end for newly-produced results (1:1 with plan/start index).
        while self.n_ended < len(results):
            tr = results[self.n_ended]
            call_id, _tool = self.started[self.n_ended]
            res = tr.get("result")
            err = (
                res.get("error", {}).get("message")
                if isinstance(res, dict) and res.get("error")
                else None
            )
            yield SSEToolEnd(
                call_id=call_id, tool=tr["tool"],
                result_preview=json.dumps(tr["result"], default=str)[:300],
                latency_ms=tr.get("latency_ms", 0), error=err,
            ).model_dump()
            self.n_ended += 1

        if node_name == "reflect_step":
            decision = _should_continue(merged)
            yield SSEReflect(
                decision="done" if decision == "synthesize" else "more",
                note=f"cycle={merged.get('reflect_cycles', 0)}",
            ).model_dump()

    def drain_orphans(self):
        """Resolve any tool.start that never got an execution (e.g. reflect
        proposed tools past the turn budget) so the UI cards don't hang on a
        spinner."""
        while self.n_ended < len(self.started):
            call_id, tool = self.started[self.n_ended]
            self.n_ended += 1
            yield SSEToolEnd(
                call_id=call_id, tool=tool,
                result_preview="(not executed — turn budget reached)",
                latency_ms=0, error="not_executed",
            ).model_dump()


# ---- Public API -------------------------------------------------------------

@dataclass
class ClaimsCopilotAgent:
    settings: Settings = field(default_factory=get_settings)
    graph: Any = field(default=None)
    checkpointer: BaseCheckpointSaver | None = field(default=None)
    _pool: Any = field(default=None, repr=False)

    def __post_init__(self):
        # Synchronous savers (memory/none) are ready immediately. "lakebase"
        # returns None here and is wired up in aopen().
        self.checkpointer = make_checkpointer(self.settings)
        self.graph = _build_graph(self.checkpointer)

    async def aopen(self) -> "ClaimsCopilotAgent":
        """Async startup for savers that need a live connection (lakebase).

        No-op for memory/none. Call once from the app lifespan AFTER
        construction. Recompiles the graph with the durable saver attached.
        """
        if self.settings.checkpointer == "lakebase" and self.checkpointer is None:
            from backend.agent.lakebase import open_lakebase_saver

            self.checkpointer, self._pool = await open_lakebase_saver(self.settings)
            self.graph = _build_graph(self.checkpointer)
            log.info("lakebase_checkpointer_ready", pool_max=self.settings.lakebase_pool_max_size)
        return self

    async def aclose(self) -> None:
        """Release the durable saver's connection pool. Idempotent."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def run_stream(
        self,
        *,
        user: UserContext,
        session_id: str,
        claim_id: Optional[str],
        message: str,
        language: str,
        confirmations: list[str],
    ) -> AsyncIterator[dict]:
        if language not in self.settings.allowed_languages:
            yield SSEError(code="UNSUPPORTED_LANG", message=REFUSAL_UNSUPPORTED_LANG, recoverable=False).model_dump()
            return

        trace_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        # thread_id is per-TURN (not per-session): each turn is its own isolated
        # checkpoint thread, so in-flight state never bleeds between a session's
        # turns and is purged when the turn ends (see finally).
        run_config = {"configurable": {"thread_id": trace_id}}

        try:
            with mlflow.start_span(name="session.turn") as root_span:
                root_span.set_attribute("session.id", session_id)
                root_span.set_attribute("user.id", user.user_id)
                root_span.set_attribute("agent.version", self.settings.agent_version)
                root_span.set_attribute("language", language)

                yield SSESessionStart(
                    trace_id=trace_id,
                    model=self.settings.chat_endpoint_primary,
                    ts=datetime.utcnow(),
                ).model_dump()

                init_state: AgentState = {
                    # Redact the OBO token before it enters graph state so it is
                    # never written to a checkpoint store (it's unused by tools).
                    "user": _redact_user(user), "session_id": session_id, "claim_id": claim_id,
                    "language": language, "user_message": message,
                    "confirmations": confirmations, "tool_results": [], "plan": [],
                    "reflect_cycles": 0, "cost_usd": 0.0, "fallback_step": 0,
                    "trace_id": trace_id,
                }

                # Drive the compiled LangGraph (load -> vuln -> plan -> execute
                # <-> reflect -> END). stream_mode="updates" surfaces each node's
                # output as it completes; _StreamProgress maps those boundaries
                # onto the SSE contract. The graph is now the single execution
                # path — no more hand-rolled loop diverging from the compiled
                # graph — and state flows through the checkpointer.
                merged: AgentState = dict(init_state)
                progress = _StreamProgress(max_tools=self.settings.max_tool_calls_per_turn)
                async for update in self.graph.astream(
                    init_state, run_config, stream_mode="updates"
                ):
                    for node_name, node_state in update.items():
                        if node_state:
                            merged.update(node_state)
                        for ev in progress.on_update(merged, node_name):
                            yield ev
                # Resolve any tool.start that never executed (budget exhausted).
                for ev in progress.drain_orphans():
                    yield ev

                # Streamed synthesis runs on the graph's final state. Synthesis
                # is intentionally outside the graph: token-level SSE does not
                # map onto a single node boundary.
                async for ev in stream_synthesize(merged):
                    yield ev

                total_ms = int((time.perf_counter() - t0) * 1000)
                root_span.set_attribute("turn.latency_ms", total_ms)
                root_span.set_attribute("turn.cost_usd", merged.get("cost_usd", 0.0))
                yield SSEDone(
                    trace_id=trace_id,
                    latency_ms_total=total_ms,
                    cost_usd=merged.get("cost_usd", 0.0),
                    fallback_step=merged.get("fallback_step", 0),
                    decision_class=merged.get("decision_class", "UNDETERMINED"),
                    confidence=merged.get("confidence", "LOW"),
                ).model_dump()
        finally:
            # Bound MemorySaver growth: drop this turn's checkpoint thread.
            if self.checkpointer is not None:
                try:
                    await self.checkpointer.adelete_thread(trace_id)
                except Exception:  # noqa: BLE001
                    pass


def build_agent() -> ClaimsCopilotAgent:
    return ClaimsCopilotAgent()
