"""Tests for the bootstrap's catalog substitution — the mechanism that makes
setup/01_ddl.sql (which ships with a `__CATALOG__` placeholder) install into
any customer catalog. The live workspace steps are exercised by `--dry-run`.
"""

from __future__ import annotations

from scripts.bootstrap import _DDL_PATH, substitute_catalog


def test_substitute_catalog_rewrites_references():
    sql = (
        "CREATE SCHEMA IF NOT EXISTS __CATALOG__.policy;\n"
        "SELECT __CATALOG__.tools.get_claim(?) AS r;"
    )
    out = substitute_catalog(sql, "acme_cat")
    assert "__CATALOG__" not in out
    assert "acme_cat.policy" in out
    assert "acme_cat.tools.get_claim" in out


def test_real_ddl_fully_substituted():
    """The shipped DDL must contain NO residual placeholder after rewrite, and
    no stranger's catalog name in the public repo."""
    out = substitute_catalog(open(_DDL_PATH).read(), "acme_cat")
    assert "__CATALOG__" not in out
    assert "zh_stable_catalog" not in out
    assert "acme_cat.policy.policy" in out  # spot-check a known table
    assert "acme_cat.tools." in out          # and the function schema
