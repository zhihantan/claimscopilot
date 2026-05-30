#!/usr/bin/env python3
"""One-command bootstrap: take a Databricks workspace from empty to
ClaimsCopilot-ready. Idempotent and parameterized — safe to re-run.

    python scripts/bootstrap.py --profile <profile> --warehouse-id <id> \
        --catalog <your_catalog> [--vs-endpoint <name>] [--seed-mode if-empty]

Steps (each idempotent + individually skippable):
  1. catalog      — ensure the UC catalog exists (create if you have privilege)
  2. ddl          — run setup/01_ddl.sql (catalog-substituted): schemas/tables/functions
  3. secret-scope — create the secret scope + seed a system canary (only if new)
  4. mlflow       — ensure the MLflow experiment exists
  5. vector-search— ensure the VS endpoint + the 3 Delta-sync indexes exist
  6. seed         — load synthetic data (guarded by --seed-mode: if-empty|replace|skip)

Finishes by running the readiness probe and printing READY / NOT READY.
Use --dry-run to preview the plan + readiness without changing anything.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.sql import (  # noqa: E402
    ExecuteStatementRequestOnWaitTimeout,
    StatementState,
)

from backend.readiness import check_readiness  # noqa: E402
from scripts.run_sql_file import _split_statements  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DDL_PATH = os.path.join(_REPO, "setup", "01_ddl.sql")
_DDL_CATALOG_TOKEN = "__CATALOG__"  # the placeholder the DDL ships with

_SEEDED_TABLES = [  # catalog-relative; truncated on --seed-mode replace
    "policy.policy", "policy.coverage", "policy.wording_document", "policy.wording_chunk",
    "claims.claim", "claims.narrative_anon", "devices.device",
    "partners.partner_account", "kb.article", "kb.article_chunk",
]


def log(step: str, msg: str) -> None:
    print(f"[bootstrap:{step}] {msg}", flush=True)


def substitute_catalog(sql_text: str, catalog: str) -> str:
    """Rewrite the DDL's shipped catalog token to the target catalog."""
    return sql_text.replace(_DDL_CATALOG_TOKEN, catalog)


def _exec(ws: WorkspaceClient, warehouse_id: str, stmt: str):
    return ws.statement_execution.execute_statement(
        statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )


def _scalar(ws: WorkspaceClient, warehouse_id: str, sql: str):
    resp = _exec(ws, warehouse_id, sql)
    if resp.status and resp.status.state == StatementState.SUCCEEDED and resp.result:
        rows = resp.result.data_array or []
        if rows:
            return rows[0][0]
    return None


# ---- steps ------------------------------------------------------------------

def ensure_catalog(ws, args) -> None:
    for cat in dict.fromkeys([args.catalog, args.catalog_ai]):
        try:
            ws.catalogs.get(name=cat)
            log("catalog", f"{cat} already exists — skip create")
        except Exception:  # noqa: BLE001 — not found (or no read); try to create
            try:
                _exec(ws, args.warehouse_id, f"CREATE CATALOG IF NOT EXISTS {cat}")
                log("catalog", f"{cat} created")
            except Exception as e:  # noqa: BLE001
                log("catalog", f"could NOT create {cat}: {e}\n"
                              f"          → create it manually (or ask an admin) and re-run.")
                raise


def run_ddl(ws, args) -> None:
    sql = substitute_catalog(open(_DDL_PATH).read(), args.catalog)
    statements = _split_statements(sql)
    ok = 0
    errors = 0
    for stmt in statements:
        try:
            resp = _exec(ws, args.warehouse_id, stmt)
            if resp.status and resp.status.state == StatementState.SUCCEEDED:
                ok += 1
            else:
                errors += 1
                msg = resp.status.error.message if resp.status and resp.status.error else resp.status.state
                log("ddl", f"FAIL: {msg} :: {' '.join(stmt.split())[:80]}")
        except Exception as e:  # noqa: BLE001
            errors += 1
            log("ddl", f"FAIL: {e} :: {' '.join(stmt.split())[:80]}")
    log("ddl", f"{ok} statements ok, {errors} failed")
    if errors:
        raise RuntimeError(f"DDL had {errors} failures")


def ensure_secret_scope(ws, args) -> None:
    scopes = {s.name for s in ws.secrets.list_scopes()}
    if args.secret_scope in scopes:
        log("secret-scope", f"{args.secret_scope} exists — leaving secrets as-is")
        return
    ws.secrets.create_scope(scope=args.secret_scope)
    ws.secrets.put_secret(scope=args.secret_scope, key="system_canary",
                          string_value=secrets.token_hex(8))
    ws.secrets.put_secret(scope=args.secret_scope, key="warehouse_id",
                          string_value=args.warehouse_id)
    log("secret-scope", f"created {args.secret_scope} + seeded system_canary, warehouse_id")


def ensure_mlflow(ws, args) -> None:
    import mlflow
    mlflow.set_tracking_uri(f"databricks://{args.profile}" if args.profile else "databricks")
    if mlflow.get_experiment_by_name(args.mlflow_experiment) is None:
        mlflow.create_experiment(args.mlflow_experiment)
        log("mlflow", f"created experiment {args.mlflow_experiment}")
    else:
        log("mlflow", f"{args.mlflow_experiment} exists")


def ensure_vector_search(ws, args) -> None:
    from databricks.vector_search.client import VectorSearchClient

    try:
        ws.vector_search_endpoints.get_endpoint(endpoint_name=args.vs_endpoint)
        log("vector-search", f"endpoint {args.vs_endpoint} exists")
    except Exception:  # noqa: BLE001
        log("vector-search", f"creating endpoint {args.vs_endpoint} (STANDARD)…")
        ws.vector_search_endpoints.create_endpoint_and_wait(
            name=args.vs_endpoint, endpoint_type="STANDARD")

    bearer = ws.config.authenticate().get("Authorization", "").removeprefix("Bearer ")
    vsc = VectorSearchClient(workspace_url=ws.config.host,
                             personal_access_token=bearer, disable_notice=True)
    existing = {i["name"] for i in vsc.list_indexes(name=args.vs_endpoint).get("vector_indexes", [])}

    specs = [
        (f"{args.catalog_ai}.indexes.policy_wordings", f"{args.catalog}.policy.wording_chunk", "chunk_id", "text", False),
        (f"{args.catalog_ai}.indexes.adjuster_kb", f"{args.catalog}.kb.article_chunk", "chunk_id", "text", False),
        (f"{args.catalog_ai}.indexes.claim_narratives", f"{args.catalog}.claims.narrative_anon", "claim_id_anon", "narrative_anon", True),
    ]
    for name, source, pk, text_col, needs_dlt in specs:
        if needs_dlt and args.skip_claim_narratives:
            log("vector-search", f"SKIP {name} (waiting for DLT-produced source)")
            continue
        if name in existing:
            log("vector-search", f"index {name} exists")
            continue
        log("vector-search", f"creating index {name}…")
        vsc.create_delta_sync_index_and_wait(
            endpoint_name=args.vs_endpoint, index_name=name, source_table_name=source,
            primary_key=pk, embedding_source_column=text_col,
            embedding_model_endpoint_name=args.embed_model, pipeline_type="TRIGGERED",
        )
        log("vector-search", f"created {name}")


def run_seed(ws, args) -> None:
    if args.seed_mode == "skip":
        log("seed", "skipped (--seed-mode skip)")
        return
    claim_tbl = f"{args.catalog}.claims.claim"
    count = _scalar(ws, args.warehouse_id, f"SELECT COUNT(*) FROM {claim_tbl}")
    count = int(count or 0)
    if count > 0 and args.seed_mode == "if-empty":
        log("seed", f"{claim_tbl} already has {count} rows — skip (use --seed-mode replace to reseed)")
        return
    if count > 0 and args.seed_mode == "replace":
        log("seed", f"replacing: truncating {len(_SEEDED_TABLES)} tables…")
        for t in _SEEDED_TABLES:
            try:
                _exec(ws, args.warehouse_id, f"TRUNCATE TABLE {args.catalog}.{t}")
            except Exception as e:  # noqa: BLE001
                log("seed", f"  truncate {t} failed (ignoring): {e}")

    from data import seed_synthetic as seedmod
    seed_args = seedmod.Args(
        rows=args.rows, seed=args.seed, languages=args.languages,
        catalog_lake=args.catalog, catalog_ai=args.catalog_ai,
        warehouse_id=args.warehouse_id, workspace_host=ws.config.host, token=None,
    )
    log("seed", f"generating {args.rows} rows (seed={args.seed}, langs={args.languages})…")
    data = seedmod.gen(seed_args)
    seedmod.write_to_uc(seed_args, data, ws=ws)
    log("seed", "done")


# ---- runner -----------------------------------------------------------------

def _settings_ns(args):
    return SimpleNamespace(
        chat_endpoint_primary=args.chat_primary,
        chat_endpoint_fallback_1=args.chat_fb1,
        chat_endpoint_fallback_2=args.chat_fb2,
        embed_endpoint_multilingual=args.embed_model,
        databricks_warehouse_id=args.warehouse_id,
        catalog_lake=args.catalog, catalog_ai=args.catalog_ai,
        vs_endpoint=args.vs_endpoint, mlflow_experiment=args.mlflow_experiment,
    )


def _print_readiness(report) -> None:
    icon = {True: "✓"}
    for c in report["checks"]:
        mark = "✓" if c["ok"] else ("✗" if c["critical"] else "⚠")
        print(f"  {mark}  {c['name']:<28} {c['detail']}")


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap a workspace for ClaimsCopilot")
    p.add_argument("--profile", default=None)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--catalog-ai", default=None, help="defaults to --catalog (single-catalog install)")
    p.add_argument("--vs-endpoint", default="claimscopilot_vs")
    p.add_argument("--embed-model", default="databricks-qwen3-embedding-0-6b")
    p.add_argument("--secret-scope", default="claimscopilot")
    p.add_argument("--mlflow-experiment", default="/Shared/claimscopilot/dev")
    p.add_argument("--rows", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--languages", default="en,es,ja")
    p.add_argument("--seed-mode", choices=["if-empty", "replace", "skip"], default="if-empty")
    p.add_argument("--skip-claim-narratives", action="store_true",
                   help="defer the claim_narratives index until the DLT pipeline runs")
    p.add_argument("--chat-primary", default="databricks-claude-opus-4-8")
    p.add_argument("--chat-fb1", default="databricks-claude-sonnet-4-6")
    p.add_argument("--chat-fb2", default="databricks-meta-llama-3-3-70b-instruct")
    for s in ["ddl", "secret", "mlflow", "vs", "seed"]:
        p.add_argument(f"--skip-{s}", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="preview plan + readiness only")
    a = p.parse_args()
    a.catalog_ai = a.catalog_ai or a.catalog
    a.languages = a.languages.split(",")

    ws = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()

    # Point MLflow at the same workspace so the readiness experiment probe is
    # accurate (both in --dry-run and the final check).
    try:
        import mlflow
        mlflow.set_tracking_uri(f"databricks://{a.profile}" if a.profile else "databricks")
    except Exception:  # noqa: BLE001
        pass

    print(f"\nBootstrap plan → catalog={a.catalog} (ai={a.catalog_ai}), warehouse={a.warehouse_id}, "
          f"vs={a.vs_endpoint}, scope={a.secret_scope}, seed-mode={a.seed_mode}\n")

    if a.dry_run:
        log("dry-run", "no changes will be made. Current readiness:")
        _print_readiness(check_readiness(_settings_ns(a), ws=ws))
        return 0

    steps = [
        ("catalog", lambda: ensure_catalog(ws, a), True),
        ("ddl", lambda: run_ddl(ws, a), not a.skip_ddl),
        ("secret-scope", lambda: ensure_secret_scope(ws, a), not a.skip_secret),
        ("mlflow", lambda: ensure_mlflow(ws, a), not a.skip_mlflow),
        ("vector-search", lambda: ensure_vector_search(ws, a), not a.skip_vs),
        ("seed", lambda: run_seed(ws, a), not a.skip_seed),
    ]
    for name, fn, enabled in steps:
        if not enabled:
            log(name, "skipped")
            continue
        t0 = time.perf_counter()
        fn()
        log(name, f"({int((time.perf_counter() - t0) * 1000)}ms)")

    print("\nFinal readiness:")
    report = check_readiness(_settings_ns(a), ws=ws)
    _print_readiness(report)
    print("\n" + ("✓ READY — deploy with: databricks bundle deploy -t dev "
                   f"--var warehouse_id={a.warehouse_id}"
                   if report["ready"] else "✗ NOT READY — see ✗ items above."))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
