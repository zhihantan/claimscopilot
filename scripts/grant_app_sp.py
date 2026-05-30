#!/usr/bin/env python3
"""Grant the ClaimsCopilot App's service principal access to its UC objects.

Run AFTER `databricks bundle deploy` (the App + its service principal must
exist). Needed only when the App runs AS a service principal — i.e. the
`stage`/`prod` targets. In `dev` mode the App runs as the deploying user, who
already owns the catalog, so grants are unnecessary.

It resolves the App SP's client id from `databricks apps get`, substitutes the
catalog + SP into setup/02_grants.sql, and (with --apply) executes each grant.

    python scripts/grant_app_sp.py --profile <p> --warehouse-id <id> \
        --catalog <catalog> --app-name claimscopilot [--apply]

Without --apply it prints the resolved grants (dry run) and changes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.sql import (  # noqa: E402
    ExecuteStatementRequestOnWaitTimeout,
    StatementState,
)

from scripts.run_sql_file import _split_statements  # noqa: E402

_GRANTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "setup", "02_grants.sql")


def main() -> int:
    ap = argparse.ArgumentParser(description="Grant the App SP access to ClaimsCopilot UC objects")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--app-name", default="claimscopilot")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--grants-file", default=_GRANTS_FILE)
    ap.add_argument("--apply", action="store_true", help="execute the grants (default: dry run)")
    a = ap.parse_args()

    ws = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()
    app = ws.apps.get(name=a.app_name)
    sp = (getattr(app, "service_principal_client_id", None)
          or getattr(app, "service_principal_name", None))
    if not sp:
        print(f"App '{a.app_name}' has no service principal — in dev mode it runs as you, "
              f"so no grants are needed.")
        return 0
    print(f"App '{a.app_name}' service principal: {sp}")

    sql = (open(a.grants_file).read()
           .replace("__CATALOG__", a.catalog)
           .replace("__APP_SP__", sp))
    statements = _split_statements(sql)

    if not a.apply:
        print(f"\n--- DRY RUN: would run {len(statements)} grants (pass --apply to execute) ---")
        for s in statements[:5]:
            print("  " + " ".join(s.split()))
        if len(statements) > 5:
            print(f"  … and {len(statements) - 5} more")
        return 0

    ok = errs = 0
    for s in statements:
        try:
            resp = ws.statement_execution.execute_statement(
                statement=s, warehouse_id=a.warehouse_id, wait_timeout="30s",
                on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL)
            if resp.status and resp.status.state == StatementState.SUCCEEDED:
                ok += 1
            else:
                errs += 1
                msg = resp.status.error.message if resp.status and resp.status.error else resp.status.state
                print(f"  FAIL: {msg}")
        except Exception as e:  # noqa: BLE001
            errs += 1
            print(f"  FAIL: {e}")
    print(f"grants: {ok} ok, {errs} failed")
    return 0 if not errs else 1


if __name__ == "__main__":
    raise SystemExit(main())
