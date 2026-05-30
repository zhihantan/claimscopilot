"""Japanese-specific PII regex masking for ClaimsCopilot narratives.

`ai_mask(..., array('person','email','phone','address',...))` reliably catches
Japanese names and phone numbers, but its address NER misses residential
*postal-code* fragments expressed numerically — e.g. ``〒150-0001`` — and bare
street-address number fragments (``1丁目2番3号``). This module is the single
source of truth for the regex pass that closes that gap.

It is deliberately dependency-free (only ``re``) so it can be unit-tested
without Spark/DLT installed. The DLT pipeline (`anonymize_narratives.py`)
imports the pattern strings and applies them with native Spark
``regexp_replace`` (Java regex), which is syntax-compatible with these patterns
for the constructs used here (character classes, quantifiers, and bounded
look-arounds). `mask_jp_pii` is the Python mirror used by the tests.
"""

from __future__ import annotations

import re

# Assorted hyphen/dash code points seen in scanned/OCR'd JP addresses. The
# ASCII hyphen-minus is listed FIRST so the class is never read as a range.
_HYPHENS = r"-‐‑‒–—ー－"
_DIGITS = r"0-9０-９"  # half-width 0-9 + full-width ０-９

# 1) Postal code with the postal mark 〒 (U+3012). The mark only appears in
#    Japanese text, so this pattern is safe to apply to narratives of any
#    language. Tolerates a space and full-width digits/hyphens.
JP_POSTAL_MARK_PATTERN = (
    r"〒\s*[" + _DIGITS + r"]{3}[" + _HYPHENS + r"]?[" + _DIGITS + r"]{4}"
)

# 2) A bare 3-4 postal code (no 〒). A hyphen is REQUIRED so we don't swallow
#    longer digit runs, and non-digit look-arounds keep it from matching the
#    middle of a longer number. Phone numbers are already handled by
#    ai_mask('phone'); this targets the postal fragments NER leaves behind.
#    Applied to JP-language rows only (see anonymize_narratives.py).
JP_POSTAL_BARE_PATTERN = (
    r"(?<![" + _DIGITS + r"])[" + _DIGITS + r"]{3}[" + _HYPHENS + r"][" + _DIGITS + r"]{4}(?![" + _DIGITS + r"])"
)

# 3) Japanese street-address number fragments: "1丁目", "23番地", "4番", "5号".
JP_ADDRESS_NUM_PATTERN = r"[" + _DIGITS + r"]+\s*(?:丁目|番地|番|号)"

POSTAL_PLACEHOLDER = "[郵便番号]"   # "postal code"
ADDRESS_PLACEHOLDER = "[住所]"       # "address"

_MARK_RE = re.compile(JP_POSTAL_MARK_PATTERN)
_BARE_RE = re.compile(JP_POSTAL_BARE_PATTERN)
_ADDR_RE = re.compile(JP_ADDRESS_NUM_PATTERN)


def mask_jp_pii(text: str | None, language: str = "ja") -> str | None:
    """Mask JP postal/address fragments left behind by ai_mask.

    The 〒-marked postal pattern is applied regardless of language (the mark is
    JP-only). The bare-postal and street-number passes apply only to
    JP-language narratives, mirroring the DLT pipeline.
    """
    if not text:
        return text
    out = _MARK_RE.sub(POSTAL_PLACEHOLDER, text)
    if language == "ja":
        out = _BARE_RE.sub(POSTAL_PLACEHOLDER, out)
        out = _ADDR_RE.sub(ADDRESS_PLACEHOLDER, out)
    return out
