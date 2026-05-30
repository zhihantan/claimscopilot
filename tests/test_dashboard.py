"""The ops Lakeview dashboard builds to valid JSON with the expected datasets +
widgets, and the catalog substitutes in. (Visual rendering is validated at
deploy — `build_dashboard.py --apply`.)"""

from __future__ import annotations

import json

from scripts.build_dashboard import _OUT, build

_EXPECTED_DATASETS = {"decisions", "feedback", "escalations"}


def test_build_substitutes_catalog_into_queries():
    payload = build("acme_cat")
    assert "acme_cat.app.decision_log" in payload
    assert "acme_cat.app.feedback" in payload
    assert "acme_cat.app.escalation" in payload
    assert "__CATALOG__" not in payload
    d = json.loads(payload)
    assert {ds["name"] for ds in d["datasets"]} == _EXPECTED_DATASETS


def test_committed_artifact_is_valid_template():
    d = json.load(open(_OUT))
    assert {ds["name"] for ds in d["datasets"]} == _EXPECTED_DATASETS
    widgets = sum(len(p.get("layout", [])) for p in d["pages"])
    assert widgets == 6
    # the committed artifact is the importable template → placeholder catalog
    assert "__CATALOG__" in json.dumps(d)
