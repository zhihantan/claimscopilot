"""The MLflow ChatAgent wrapper drives the LangGraph agent to a ChatAgentResponse.
Tests are SYNC because `predict` calls `asyncio.run` internally (it can't run
inside pytest-asyncio's loop). The underlying agent is mocked — no LLM/tools."""

from __future__ import annotations

from mlflow.types.agent import ChatAgentMessage

from backend.agent.chat_agent import (
    ClaimsCopilotChatAgent,
    _last_user_text,
    _service_user,
)


async def _fake_stream(**kwargs):
    yield {"event": "session.start", "trace_id": "tr-1"}
    yield {"event": "token", "delta": "An excess is "}
    yield {"event": "token", "delta": "the fixed amount you pay."}
    yield {"event": "citation", "citation": {"kind": "policy", "label": "[POLICY §5]", "ref": "W#sec=5"}}
    yield {"event": "done", "decision_class": "UNDETERMINED", "confidence": "HIGH",
           "trace_id": "tr-1", "cost_usd": 0.012}


def test_predict_assembles_chat_agent_response():
    a = ClaimsCopilotChatAgent()
    a._agent.run_stream = _fake_stream  # replace the async generator with a canned one
    resp = a.predict([ChatAgentMessage(role="user", content="what is an excess?")])

    assert resp.messages[0].role == "assistant"
    assert "fixed amount" in resp.messages[0].content
    co = resp.custom_outputs
    assert co["decision_class"] == "UNDETERMINED"
    assert co["confidence"] == "HIGH"
    assert co["trace_id"] == "tr-1"
    assert co["cost_usd"] == 0.012
    assert len(co["citations"]) == 1


def test_predict_accepts_dict_messages_and_custom_inputs():
    a = ClaimsCopilotChatAgent()
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        yield {"event": "token", "delta": "ok"}
        yield {"event": "done", "decision_class": "APPROVE", "confidence": "MED", "trace_id": "t", "cost_usd": 0.0}

    a._agent.run_stream = _capture
    resp = a.predict(
        [{"role": "user", "content": "covered?"}],
        custom_inputs={"claim_id": "CLM-1", "language": "es", "role": "TEAM_LEAD"},
    )
    assert resp.messages[0].content == "ok"
    assert captured["claim_id"] == "CLM-1"
    assert captured["language"] == "es"
    assert captured["user"].role == "TEAM_LEAD"


def test_last_user_text_objects_and_dicts():
    assert _last_user_text([{"role": "user", "content": "hi"}]) == "hi"
    assert _last_user_text([ChatAgentMessage(role="user", content="yo")]) == "yo"
    assert _last_user_text([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "second"},
    ]) == "second"


def test_service_user_defaults_and_overrides():
    u = _service_user({})
    assert u.role == "ADJUSTER_L2" and u.country == "GB" and u.obo_token == "__served__"
    u2 = _service_user({"user_id": "x@y.com", "role": "TEAM_LEAD", "country": "JP"})
    assert (u2.user_id, u2.role, u2.country) == ("x@y.com", "TEAM_LEAD", "JP")
