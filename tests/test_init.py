"""Tests for scripts/init.py value-substitution helpers, including against the
REAL app.yaml + databricks.yml so format drift (which would silently make init
a no-op) is caught.
"""

from __future__ import annotations

from scripts.init import (
    _APP_YAML,
    _BUNDLE,
    set_app_env,
    set_indented_value,
    set_resource_field,
)


def test_set_app_env_replaces_only_target():
    text = (
        '  - name: CC_CATALOG_LAKE\n    value: "old"\n'
        '  - name: CC_REGION\n    value: "EMEA"\n'
    )
    out, n = set_app_env(text, "CC_CATALOG_LAKE", "acme")
    assert n == 1
    assert 'value: "acme"' in out
    assert 'value: "EMEA"' in out  # other entry untouched


def test_set_app_env_missing_key_is_noop():
    out, n = set_app_env('  - name: X\n    value: "y"\n', "CC_NOPE", "z")
    assert n == 0


def test_set_indented_value_replaces_dev_var():
    text = '    variables:\n      catalog_lake: "old"\n      warehouse_id: "w"\n'
    out, n = set_indented_value(text, "catalog_lake", "acme")
    assert n == 1 and 'catalog_lake: "acme"' in out
    assert 'warehouse_id: "w"' in out


def test_real_app_yaml_has_expected_keys():
    txt = open(_APP_YAML).read()
    for k in ("CC_CATALOG_LAKE", "CC_CATALOG_AI", "CC_WAREHOUSE_ID",
              "CC_VS_ENDPOINT", "CC_MLFLOW_EXPERIMENT", "CC_REGION", "CC_SYSTEM_CANARY"):
        _, n = set_app_env(txt, k, "x")
        assert n == 1, f"{k} not found in app.yaml — init would silently skip it"


def test_real_bundle_dev_target_editable():
    txt = open(_BUNDLE).read()
    for k in ("catalog_lake", "catalog_ai", "warehouse_id"):
        _, n = set_indented_value(txt, k, "x")
        assert n == 1, f"dev var {k} not found in databricks.yml"


def test_real_app_yaml_resource_fields_editable():
    """The App's access-grant resources (warehouse id + VS endpoint name) must be
    rewritable so the template binds to the customer's resources, not ours."""
    txt = open(_APP_YAML).read()
    _, nw = set_resource_field(txt, "sql_warehouse", "id", "x")
    assert nw == 1, "sql_warehouse.id not found in app.yaml resources"
    _, nv = set_resource_field(txt, "vector_search_endpoint", "name", "x")
    assert nv == 1, "vector_search_endpoint.name not found in app.yaml resources"
