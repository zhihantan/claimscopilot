"""AI Gateway: the chat endpoint chain (app-side routing) and the gateway
config builder (provisioning script). No live workspace needed."""

from __future__ import annotations

from databricks.sdk.service.serving import (
    AiGatewayGuardrailPiiBehaviorBehavior,
    AiGatewayRateLimitKey,
)

from backend.agent.config import get_settings
from scripts.setup_ai_gateway import build_config


def _settings(**kw):
    return get_settings().model_copy(update=kw)


def test_chain_default_is_fmapi_only():
    s = _settings(use_gateway=False)
    assert s.chat_endpoint_chain() == [
        s.chat_endpoint_primary, s.chat_endpoint_fallback_1, s.chat_endpoint_fallback_2,
    ]


def test_chain_prepends_gateway_when_enabled():
    s = _settings(use_gateway=True, gateway_chat="my-gateway-ep")
    chain = s.chat_endpoint_chain()
    assert chain[0] == "my-gateway-ep"  # gateway tried first
    assert chain[1:] == [  # FMAPI endpoints remain the fallback
        s.chat_endpoint_primary, s.chat_endpoint_fallback_1, s.chat_endpoint_fallback_2,
    ]


def test_chain_enabled_but_no_endpoint_is_safe():
    s = _settings(use_gateway=True, gateway_chat="")
    assert s.chat_endpoint_chain()[0] == s.chat_endpoint_primary  # no empty prepend


def test_build_config_block_pii_with_rate_and_table():
    g, usage, infer, rate = build_config(
        pii="BLOCK", rate_per_min=120, rate_key="user", catalog="acme",
        usage=True, infer=True)
    assert g.input.safety is True and g.output.safety is True
    assert g.output.pii.behavior == AiGatewayGuardrailPiiBehaviorBehavior.BLOCK
    assert usage.enabled is True
    assert infer.catalog_name == "acme" and infer.schema_name == "app"
    assert rate[0].calls == 120 and rate[0].key == AiGatewayRateLimitKey.USER


def test_build_config_minimal_no_pii_no_rate_no_table():
    g, usage, infer, rate = build_config(
        pii="NONE", rate_per_min=0, rate_key="endpoint", catalog=None,
        usage=False, infer=True)
    assert g.output.pii is None
    assert usage.enabled is False
    assert infer is None  # no catalog → no inference table
    assert rate is None
