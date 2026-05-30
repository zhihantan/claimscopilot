"""Workspace readiness checks for ClaimsCopilot.

Probes the dependencies the app needs (chat/embedding serving endpoints, SQL
warehouse, Unity Catalog catalogs + schemas, Vector Search endpoint, MLflow
experiment) and reports what's present vs. missing. Shared by:

  - `/api/readiness` (backend/main.py) — so the UI can show a "run setup" banner
    instead of failing silently when a customer hasn't bootstrapped yet, and
  - `scripts/preflight.py` — a pre-install check a customer runs against their
    workspace before deploying.

All checks are best-effort and individually guarded: a probe failure becomes a
failed check, never an exception. `ready` is True only when every *critical*
check passes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class Check:
    name: str
    ok: bool
    critical: bool
    detail: str


# The UC schemas setup/01_ddl.sql creates (split across catalog_lake/catalog_ai;
# in a single-catalog install they all live in one).
_EXPECTED_SCHEMAS = {
    "policy", "claims", "devices", "repairs", "partners",
    "kb", "tools", "app", "eval", "indexes", "models",
}


def check_readiness(settings: Any, ws: Optional[Any] = None) -> dict:
    """Run all readiness probes and return {"ready": bool, "checks": [...]}.

    `settings` needs the attributes used below (the real Settings or any
    namespace works). `ws` is an optional WorkspaceClient (the CLI passes one
    built from a profile; the app passes None → App SP creds).
    """
    if ws is None:
        from databricks.sdk import WorkspaceClient
        ws = WorkspaceClient()

    checks: list[Check] = []

    # --- serving endpoints (chat primary is critical; others are fallbacks) ---
    try:
        names = {e.name for e in ws.serving_endpoints.list()}
        for label, ep, crit in [
            ("chat_primary", settings.chat_endpoint_primary, True),
            ("chat_fallback_1", settings.chat_endpoint_fallback_1, False),
            ("chat_fallback_2", settings.chat_endpoint_fallback_2, False),
            ("embed_multilingual", settings.embed_endpoint_multilingual, False),
        ]:
            present = ep in names
            checks.append(Check(f"serving:{label}", present, crit,
                                f"{ep} {'found' if present else 'MISSING'}"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("serving_endpoints", False, True, f"list failed: {e}"))

    # --- SQL warehouse (critical: tools + session reads need it) ---
    try:
        wh = ws.warehouses.get(id=settings.databricks_warehouse_id)
        checks.append(Check("warehouse", True, True, f"{wh.name} (state={wh.state})"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("warehouse", False, True,
                            f"{settings.databricks_warehouse_id}: {e}"))

    # --- Unity Catalog: catalogs exist (critical) ---
    catalogs = []
    for label, cat in [("catalog_lake", settings.catalog_lake),
                       ("catalog_ai", settings.catalog_ai)]:
        if cat in catalogs:
            continue
        catalogs.append(cat)
        try:
            ws.catalogs.get(name=cat)
            checks.append(Check(f"{label}", True, True, f"{cat} found"))
        except Exception as e:  # noqa: BLE001
            checks.append(Check(f"{label}", False, True, f"{cat} MISSING ({e})"))

    # --- expected schemas present (critical: signals DDL has been run) ---
    try:
        present_schemas: set[str] = set()
        for cat in catalogs:
            try:
                present_schemas |= {s.name for s in ws.schemas.list(catalog_name=cat)}
            except Exception:  # noqa: BLE001
                pass
        missing = sorted(_EXPECTED_SCHEMAS - present_schemas)
        checks.append(Check("schemas", not missing, True,
                            "all present" if not missing else f"missing: {missing} — run setup/01_ddl.sql"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("schemas", False, True, f"schema probe failed: {e}"))

    # --- Vector Search endpoint (non-critical: retrieval tools degrade) ---
    try:
        ws.vector_search_endpoints.get_endpoint(endpoint_name=settings.vs_endpoint)
        checks.append(Check("vector_search_endpoint", True, False, f"{settings.vs_endpoint} found"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("vector_search_endpoint", False, False,
                            f"{settings.vs_endpoint} MISSING ({e})"))

    # --- MLflow experiment (info: auto-created on first use) ---
    try:
        import mlflow
        exp = mlflow.get_experiment_by_name(settings.mlflow_experiment)
        checks.append(Check("mlflow_experiment", exp is not None, False,
                            f"{settings.mlflow_experiment} {'found' if exp else 'will be auto-created on first run'}"))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("mlflow_experiment", True, False, f"skipped ({e})"))

    ready = all(c.ok for c in checks if c.critical)
    return {"ready": ready, "checks": [asdict(c) for c in checks]}
