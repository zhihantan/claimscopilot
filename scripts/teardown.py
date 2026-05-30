#!/usr/bin/env python3
"""Tear down the data-plane resources `bootstrap.py` created.

Removes the Vector Search indexes, the ClaimsCopilot UC schemas (CASCADE), the
secret scope, and the MLflow experiment. Does NOT touch the bundle resources
(App, jobs, DLT pipeline) — remove those with `databricks bundle destroy -t <t>`.

    python scripts/teardown.py --profile <p> --warehouse-id <id> --catalog <cat> \
        --vs-endpoint claimscopilot_vs [--drop-endpoint] [--yes]

DESTRUCTIVE. Without --yes it is a DRY RUN (prints the plan, deletes nothing,
and needs no workspace auth).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SCHEMAS = ["policy", "claims", "devices", "repairs", "partners",
            "kb", "tools", "app", "eval", "indexes", "models"]
_INDEXES = ["policy_wordings", "adjuster_kb", "claim_narratives"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Tear down ClaimsCopilot data-plane resources")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--catalog-ai", default=None)
    ap.add_argument("--vs-endpoint", default="claimscopilot_vs")
    ap.add_argument("--secret-scope", default="claimscopilot")
    ap.add_argument("--mlflow-experiment", default="/Shared/claimscopilot")
    ap.add_argument("--drop-endpoint", action="store_true",
                    help="also delete the VS endpoint (it may be shared — off by default)")
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    a = ap.parse_args()
    a.catalog_ai = a.catalog_ai or a.catalog

    plan: list[tuple[str, str]] = []
    plan += [("vs-index", f"{a.catalog_ai}.indexes.{i}") for i in _INDEXES]
    if a.drop_endpoint:
        plan.append(("vs-endpoint", a.vs_endpoint))
    plan += [("schema", f"{a.catalog}.{s} (CASCADE)") for s in _SCHEMAS]
    plan.append(("secret-scope", a.secret_scope))
    plan.append(("mlflow-exp", a.mlflow_experiment))

    print(f"\nTeardown plan ({'APPLY' if a.yes else 'DRY RUN'}):")
    for kind, name in plan:
        print(f"  - {kind:13} {name}")
    if not a.yes:
        print("\n(dry run — nothing deleted. Re-run with --yes to apply.)")
        print("Bundle resources (App, jobs, DLT) are removed separately:")
        print("  databricks bundle destroy -t <target>")
        return 0

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
    ws = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()

    from databricks.vector_search.client import VectorSearchClient
    bearer = ws.config.authenticate().get("Authorization", "").removeprefix("Bearer ")
    vsc = VectorSearchClient(workspace_url=ws.config.host, personal_access_token=bearer,
                             disable_notice=True)
    for i in _INDEXES:
        name = f"{a.catalog_ai}.indexes.{i}"
        try:
            vsc.delete_index(endpoint_name=a.vs_endpoint, index_name=name)
            print(f"  deleted index {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  index {name}: {e}")
    if a.drop_endpoint:
        try:
            ws.vector_search_endpoints.delete_endpoint(endpoint_name=a.vs_endpoint)
            print(f"  deleted endpoint {a.vs_endpoint}")
        except Exception as e:  # noqa: BLE001
            print(f"  endpoint: {e}")

    for s in _SCHEMAS:
        stmt = f"DROP SCHEMA IF EXISTS {a.catalog}.{s} CASCADE"
        try:
            ws.statement_execution.execute_statement(
                statement=stmt, warehouse_id=a.warehouse_id, wait_timeout="50s",
                on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL)
            print(f"  dropped schema {a.catalog}.{s}")
        except Exception as e:  # noqa: BLE001
            print(f"  schema {s}: {e}")

    try:
        ws.secrets.delete_scope(scope=a.secret_scope)
        print(f"  deleted secret scope {a.secret_scope}")
    except Exception as e:  # noqa: BLE001
        print(f"  secret scope: {e}")

    try:
        import mlflow
        mlflow.set_tracking_uri(f"databricks://{a.profile}" if a.profile else "databricks")
        exp = mlflow.get_experiment_by_name(a.mlflow_experiment)
        if exp:
            mlflow.delete_experiment(exp.experiment_id)
            print(f"  deleted MLflow experiment {a.mlflow_experiment}")
    except Exception as e:  # noqa: BLE001
        print(f"  mlflow: {e}")

    print("teardown done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
