"""Regression for the FMAPI 400: Databricks' Claude endpoint maps OpenAI
system-role messages onto Anthropic's single top-level `system` field and
returns 400 Bad Request on multiple system messages. The synthesize prompt sent
four, so every answer turn 400'd. `_consolidate_messages` merges them into one.
"""

from __future__ import annotations

from backend.agent.agent import _consolidate_messages


def test_merges_multiple_system_messages_into_one_leading():
    msgs = [
        {"role": "system", "content": "A"},
        {"role": "system", "content": "B"},
        {"role": "system", "content": "C"},
        {"role": "system", "content": "D"},
        {"role": "user", "content": "Q"},
    ]
    out = _consolidate_messages(msgs)
    assert [m["role"] for m in out] == ["system", "user"]  # exactly one system
    assert out[0]["content"] == "A\n\nB\n\nC\n\nD"          # order preserved
    assert out[1]["content"] == "Q"


def test_single_system_is_unchanged():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert _consolidate_messages(msgs) == msgs


def test_no_system_message_passes_through():
    msgs = [{"role": "user", "content": "U"}]
    assert _consolidate_messages(msgs) == msgs


def test_preserves_non_system_order_and_merges_scattered_systems():
    msgs = [
        {"role": "system", "content": "S1"},
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "system", "content": "S2"},
        {"role": "user", "content": "U2"},
    ]
    out = _consolidate_messages(msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[0]["content"] == "S1\n\nS2"
    assert [m["content"] for m in out[1:]] == ["U1", "A1", "U2"]
