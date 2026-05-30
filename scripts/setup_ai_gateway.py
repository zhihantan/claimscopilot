#!/usr/bin/env python3
"""Provision AI Gateway (guardrails + rate limits + usage/payload logging) on a
serving endpoint, then point ClaimsCopilot at it.

AI Gateway config is set ON a serving endpoint you CONTROL — an external-model,
custom, or provisioned-throughput endpoint. System pay-per-token FMAPI endpoints
(databricks-claude-*) can't be reconfigured, so create a gateway/proxy endpoint
first and pass its name here.

    python scripts/setup_ai_gateway.py --profile <p> --endpoint <gateway-endpoint> \
        --catalog <catalog> [--pii BLOCK] [--rate-limit-per-min 120] [--apply]

After --apply: set CC_GATEWAY_CHAT=<gateway-endpoint> and CC_USE_GATEWAY=true
(via scripts/init.py or app.yaml) and redeploy — the app then calls the guarded
endpoint first, falling back to the raw FMAPI endpoints.

Without --apply this is a DRY RUN: it builds + prints the config, changes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from databricks.sdk.service.serving import (  # noqa: E402
    AiGatewayGuardrailParameters,
    AiGatewayGuardrailPiiBehavior,
    AiGatewayGuardrailPiiBehaviorBehavior,
    AiGatewayGuardrails,
    AiGatewayInferenceTableConfig,
    AiGatewayRateLimit,
    AiGatewayRateLimitKey,
    AiGatewayRateLimitRenewalPeriod,
    AiGatewayUsageTrackingConfig,
)


def build_config(*, pii: str, rate_per_min: int, rate_key: str,
                 catalog: str | None, usage: bool, infer: bool):
    """Build the (guardrails, usage_cfg, inference_cfg, rate_limits) tuple for
    put_ai_gateway. Pure — no workspace calls, so it's unit-testable."""
    pii_beh = (AiGatewayGuardrailPiiBehavior(behavior=AiGatewayGuardrailPiiBehaviorBehavior.BLOCK)
               if pii == "BLOCK" else None)
    guardrails = AiGatewayGuardrails(
        input=AiGatewayGuardrailParameters(safety=True),
        output=AiGatewayGuardrailParameters(safety=True, pii=pii_beh),
    )
    usage_cfg = AiGatewayUsageTrackingConfig(enabled=usage)
    infer_cfg = None
    if infer and catalog:
        infer_cfg = AiGatewayInferenceTableConfig(
            enabled=True, catalog_name=catalog, schema_name="app",
            table_name_prefix="gateway")
    rate_limits = None
    if rate_per_min > 0:
        rate_limits = [AiGatewayRateLimit(
            calls=rate_per_min,
            renewal_period=AiGatewayRateLimitRenewalPeriod.MINUTE,
            key=(AiGatewayRateLimitKey.USER if rate_key == "user"
                 else AiGatewayRateLimitKey.ENDPOINT))]
    return guardrails, usage_cfg, infer_cfg, rate_limits


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision AI Gateway on a serving endpoint")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--endpoint", required=True, help="serving endpoint to configure (one you control)")
    ap.add_argument("--catalog", default=None, help="catalog for the inference (payload) table; omit to skip")
    ap.add_argument("--pii", choices=["BLOCK", "NONE"], default="NONE",
                    help="output PII guardrail (SDK 0.40 supports BLOCK or NONE)")
    ap.add_argument("--rate-limit-per-min", type=int, default=0)
    ap.add_argument("--rate-limit-key", choices=["endpoint", "user"], default="endpoint")
    ap.add_argument("--no-usage-tracking", action="store_true")
    ap.add_argument("--no-inference-table", action="store_true")
    ap.add_argument("--apply", action="store_true", help="apply (default: dry run)")
    a = ap.parse_args()

    guardrails, usage_cfg, infer_cfg, rate_limits = build_config(
        pii=a.pii, rate_per_min=a.rate_limit_per_min, rate_key=a.rate_limit_key,
        catalog=a.catalog, usage=not a.no_usage_tracking, infer=not a.no_inference_table)

    print(f"\nAI Gateway config for endpoint '{a.endpoint}':")
    print("  guardrails.input.safety  = True")
    print("  guardrails.output.safety = True")
    print(f"  guardrails.output.pii    = {a.pii}")
    print(f"  usage_tracking           = {not a.no_usage_tracking}")
    print(f"  inference_table          = "
          f"{(a.catalog + '.app.gateway_*') if infer_cfg else 'disabled (no --catalog)'}")
    print(f"  rate_limit               = "
          f"{(str(a.rate_limit_per_min) + '/min per ' + a.rate_limit_key) if rate_limits else 'none'}")

    if not a.apply:
        print("\n(dry run — nothing changed. Re-run with --apply to set it.)")
        print("NOTE: --endpoint must be one you control (external-model / custom / "
              "provisioned-throughput), not a system pay-per-token FMAPI endpoint.")
        return 0

    from databricks.sdk import WorkspaceClient
    ws = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()
    ws.serving_endpoints.put_ai_gateway(
        name=a.endpoint, guardrails=guardrails, usage_tracking_config=usage_cfg,
        inference_table_config=infer_cfg, rate_limits=rate_limits)
    print(f"\n✓ AI Gateway configured on '{a.endpoint}'.\n"
          f"Next: set CC_GATEWAY_CHAT={a.endpoint} and CC_USE_GATEWAY=true "
          f"(scripts/init.py or app.yaml), then redeploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
