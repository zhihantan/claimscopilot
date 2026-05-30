"""Tests for failure-mode #3: Japanese postal/address fragments that ai_mask's
NER leaves behind must be caught by the regex pass in dlt/jp_pii.py.

dlt/jp_pii.py is loaded by file path (not `import dlt.jp_pii`) because the
`dlt/` dir has no __init__.py and `dlt` is also the Databricks DLT library
name — a normal import would be ambiguous. The module itself only needs `re`.
"""

from __future__ import annotations

import importlib.util
import pathlib

_JP_PII_PATH = pathlib.Path(__file__).resolve().parent.parent / "dlt" / "jp_pii.py"


def _load():
    spec = importlib.util.spec_from_file_location("jp_pii_under_test", _JP_PII_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


jp = _load()


# ---- the documented gap: 〒-marked postal codes -----------------------------

def test_postal_mark_half_width_is_masked():
    out = jp.mask_jp_pii("お客様のご住所は〒150-0001 東京都渋谷区です。", "ja")
    assert jp.POSTAL_PLACEHOLDER in out
    assert "150-0001" not in out
    assert "150" not in out and "0001" not in out


def test_postal_mark_full_width_is_masked():
    out = jp.mask_jp_pii("〒１５０－０００１", "ja")
    assert out == jp.POSTAL_PLACEHOLDER


def test_postal_mark_is_masked_regardless_of_language():
    # The 〒 mark only appears in JP text, so the mark pass is language-agnostic.
    out = jp.mask_jp_pii("Ship to 〒150-0001 please", "en")
    assert "150-0001" not in out
    assert jp.POSTAL_PLACEHOLDER in out


# ---- bare postal codes (JP rows only) ---------------------------------------

def test_bare_postal_masked_in_japanese():
    out = jp.mask_jp_pii("郵便番号は150-0001、よろしくお願いします。", "ja")
    assert "150-0001" not in out
    assert jp.POSTAL_PLACEHOLDER in out


def test_bare_postal_left_alone_in_english():
    # No bare-postal pass for non-JP rows — we don't want to over-mask en/es.
    out = jp.mask_jp_pii("reference code 150-0001 on the invoice", "en")
    assert "150-0001" in out


def test_zip_plus_four_is_not_a_postal_match():
    # 5-4 digit ZIP+4 must not be mistaken for a 3-4 JP postal code.
    out = jp.mask_jp_pii("12345-6789", "ja")
    assert out == "12345-6789"


# ---- street-address number fragments ----------------------------------------

def test_address_number_fragments_masked():
    out = jp.mask_jp_pii("渋谷区神南1丁目2番3号", "ja")
    for frag in ("1丁目", "2番", "3号"):
        assert frag not in out
    assert jp.ADDRESS_PLACEHOLDER in out
    assert "渋谷区神南" in out  # the non-numeric address text is ai_mask's job


# ---- robustness --------------------------------------------------------------

def test_none_and_empty_pass_through():
    assert jp.mask_jp_pii(None) is None
    assert jp.mask_jp_pii("") == ""


def test_clean_japanese_text_untouched():
    s = "画面が割れました。修理をお願いします。"
    assert jp.mask_jp_pii(s, "ja") == s
