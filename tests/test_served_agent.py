"""Offline checks for the served-agent path: the resource specs that grant the
served model auth, and the golden->Agent-Evaluation schema mapping. (Logging,
serving, and the LLM-judge eval are validated live.)"""

from __future__ import annotations

import os

from eval.run_agent_eval import load_golden
from scripts.register_agent import build_resources

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHAT = ["databricks-claude-opus-4-8", "databricks-claude-sonnet-4-6",
         "databricks-meta-llama-3-3-70b-instruct"]


def test_build_resources_counts_and_catalog_substitution():
    res = build_resources("acme", "acme_ai", "wh-123", _CHAT, "databricks-qwen3-embedding-0-6b")
    kinds = [type(r).__name__ for r in res]
    assert kinds.count("DatabricksServingEndpoint") == 4  # 3 chat + 1 embed
    assert kinds.count("DatabricksFunction") == 9          # the UC tool functions
    assert kinds.count("DatabricksVectorSearchIndex") == 3
    assert kinds.count("DatabricksSQLWarehouse") == 1
    # the catalog flows into the specs: a different catalog yields different specs
    res2 = build_resources("zzz", "zzz_ai", "wh-123", _CHAT, "databricks-qwen3-embedding-0-6b")
    assert str(res) != str(res2)


def test_load_golden_maps_to_agent_eval_schema():
    df = load_golden(os.path.join(_REPO, "eval", "golden_dataset.jsonl"))
    assert len(df) >= 20
    row = df.iloc[0]
    assert row["request"]["messages"][0]["role"] == "user"
    assert isinstance(row["request"]["messages"][0]["content"], str)
    assert "expected_response" in df.columns
