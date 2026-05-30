"""Shared fixtures for the ClaimsCopilot test suite.

Sets the CC_* env vars required by `backend.agent.config.Settings` so import
of `backend.main` doesn't blow up when tests run in a clean shell. Stub
values only — these tests never reach a real Databricks workspace.
"""

from __future__ import annotations

import os

_STUB_ENV = {
    "CC_APP_ENV": "dev",
    "CC_AGENT_VERSION": "v0.3.3-test",
    "CC_REGION": "EMEA",
    "CC_ENABLED": "true",
    "DATABRICKS_HOST": "https://example.cloud.databricks.com",
    "CC_WAREHOUSE_ID": "stub-warehouse-id",
    "CC_CATALOG_LAKE": "main",
    "CC_CATALOG_AI": "main",
    "CC_SYSTEM_CANARY": "stub-canary-0123456789abcdef",
    "CC_MLFLOW_EXPERIMENT": "/local/claimscopilot/test",
    "MLFLOW_TRACKING_URI": "file:./.mlruns-test",
    "TOKENIZERS_PARALLELISM": "false",
}

for k, v in _STUB_ENV.items():
    os.environ.setdefault(k, v)
