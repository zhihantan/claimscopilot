#!/usr/bin/env python3
"""Configure a cloned ClaimsCopilot for YOUR Databricks workspace.

Run once after cloning. It fills the deployment-specific values into `app.yaml`
(the App's runtime env) and the `dev` target of `databricks.yml`, and generates
a fresh system canary. Then: preflight -> bootstrap -> deploy.

    python scripts/init.py \
        --catalog my_catalog --warehouse-id <id> --region EMEA \
        --vs-endpoint claimscopilot_vs --write

The `dev` target is host-less, so it deploys to whatever workspace your CLI
profile points at — no host needed here; just authenticate the right profile.

Missing values are prompted for when run interactively. Use --print to preview
the changes without writing (nothing is modified). --write backs up each file
to <file>.bak before editing.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_YAML = os.path.join(_REPO, "app.yaml")
_BUNDLE = os.path.join(_REPO, "databricks.yml")


def set_app_env(text: str, key: str, value: str) -> tuple[str, int]:
    """Replace the quoted value of an `- name: KEY / value: "..."` env entry."""
    pat = re.compile(rf'(- name: {re.escape(key)}\n\s*value: )"[^"]*"')
    return pat.subn(lambda m: f'{m.group(1)}"{value}"', text, count=1)


def set_indented_value(text: str, key: str, value: str, indent: int = 6) -> tuple[str, int]:
    """Replace a quoted `<indent spaces>key: "..."` scalar (the dev target's
    variables + host live at 6-space indent)."""
    pat = re.compile(rf'(\n{" " * indent}{re.escape(key)}: )"[^"]*"')
    return pat.subn(lambda m: f'{m.group(1)}"{value}"', text, count=1)


def set_resource_field(text: str, parent_key: str, field: str, value: str) -> tuple[str, int]:
    """Replace `field: "..."` directly under a `parent_key:` mapping in the
    app.yaml resources block (e.g. sql_warehouse.id, vector_search_endpoint.name)."""
    pat = re.compile(rf'({re.escape(parent_key)}:\n\s*{re.escape(field)}: )"[^"]*"')
    return pat.subn(lambda m: f'{m.group(1)}"{value}"', text, count=1)


def _prompt(label: str, default: str | None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or (default or "")


def main() -> int:
    p = argparse.ArgumentParser(description="Configure ClaimsCopilot for your workspace")
    p.add_argument("--catalog")
    p.add_argument("--catalog-ai")
    p.add_argument("--warehouse-id")
    p.add_argument("--region", choices=["EMEA", "APAC", "AMER"])
    p.add_argument("--vs-endpoint", default="claimscopilot_vs")
    p.add_argument("--mlflow-experiment", default="/Shared/claimscopilot")
    p.add_argument("--secret-scope", default="claimscopilot")
    p.add_argument("--chat-primary", default="databricks-claude-opus-4-8")
    p.add_argument("--embed-ml", default="databricks-qwen3-embedding-0-6b")
    p.add_argument("--print", dest="dry", action="store_true", help="preview only; write nothing")
    p.add_argument("--write", action="store_true", help="apply changes (backs up to .bak)")
    a = p.parse_args()

    interactive = sys.stdin.isatty()
    for field, label in [("catalog", "UC catalog"),
                         ("warehouse_id", "SQL warehouse id"), ("region", "Region (EMEA/APAC/AMER)")]:
        if not getattr(a, field):
            if interactive:
                setattr(a, field, _prompt(label, "EMEA" if field == "region" else None))
            else:
                p.error(f"--{field.replace('_', '-')} is required (or run interactively)")
    a.catalog_ai = a.catalog_ai or a.catalog
    canary = secrets.token_hex(8)

    app_env = {
        "CC_CATALOG_LAKE": a.catalog, "CC_CATALOG_AI": a.catalog_ai,
        "CC_WAREHOUSE_ID": a.warehouse_id, "CC_VS_ENDPOINT": a.vs_endpoint,
        "CC_MLFLOW_EXPERIMENT": a.mlflow_experiment, "CC_REGION": a.region,
        "CC_SYSTEM_CANARY": canary, "CC_CHAT_PRIMARY": a.chat_primary,
        "CC_EMBED_ML": a.embed_ml,
    }
    dev_vars = {"catalog_lake": a.catalog, "catalog_ai": a.catalog_ai,
                "warehouse_id": a.warehouse_id}

    app_text = open(_APP_YAML).read()
    changes: list[str] = []
    for k, v in app_env.items():
        app_text, n = set_app_env(app_text, k, v)
        changes.append(f"  app.yaml      env {k:<20} = {'<generated>' if k == 'CC_SYSTEM_CANARY' else v}"
                       + ("" if n else "   (KEY NOT FOUND — skipped)"))
    # resources block (the App's access grants) — warehouse id + VS endpoint name
    for parent, field, val, label in [
        ("sql_warehouse", "id", a.warehouse_id, "resources warehouse.id"),
        ("vector_search_endpoint", "name", a.vs_endpoint, "resources vs.name"),
    ]:
        app_text, n = set_resource_field(app_text, parent, field, val)
        changes.append(f"  app.yaml      {label:<24} = {val}" + ("" if n else "   (NOT FOUND)"))

    bundle_text = open(_BUNDLE).read()
    for k, v in dev_vars.items():
        bundle_text, n = set_indented_value(bundle_text, k, v)
        changes.append(f"  databricks.yml dev.{k:<14} = {v}" + ("" if n else "   (NOT FOUND)"))

    print("\nPlanned configuration:")
    print("\n".join(changes))

    if not a.write or a.dry:
        print("\n(--print / no --write: nothing was modified. Re-run with --write to apply.)")
        return 0

    for path, text in [(_APP_YAML, app_text), (_BUNDLE, bundle_text)]:
        with open(path + ".bak", "w") as f:
            f.write(open(path).read())
        with open(path, "w") as f:
            f.write(text)
    print(f"\n✓ Wrote app.yaml + databricks.yml (backups at *.bak).\n\nNext:\n"
          f"  python scripts/preflight.py --profile <p> --warehouse-id {a.warehouse_id} --catalog {a.catalog} --vs-endpoint {a.vs_endpoint}\n"
          f"  python scripts/bootstrap.py --profile <p> --warehouse-id {a.warehouse_id} --catalog {a.catalog} --vs-endpoint {a.vs_endpoint}\n"
          f"  databricks bundle deploy -t dev -p <p>\n"
          f"  databricks apps deploy claimscopilot --source-code-path <synced-path> -p <p>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
