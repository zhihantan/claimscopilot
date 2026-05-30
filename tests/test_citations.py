"""Citation-extraction regex sanity. These regexes gate what the citation
verifier accepts as a candidate; a regression here means citations either
get silently dropped or fake citations slip through."""

from __future__ import annotations

from backend.agent.agent import (
    _CITATION_CLAIM_RE,
    _CITATION_KB_RE,
    _CITATION_POLICY_RE,
    _DECISION_TAG_RE,
)


def test_policy_citation_extracts_section_and_version():
    text = "Covered under [POLICY §3.2 / wording v2025-04] subject to excess."
    matches = list(_CITATION_POLICY_RE.finditer(text))
    assert len(matches) == 1
    assert matches[0].group(1) == "3.2"
    assert matches[0].group(2) == "v2025-04"


def test_kb_citation_extracts_id():
    text = "See [KB-EMEA-FRAUD-001] for the SIU referral playbook."
    matches = list(_CITATION_KB_RE.finditer(text))
    assert [m.group(1) for m in matches] == ["EMEA-FRAUD-001"]


def test_claim_citation_extracts_anon_id():
    text = "Similar prior: [CLAIM-7f3a2b91 similar] same product and excess."
    matches = list(_CITATION_CLAIM_RE.finditer(text))
    assert [m.group(1) for m in matches] == ["7f3a2b91"]


def test_decision_tag_extracts_class_and_confidence():
    text = 'Final answer.\n<decision class="APPROVE" confidence="HIGH" />'
    m = _DECISION_TAG_RE.search(text)
    assert m is not None
    assert m.group(1) == "APPROVE"
    assert m.group(2) == "HIGH"


def test_decision_tag_rejects_invalid_class():
    text = '<decision class="MAYBE" confidence="HIGH" />'
    assert _DECISION_TAG_RE.search(text) is None


def test_policy_citation_does_not_match_partial_strings():
    text = "[POLICY no-section v1]"
    assert _CITATION_POLICY_RE.search(text) is None
