#!/usr/bin/env python3
"""Preflight — verify a Databricks workspace has everything ClaimsCopilot needs
BEFORE you deploy it. Read-only: lists/gets resources, never creates anything.

    python scripts/preflight.py --profile <profile> --warehouse-id <id> \
        [--catalog <name>] [--catalog-ai <name>] [--vs-endpoint <name>] \
        [--chat-primary <ep>] [--mlflow-experiment <path>]

Exits 0 if every critical check passes, 1 otherwise. Use it in CI or as the
first step a customer runs against their own workspace.
"""

from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

# Make `backend` importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULTS = {
    "chat_primary": "databricks-claude-opus-4-8",
    "chat_fb1": "databricks-claude-sonnet-4-6",
    "chat_fb2": "databricks-meta-llama-3-3-70b-instruct",
    "embed_ml": "databricks-qwen3-embedding-0-6b",
    "vs_endpoint": "claimscopilot_vs",
    "mlflow_experiment": "/Shared/claimscopilot",
}

_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗"}  # ✓ ⚠ ✗


def main() -> int:
    p = argparse.ArgumentParser(description="ClaimsCopilot workspace preflight")
    p.add_argument("--profile", default=None, help="Databricks CLI profile")
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", default="main",
                   help="catalog for both lake + ai unless --catalog-ai is given")
    p.add_argument("--catalog-ai", default=None)
    p.add_argument("--vs-endpoint", default=DEFAULTS["vs_endpoint"])
    p.add_argument("--chat-primary", default=DEFAULTS["chat_primary"])
    p.add_argument("--chat-fb1", default=DEFAULTS["chat_fb1"])
    p.add_argument("--chat-fb2", default=DEFAULTS["chat_fb2"])
    p.add_argument("--embed-ml", default=DEFAULTS["embed_ml"])
    p.add_argument("--mlflow-experiment", default=DEFAULTS["mlflow_experiment"])
    a = p.parse_args()

    settings = SimpleNamespace(
        chat_endpoint_primary=a.chat_primary,
        chat_endpoint_fallback_1=a.chat_fb1,
        chat_endpoint_fallback_2=a.chat_fb2,
        embed_endpoint_multilingual=a.embed_ml,
        databricks_warehouse_id=a.warehouse_id,
        catalog_lake=a.catalog,
        catalog_ai=a.catalog_ai or a.catalog,
        vs_endpoint=a.vs_endpoint,
        mlflow_experiment=a.mlflow_experiment,
    )

    from databricks.sdk import WorkspaceClient
    ws = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()

    # Point MLflow at the same workspace so the experiment probe is meaningful.
    try:
        import mlflow
        mlflow.set_tracking_uri(f"databricks://{a.profile}" if a.profile else "databricks")
    except Exception:  # noqa: BLE001
        pass

    from backend.readiness import check_readiness
    report = check_readiness(settings, ws=ws)

    print("\nClaimsCopilot preflight\n" + "=" * 60)
    for c in report["checks"]:
        icon = _ICON["ok"] if c["ok"] else (_ICON["fail"] if c["critical"] else _ICON["warn"])
        print(f"  {icon}  {c['name']:<28} {c['detail']}")
    print("=" * 60)
    if report["ready"]:
        print(f"{_ICON['ok']} READY — all critical checks passed. Safe to deploy.\n")
        return 0
    print(f"{_ICON['fail']} NOT READY — run scripts/bootstrap.py (or fix the ✗ items) before deploying.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
