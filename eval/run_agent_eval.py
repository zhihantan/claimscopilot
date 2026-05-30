#!/usr/bin/env python3
"""Score the registered ClaimsCopilot agent with Mosaic AI Agent Evaluation.

Complements eval/run_eval.py (which drives the in-process agent + custom
metrics). This runs the workspace's LLM-judge evaluation against the *registered*
model, so it needs the model registered (scripts/register_agent.py --register)
and a live workspace.

    python eval/run_agent_eval.py --model-name <cat>.models.claimscopilot_agent \
        --version 1 --profile <p> [--limit 10]
"""

from __future__ import annotations

import argparse
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_golden(path: str):
    """Map the golden JSONL into the Agent Evaluation schema
    (request + expected_response). Pure — unit-testable without a workspace."""
    import pandas as pd
    rows = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            ex = json.loads(line)
            rows.append({
                "request": {"messages": [{"role": "user", "content": ex["adjuster_query"]}]},
                "expected_response": ex.get("reference_answer", ""),
            })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Mosaic AI Agent Evaluation for the served agent")
    ap.add_argument("--model-name", required=True)
    ap.add_argument("--version", default=None, help="model version (default: @current alias)")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--golden", default=os.path.join(_REPO, "eval", "golden_dataset.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    import mlflow
    mlflow.set_tracking_uri(f"databricks://{a.profile}" if a.profile else "databricks")

    df = load_golden(a.golden)
    if a.limit:
        df = df.head(a.limit)
    model_uri = (f"models:/{a.model_name}/{a.version}" if a.version
                 else f"models:/{a.model_name}@current")
    print(f"Evaluating {model_uri} on {len(df)} examples (Mosaic AI Agent Evaluation)…")
    results = mlflow.evaluate(model=model_uri, data=df, model_type="databricks-agent")
    print("\nMetrics:")
    for k, v in (results.metrics or {}).items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
