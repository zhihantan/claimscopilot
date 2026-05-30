#!/usr/bin/env python3
"""Log the ClaimsCopilot ChatAgent to MLflow / Unity Catalog and (optionally)
deploy it to Model Serving via the Mosaic AI Agent Framework.

This is the SERVING path — a registered, governed, separately-servable agent —
complementary to the in-process Databricks App. The SAME agent code
(backend/agent/chat_agent.py) backs both.

    # preview the resources + model name (no logging; verifies resource specs)
    python scripts/register_agent.py --catalog <cat>

    # log + register to Unity Catalog
    python scripts/register_agent.py --catalog <cat> --warehouse-id <id> --profile <p> --register

    # ...and deploy to Model Serving (creates an endpoint + review app)
    python scripts/register_agent.py --catalog <cat> --warehouse-id <id> --profile <p> --register --deploy

Prereqs for --register: the catalog/endpoints/indexes exist (scripts/bootstrap.py)
and `<catalog_ai>.models` schema exists.
"""

from __future__ import annotations

import argparse
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

_TOOL_FUNCS = ["get_policy_terms", "get_claim", "get_claim_events", "get_claim_history",
               "get_device", "get_repair_order", "compute_excess",
               "estimate_repair_cost", "check_partner_sla"]
_INDEXES = ["policy_wordings", "adjuster_kb", "claim_narratives"]
_CHAT_DEFAULTS = ["databricks-claude-opus-4-8", "databricks-claude-sonnet-4-6",
                  "databricks-meta-llama-3-3-70b-instruct"]
_EMBED_DEFAULT = "databricks-qwen3-embedding-0-6b"


def build_resources(catalog: str, catalog_ai: str, warehouse_id: str,
                    chat_endpoints: list[str], embed_endpoint: str):
    """Declare the UC/serving resources the agent needs so the served model gets
    automatic auth passthrough. Pure (constructs the resource specs) — takes
    endpoint names directly so it doesn't depend on the app's runtime Settings."""
    from mlflow.models.resources import (
        DatabricksFunction, DatabricksServingEndpoint,
        DatabricksSQLWarehouse, DatabricksVectorSearchIndex,
    )
    res = [DatabricksServingEndpoint(endpoint_name=ep) for ep in [*chat_endpoints, embed_endpoint]]
    res += [DatabricksFunction(function_name=f"{catalog}.tools.{fn}") for fn in _TOOL_FUNCS]
    res += [DatabricksVectorSearchIndex(index_name=f"{catalog_ai}.indexes.{i}") for i in _INDEXES]
    res.append(DatabricksSQLWarehouse(warehouse_id=warehouse_id))
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Register/deploy the ClaimsCopilot served agent")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--catalog-ai", default=None)
    ap.add_argument("--warehouse-id", default=None)
    ap.add_argument("--model-name", default=None,
                    help="UC model (default <catalog_ai>.models.claimscopilot_agent)")
    ap.add_argument("--register", action="store_true", help="log + register to UC")
    ap.add_argument("--deploy", action="store_true", help="deploy to Model Serving (implies --register)")
    ap.add_argument("--chat-primary", default=_CHAT_DEFAULTS[0])
    ap.add_argument("--chat-fb1", default=_CHAT_DEFAULTS[1])
    ap.add_argument("--chat-fb2", default=_CHAT_DEFAULTS[2])
    ap.add_argument("--embed-ml", default=_EMBED_DEFAULT)
    ap.add_argument("--mlflow-experiment", default="/Shared/claimscopilot")
    a = ap.parse_args()
    a.catalog_ai = a.catalog_ai or a.catalog
    model_name = a.model_name or f"{a.catalog_ai}.models.claimscopilot_agent"
    chat_eps = [a.chat_primary, a.chat_fb1, a.chat_fb2]
    resources = build_resources(a.catalog, a.catalog_ai, a.warehouse_id or "<warehouse-id>",
                                chat_eps, a.embed_ml)

    n_ep = sum("ServingEndpoint" in type(r).__name__ for r in resources)
    n_fn = sum("Function" in type(r).__name__ for r in resources)
    n_ix = sum("Index" in type(r).__name__ for r in resources)
    print("\nServed-agent plan:")
    print(f"  UC model   : {model_name}")
    print(f"  code       : backend/agent/chat_agent.py (MLflow models-from-code)")
    print(f"  resources  : {len(resources)}  ({n_ep} serving endpoints, {n_fn} UC functions, "
          f"{n_ix} VS indexes, 1 warehouse)")

    if not (a.register or a.deploy):
        print("\n(no --register: nothing logged. Re-run with --register [--deploy].)")
        return 0
    if not a.warehouse_id:
        print("--warehouse-id is required with --register/--deploy")
        return 1

    import mlflow
    mlflow.set_tracking_uri(f"databricks://{a.profile}" if a.profile else "databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(a.mlflow_experiment)

    chat_agent_file = os.path.join(_REPO, "backend", "agent", "chat_agent.py")
    with mlflow.start_run(run_name="register-claimscopilot-agent"):
        info = mlflow.pyfunc.log_model(
            artifact_path="agent",
            python_model=chat_agent_file,                 # models-from-code (set_model in the file)
            code_paths=[os.path.join(_REPO, "backend")],  # bundle the backend package
            pip_requirements=os.path.join(_REPO, "requirements.txt"),
            resources=resources,
            registered_model_name=model_name,
            input_example={"messages": [{"role": "user", "content": "Is a cracked screen covered?"}]},
        )
    version = getattr(info, "registered_model_version", None)
    print(f"✓ logged + registered: {model_name} v{version}")

    if a.deploy:
        from databricks.agents import deploy
        d = deploy(model_name, version)
        print(f"✓ deployed to Model Serving: {getattr(d, 'endpoint_name', d)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
