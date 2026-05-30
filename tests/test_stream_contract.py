"""End-to-end SSE contract test for the graph-driven streaming path (#1).

Drives ClaimsCopilotAgent.run_stream with a mocked LLM and mocked tools (no
Databricks), asserting:
  * the event ORDER the frontend expects:
    session.start -> plan -> tool.start -> tool.end -> reflect -> token(s)
    -> citation(s) -> done;
  * tool.start / tool.end correlate by call_id (the frontend keys on this;
    the pre-refactor path emitted mismatched ids so cards never resolved);
  * server-verified citations still flow through;
  * the per-turn checkpoint thread is purged when the turn ends (no leak);
  * the vulnerability gate short-circuits before any tool runs.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from backend.agent import agent as agent_mod
from backend.agent import tools as tools_mod
from backend.agent.prompts import (
    PLAN_SYSTEM_PROMPT,
    REFLECT_SYSTEM_PROMPT,
    REFUSAL_VULNERABILITY,
)
from backend.schemas import UserContext

POLICY_REF = "WORD-GB-v2025-04#sec=3.2"
SYNTH_DELTAS = [
    "The cracked screen is covered ",
    "under [POLICY §3.2 / wording v2025-04]. ",
    "An excess applies.\n",
    '<decision class="APPROVE" confidence="HIGH" />',
]


@contextmanager
def _noop_span(*_a, **_k):
    class _S:
        def set_attribute(self, *_x, **_y):
            return None

    yield _S()


@pytest.fixture
def user() -> UserContext:
    return UserContext(
        user_id="adj@example.com", email="adj@example.com", display_name="Adj",
        role="ADJUSTER_L2", country="GB", obo_token="tok",
        workspace_host="https://example.cloud.databricks.com",
    )


def _install_common_mocks(monkeypatch):
    monkeypatch.setattr(agent_mod.mlflow, "start_span", _noop_span)

    async def fake_llm(user, settings, messages, *, temperature=0.0,
                       max_tokens=0, json_mode=False, stream=False):
        if stream:
            async def _gen():
                for d in SYNTH_DELTAS:
                    yield d
            return _gen()
        sys_content = messages[0]["content"] if messages else ""
        if sys_content == PLAN_SYSTEM_PROMPT:
            content = (
                '{"plan":[{"tool":"search_policy_wordings",'
                '"args":{"query":"cracked screen excess","language":"en"},'
                '"why":"confirm coverage"}]}'
            )
        elif sys_content == REFLECT_SYSTEM_PROMPT:
            content = '{"decision":"done","note":"enough","extra_plan":[]}'
        else:
            content = "{}"
        return agent_mod.LLMResult(
            content=content, model="mock", prompt_tokens=10,
            completion_tokens=10, fallback_step=0, cost_usd=0.001,
        )

    monkeypatch.setattr(agent_mod, "_llm_complete", fake_llm)


async def _drain(agen):
    return [ev async for ev in agen]


def _kinds(events):
    return [e["event"] for e in events]


async def test_happy_path_event_order_and_call_id_correlation(monkeypatch, user):
    _install_common_mocks(monkeypatch)

    async def fake_get_claim(args, u):
        return {"claim_id": args.claim_id, "status": "OPEN",
                "incident_description_en": "Dropped phone, screen cracked."}

    async def fake_search(args, u):
        return {"results": [{
            "citation": "[POLICY §3.2 / wording v2025-04]",
            "ref": POLICY_REF, "section_title": "Accidental damage",
            "text": "Screen damage is covered subject to excess.", "language": "en",
        }]}

    monkeypatch.setattr(tools_mod, "get_claim", fake_get_claim)
    monkeypatch.setitem(
        tools_mod.ALL_TOOLS, "search_policy_wordings",
        (fake_search, tools_mod.SearchPolicyWordingsIn, "search policy"),
    )

    agent = agent_mod.build_agent()
    events = await _drain(agent.run_stream(
        user=user, session_id="s1", claim_id="CLM-GB-1042",
        message="Is the cracked screen covered? What's the excess?",
        language="en", confirmations=[],
    ))
    kinds = _kinds(events)

    # --- presence + order ---
    assert kinds[0] == "session.start"
    assert kinds[-1] == "done"
    for a, b in [("session.start", "plan"), ("plan", "tool.start"),
                 ("tool.start", "tool.end"), ("tool.end", "reflect"),
                 ("reflect", "token"), ("token", "citation"), ("citation", "done")]:
        assert kinds.index(a) < kinds.index(b), f"{a} must precede {b}: {kinds}"

    # --- call_id correlation (the frontend bug fix) ---
    starts = [e for e in events if e["event"] == "tool.start"]
    ends = [e for e in events if e["event"] == "tool.end"]
    assert len(starts) == 1 and len(ends) == 1
    assert sorted(s["call_id"] for s in starts) == sorted(e["call_id"] for e in ends)
    assert starts[0]["tool"] == "search_policy_wordings"
    assert ends[0]["error"] is None

    # --- citation verified against the tool result ---
    cites = [e for e in events if e["event"] == "citation"]
    assert any(c["citation"]["ref"] == POLICY_REF for c in cites)

    # --- streamed answer + decision ---
    answer = "".join(e["delta"] for e in events if e["event"] == "token")
    assert "covered" in answer
    done = events[-1]
    assert done["decision_class"] == "APPROVE"
    assert done["confidence"] == "HIGH"
    assert done["cost_usd"] > 0


async def test_checkpoint_thread_purged_after_turn(monkeypatch, user):
    _install_common_mocks(monkeypatch)

    async def fake_search(args, u):
        return {"results": []}

    monkeypatch.setitem(
        tools_mod.ALL_TOOLS, "search_policy_wordings",
        (fake_search, tools_mod.SearchPolicyWordingsIn, "search policy"),
    )

    agent = agent_mod.build_agent()
    assert agent.checkpointer is not None  # default CC_CHECKPOINTER=memory

    events = await _drain(agent.run_stream(
        user=user, session_id="s2", claim_id=None,
        message="Is a cracked screen covered?", language="en", confirmations=[],
    ))
    trace_id = next(e["trace_id"] for e in events if e["event"] == "session.start")

    # The turn ran through the checkpointer, then purged its thread — so the
    # saver doesn't grow one entry per turn for the life of the process.
    assert trace_id not in agent.checkpointer.storage


async def test_obo_token_redacted_from_graph_state(monkeypatch, user):
    """The OBO token must be scrubbed from state before it enters the graph, so
    no checkpointer (durable or in-memory) ever persists it — while the user's
    identity is preserved for tool auditing."""
    _install_common_mocks(monkeypatch)
    seen: dict[str, str] = {}

    async def capture_search(args, u):
        seen["obo_token"] = u.obo_token
        seen["user_id"] = u.user_id
        return {"results": []}

    monkeypatch.setitem(
        tools_mod.ALL_TOOLS, "search_policy_wordings",
        (capture_search, tools_mod.SearchPolicyWordingsIn, "search policy"),
    )

    assert user.obo_token == "tok"  # the real inbound token
    agent = agent_mod.build_agent()
    await _drain(agent.run_stream(
        user=user, session_id="s4", claim_id=None,
        message="Is a cracked screen covered?", language="en", confirmations=[],
    ))
    assert seen["obo_token"] == agent_mod._OBO_REDACTED  # never the real token
    assert seen["user_id"] == "adj@example.com"          # identity preserved


async def test_vulnerability_gate_short_circuits_before_tools(monkeypatch, user):
    _install_common_mocks(monkeypatch)

    escalations = []

    async def fake_escalate(args, u):
        escalations.append(args.reason)
        return {"escalation_id": "esc-1", "queue": "VULN_CARE", "eta_minutes": 30}

    monkeypatch.setattr(tools_mod, "escalate_to_human", fake_escalate)

    agent = agent_mod.build_agent()
    events = await _drain(agent.run_stream(
        user=user, session_id="s3", claim_id=None,
        message="My husband just passed away and I can't afford the excess.",
        language="en", confirmations=[],
    ))
    kinds = _kinds(events)

    assert "tool.start" not in kinds and "tool.end" not in kinds
    assert escalations == ["vulnerability_signal"]
    answer = "".join(e["delta"] for e in events if e["event"] == "token")
    assert answer == REFUSAL_VULNERABILITY["en"]
    done = events[-1]
    assert done["event"] == "done"
    assert done["decision_class"] == "UNDETERMINED"
    assert done["confidence"] == "LOW"
