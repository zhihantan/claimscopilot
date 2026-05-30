"""MLflow `ChatAgent` wrapper around the LangGraph ClaimsCopilot agent.

This exposes the SAME agent that runs in-process in the Databricks App
(backend/main.py) through the Mosaic AI Agent Framework `ChatAgent` interface,
so it can additionally be logged to Unity Catalog, served via Model Serving,
and scored with Agent Evaluation. See scripts/register_agent.py +
eval/run_agent_eval.py.

The agent's streaming SSE loop is driven to completion here and the final
answer + citations/decision are returned as a ChatAgentResponse (+ custom
outputs). `predict_stream` uses MLflow's default (wraps `predict`).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from mlflow.pyfunc import ChatAgent
from mlflow.types.agent import ChatAgentMessage, ChatAgentResponse

from backend.agent.agent import ClaimsCopilotAgent
from backend.agent.config import get_settings
from backend.schemas import UserContext


def _msg_attr(m: Any, key: str) -> Any:
    """Read a field from a ChatAgentMessage object OR a plain dict."""
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def _last_user_text(messages: list) -> str:
    for m in reversed(messages or []):
        if _msg_attr(m, "role") == "user":
            return _msg_attr(m, "content") or ""
    # fall back to the last message of any role
    return (_msg_attr(messages[-1], "content") if messages else "") or ""


def _service_user(custom_inputs: dict) -> UserContext:
    """Build the agent's UserContext for the served (no-OBO) path. Identity can
    be passed via custom_inputs; otherwise a service identity is used."""
    s = get_settings()
    uid = custom_inputs.get("user_id") or "served-agent@databricks"
    return UserContext(
        user_id=uid, email=custom_inputs.get("email") or uid,
        display_name=custom_inputs.get("display_name") or "Served Agent",
        role=custom_inputs.get("role") or "ADJUSTER_L2",
        country=custom_inputs.get("country") or "GB",
        obo_token="__served__",  # unused by tools (App SP / serving auth)
        workspace_host=s.databricks_host,
    )


class ClaimsCopilotChatAgent(ChatAgent):
    def __init__(self) -> None:
        self._agent = ClaimsCopilotAgent()

    def predict(self, messages: list, context: Optional[Any] = None,
                custom_inputs: Optional[dict] = None) -> ChatAgentResponse:
        ci = dict(custom_inputs or {})
        final_text, citations, decision, confidence, trace_id, cost = asyncio.run(
            self._run(
                user=_service_user(ci),
                message=_last_user_text(messages),
                claim_id=ci.get("claim_id"),
                language=ci.get("language", "en"),
                confirmations=ci.get("confirmations", []),
            )
        )
        return ChatAgentResponse(
            messages=[ChatAgentMessage(
                role="assistant", content=final_text or "",
                id=trace_id or uuid.uuid4().hex)],
            custom_outputs={
                "citations": citations, "decision_class": decision,
                "confidence": confidence, "trace_id": trace_id, "cost_usd": cost,
            },
        )

    async def _run(self, *, user, message, claim_id, language, confirmations):
        final_text = ""
        citations: list = []
        decision, confidence, trace_id, cost = "UNDETERMINED", "LOW", None, 0.0
        async for ev in self._agent.run_stream(
            user=user, session_id=f"served-{uuid.uuid4().hex}", claim_id=claim_id,
            message=message, language=language, confirmations=confirmations,
        ):
            e = ev.get("event")
            if e == "token":
                final_text += ev.get("delta", "")
            elif e == "citation":
                citations.append(ev["citation"])
            elif e == "done":
                decision = ev.get("decision_class", decision)
                confidence = ev.get("confidence", confidence)
                trace_id = ev.get("trace_id")
                cost = ev.get("cost_usd", 0.0)
            elif e == "error":
                final_text = final_text or ev.get("message", "agent error")
        return final_text, citations, decision, confidence, trace_id, cost


# MLflow `models from code` entrypoint: register_agent.py logs THIS file and
# mlflow.models.set_model marks the agent instance as the served model.
AGENT = ClaimsCopilotChatAgent()
try:
    import mlflow
    mlflow.models.set_model(AGENT)
except Exception:  # noqa: BLE001 — set_model only matters during logging
    pass
