"""Execute a multi-statement SQL file against a Databricks SQL warehouse.

Splits on `;` boundaries (UC SQL functions in this repo are single statements
with parenthesized bodies, so naive splitting is safe). Reports per-statement
success/failure. Continues on error by default — pass --fail-fast to abort.

Usage:
  python scripts/run_sql_file.py setup/01_ddl.sql \\
      --profile <profile> --warehouse-id <warehouse-id>
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    ExecuteStatementRequestOnWaitTimeout,
    StatementState,
)

_COMMENT_LINE = re.compile(r"^\s*--.*$", flags=re.MULTILINE)


def _split_statements(sql_text: str) -> list[str]:
    body = _COMMENT_LINE.sub("", sql_text)
    parts = [s.strip() for s in body.split(";")]
    return [s for s in parts if s]


def _preview(stmt: str, n: int = 90) -> str:
    flat = " ".join(stmt.split())
    return flat[:n] + ("…" if len(flat) > n else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sql_file", type=Path)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--warehouse-id", required=True)
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args()

    ws = WorkspaceClient(profile=args.profile)
    text = args.sql_file.read_text()
    statements = _split_statements(text)
    print(f"[run_sql_file] {len(statements)} statements in {args.sql_file}")

    errors: list[tuple[int, str, str]] = []
    for i, stmt in enumerate(statements, 1):
        t0 = time.perf_counter()
        try:
            resp = ws.statement_execution.execute_statement(
                statement=stmt,
                warehouse_id=args.warehouse_id,
                wait_timeout="30s",
                on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
            )
            state = resp.status.state if resp.status else StatementState.FAILED
            ms = int((time.perf_counter() - t0) * 1000)
            if state == StatementState.SUCCEEDED:
                print(f"  [{i:3d}/{len(statements)}] ok    {ms:5d}ms  {_preview(stmt)}")
            else:
                msg = (resp.status.error.message if resp.status and resp.status.error else str(state))
                print(f"  [{i:3d}/{len(statements)}] FAIL  {ms:5d}ms  {_preview(stmt)}\n         → {msg}")
                errors.append((i, _preview(stmt), msg))
                if args.fail_fast:
                    break
        except Exception as e:  # noqa: BLE001
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"  [{i:3d}/{len(statements)}] FAIL  {ms:5d}ms  {_preview(stmt)}\n         → {e}")
            errors.append((i, _preview(stmt), str(e)))
            if args.fail_fast:
                break

    print(f"\n[run_sql_file] done — {len(statements) - len(errors)} ok, {len(errors)} failed")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
