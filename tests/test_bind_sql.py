"""Regression tests for `bind_sql` — the helper that converts positional `?`
placeholders to the named `:pN` form databricks-sdk 0.40 requires."""

from __future__ import annotations

import pytest

from backend.agent.tools import bind_sql


def test_single_placeholder():
    sql, params = bind_sql("SELECT * FROM t WHERE id = ?", ["abc"])
    assert sql == "SELECT * FROM t WHERE id = :p0"
    assert [p.name for p in params] == ["p0"]
    assert [p.value for p in params] == ["abc"]


def test_multi_placeholders_preserve_order():
    sql, params = bind_sql(
        "INSERT INTO t (a, b, c) VALUES (?, ?, ?)",
        ["x", 42, None],
    )
    assert sql == "INSERT INTO t (a, b, c) VALUES (:p0, :p1, :p2)"
    assert [p.value for p in params] == ["x", "42", None]


def test_no_placeholders():
    sql, params = bind_sql("SELECT CURRENT_TIMESTAMP()", [])
    assert sql == "SELECT CURRENT_TIMESTAMP()"
    assert params == []


def test_mismatched_count_raises():
    with pytest.raises(ValueError, match="placeholder/value count mismatch"):
        bind_sql("SELECT * FROM t WHERE a = ? AND b = ?", ["only-one"])


def test_none_passes_through_as_null():
    _, params = bind_sql("INSERT INTO t (a) VALUES (?)", [None])
    assert params[0].value is None  # SDK renders NULL when value is None
