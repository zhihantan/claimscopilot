#!/usr/bin/env python3
"""Build (and optionally deploy) the ClaimsCopilot "Agent Operations" Lakeview
dashboard.

Datasets come from the App's audit tables (`<catalog>.app.decision_log`,
`app.feedback`, `app.escalation`) — populated as adjusters use the app (Approve
& Log, thumbs/rating, escalations). Widgets: decision volume + mix, adjuster
concurrence, feedback, escalations by reason. (When AI Gateway is enabled, its
inference/usage table adds cost/latency — a natural follow-on dataset.)

    # 1) write the dashboard JSON artifact (no workspace needed)
    python scripts/build_dashboard.py --catalog <catalog>

    # 2) create it in the workspace
    python scripts/build_dashboard.py --catalog <catalog> --warehouse-id <id> \
        --profile <p> --apply

The committed resources/claimscopilot_dashboard.lvdash.json is generated with a
__CATALOG__ placeholder (importable via the Lakeview UI after substitution); the
deploy path regenerates with your real catalog.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # vendored builder
from lakeview_builder import LakeviewDashboard  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT = os.path.join(_REPO, "resources", "claimscopilot_dashboard.lvdash.json")
_TITLE = "ClaimsCopilot — Agent Operations"


def build(catalog: str) -> str:
    """Construct the dashboard and return its serialized JSON."""
    app = f"{catalog}.app"
    d = LakeviewDashboard(_TITLE)
    d.add_dataset("decisions", "Decisions",
                  f"SELECT created_at, agent_recommendation, "
                  f"COALESCE(adjuster_concurred, false) AS concurred, model, agent_version "
                  f"FROM {app}.decision_log")
    d.add_dataset("feedback", "Feedback",
                  f"SELECT created_at, thumbs, rating FROM {app}.feedback")
    d.add_dataset("escalations", "Escalations",
                  f"SELECT created_at, reason, queue FROM {app}.escalation")

    d.add_counter("decisions", "agent_recommendation", value_agg="COUNT",
                  title="Decisions logged", position={"x": 0, "y": 0, "width": 2, "height": 3})
    d.add_counter("escalations", "reason", value_agg="COUNT",
                  title="Escalations", position={"x": 2, "y": 0, "width": 2, "height": 3})
    d.add_counter("feedback", "rating", value_agg="AVG",
                  title="Avg feedback rating", position={"x": 4, "y": 0, "width": 2, "height": 3})

    d.add_line_chart("decisions", x_field="created_at", y_field="agent_recommendation",
                     y_agg="COUNT", time_grain="DAY", title="Decisions per day",
                     position={"x": 0, "y": 3, "width": 6, "height": 6})
    d.add_bar_chart("decisions", x_field="agent_recommendation", y_field="agent_recommendation",
                    y_agg="COUNT", title="Decision mix", sort_descending=True,
                    position={"x": 0, "y": 9, "width": 3, "height": 6})
    d.add_pie_chart("escalations", angle_field="reason", color_field="reason",
                    angle_agg="COUNT", title="Escalations by reason",
                    position={"x": 3, "y": 9, "width": 3, "height": 6})
    return d.to_json()


def main() -> int:
    ap = argparse.ArgumentParser(description="Build/deploy the ClaimsCopilot ops dashboard")
    ap.add_argument("--catalog", default="__CATALOG__",
                    help="catalog holding the app schema (default: __CATALOG__ placeholder)")
    ap.add_argument("--out", default=_OUT)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--warehouse-id", default=None)
    ap.add_argument("--apply", action="store_true", help="create the dashboard in the workspace")
    a = ap.parse_args()

    payload = build(a.catalog)
    with open(a.out, "w") as f:
        f.write(payload)
    print(f"wrote dashboard JSON -> {a.out} ({len(payload)} bytes, catalog={a.catalog})")

    if not a.apply:
        print("(no --apply: not created in the workspace.)")
        return 0
    if not a.warehouse_id:
        print("--warehouse-id is required with --apply")
        return 1

    from databricks.sdk import WorkspaceClient
    ws = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()
    email = ws.current_user.me().user_name
    resp = ws.api_client.do("POST", "/api/2.0/lakeview/dashboards", body={
        "display_name": _TITLE,
        "warehouse_id": a.warehouse_id,
        "parent_path": f"/Users/{email}",
        "serialized_dashboard": payload,
    })
    print(f"✓ created dashboard: id={resp.get('dashboard_id')} (open it in the Dashboards UI)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
