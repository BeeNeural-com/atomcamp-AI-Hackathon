"""Normalizers, validators, scoring and the job state machine (pure functions)."""

from __future__ import annotations

import csv
from datetime import date

import pytest

from beeproof.config import REPO_DIR
from beeproof.contracts import Decision, FieldEvidence, RecordDraft
from beeproof.domain import normalizers as N
from beeproof.domain.scoring import score_and_decide
from beeproof.domain.states import InvalidTransition, JobStatus, check_transition
from beeproof.domain.validators import (
    apply_rules,
    apy_max,
    check_apy,
    infer_compounding,
    validate_cross_records,
    validate_record,
)

TODAY = date(2026, 7, 15)


@pytest.mark.parametrize("raw,expected", [("3.55%", 3.55), ("3.550 %", 3.55), ("3.55*", 3.55), ("abc", None)])
def test_parse_pct(raw, expected):
    assert N.parse_pct(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,000", 1000),
        ("1K", 1000),
        ("$25k", 25000),
        ("None", 0),
        ("No minimum", 0),
        ("$0", 0),
        ("$10,000", 10000),
        ("", None),
    ],
)
def test_parse_money(raw, expected):
    assert N.parse_money(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12 Mo", 12),
        ("1 Year", 12),
        ("18-Month", 18),
        ("6 mos", 6),
        ("365 days", 12),
        ("N/A", 0),
        ("", 0),
        ("-", 0),
        ("3 Years", 36),
    ],
)
def test_parse_term(raw, expected):
    assert N.parse_term(raw) == expected


@pytest.mark.parametrize(
    "raw", ["7/1/2026", "July 1, 2026", "2026-07-01", "Effective 07/01/26", "APY accurate as of 07/01/2026."]
)
def test_parse_date(raw):
    assert N.parse_date(raw) == "2026-07-01"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Share Certificate", "CD"),
        ("IRA Certificate", "IRA CD"),
        ("12 Month CD*", "CD"),
        ("Money Market Account", "Money Market"),
        ("High-Yield Savings", "High-Yield Savings"),
        ("Checking Plus", "Checking Plus"),
        ("Share Savings", "Savings"),
        ("Gold Bar", "Other"),
    ],
)
def test_map_product(label, expected):
    assert N.map_product(label).value == expected


@pytest.mark.parametrize(
    "header,field",
    [
        ("Dividend Rate", "rate_pct"),
        ("APY*", "apy_pct"),
        ("Annual Percentage Yield", "apy_pct"),
        ("Min. to Open", "min_deposit"),
        ("Minimum Balance", "min_deposit"),
        ("Term", "term_months"),
        ("Account", "product_type"),
        ("Interest Rate", "rate_pct"),
        ("Footnotes", None),
    ],
)
def test_map_header(header, field):
    assert N.map_header(header) == field


def test_institution_from_filename():
    assert N.institution_from_filename("summit_trust_rates_page4.pdf") == "Summit Trust"
    assert N.institution_from_filename("metro_credit_union_rates_page3__2026-09-01.pdf") == "Metro Credit Union"


def test_values_agree_rounding():
    assert N.values_agree("apy_pct", 4.06, 4.057, "4.06%", "4.057%") == 0.5
    assert N.values_agree("apy_pct", 4.057, 4.057, "4.057%", "4.057") == 1.0
    assert N.values_agree("apy_pct", 4.07, 4.057, "4.07%", "4.057%") == 0.0


# ---------------------------------------------------------------- validators (ARCHITECTURE.md §18 fixtures)
def test_physics_fail_sample_row():
    checks, _ = check_apy(1.076, 1.162)
    assert dict(checks)["APY_PHYSICS_MAX"] is False
    assert round(apy_max(1.076), 3) == 1.082


def test_physics_pass_and_compounding_detected():
    checks, comp = check_apy(3.998, 4.057)
    assert all(ok for _, ok in checks)
    assert comp == "quarterly"


def test_first_21_sample_rows_physics_failures():
    """Claim in ARCHITECTURE.md §1: 15 of the first 21 organizer rows violate APY physics."""
    rows = list(csv.DictReader((REPO_DIR / "ps06_sample_rate_sheets.csv").open()))[:21]
    fails = sum(not dict(check_apy(float(r["rate_pct"]), float(r["apy_pct"]))[0])["APY_PHYSICS_MAX"] for r in rows)
    assert fails == 15


def test_infer_compounding_equal_rates():
    assert infer_compounding(2.0, 2.0) == "annual"


def _rec(**values) -> RecordDraft:
    base = {
        "institution_name": "Bank",
        "product_type": "CD",
        "term_months": 12,
        "rate_pct": 3.0,
        "apy_pct": 3.04,
        "min_deposit": 1000.0,
        "effective_date": "2026-07-01",
    }
    base.update(values)
    fields = {
        k: FieldEvidence(name=k, value=v, agreement=1.0, grounded=True, witness_a_conf=1.0, bbox=[0, 0, 0.1, 0.1])
        for k, v in base.items()
    }
    return RecordDraft(key="p1r0", source_file="f.pdf", page=1, fields=fields)


def _status(rules, rule_id):
    return next(r.status for r in rules if r.rule_id == rule_id)


def test_validate_record_clean():
    assert not [r for r in validate_record(_rec(), today=TODAY) if r.status == "fail"]


def test_cd_without_term_fails():
    assert _status(validate_record(_rec(term_months=None), today=TODAY), "TERM_PRODUCT_CONSISTENT") == "fail"


def test_savings_with_term_fails():
    rules = validate_record(_rec(product_type="Savings", term_months=12), today=TODAY)
    assert _status(rules, "TERM_PRODUCT_CONSISTENT") == "fail"


def test_swapped_rate_apy_fails():
    assert _status(validate_record(_rec(rate_pct=3.04, apy_pct=3.0), today=TODAY), "APY_GE_RATE") == "fail"


def test_range_and_missing():
    rules = validate_record(_rec(rate_pct=55.0, effective_date=None), today=TODAY)
    assert _status(rules, "RANGE_RATE") == "fail"
    assert _status(rules, "MISSING_REQUIRED") == "fail"


def test_cross_record_duplicate_and_date():
    a, b = _rec(), _rec()
    b.key = "p1r1"
    c = _rec(product_type="Savings", term_months=0, effective_date="2026-08-01")
    c.key = "p1r2"
    extra = validate_cross_records([a, b, c])
    assert _status(extra["p1r1"], "DUPLICATE_ROW") == "warn"
    assert _status(extra["p1r2"], "DATE_CONSISTENT_IN_DOC") == "warn"


# ---------------------------------------------------------------- scoring / decision
def test_document_inconsistency_when_witnesses_agree():
    rec = _rec(product_type="Money Market", term_months=0, rate_pct=1.076, apy_pct=1.162)
    apply_rules(rec, validate_record(rec, today=TODAY))
    score_and_decide(rec, tau=0.8)
    assert rec.decision == Decision.DOCUMENT_INCONSISTENCY
    assert "APY_PHYSICS_MAX" in rec.reason_codes


def test_needs_review_when_witnesses_disagree():
    rec = _rec(product_type="Money Market", term_months=0, rate_pct=1.076, apy_pct=1.162)
    rec.fields["apy_pct"].agreement = 0.0
    apply_rules(rec, validate_record(rec, today=TODAY))
    score_and_decide(rec, tau=0.8)
    assert rec.decision == Decision.NEEDS_REVIEW


def test_auto_approved_clean_row():
    rec = _rec()
    apply_rules(rec, validate_record(rec, today=TODAY))
    score_and_decide(rec, tau=0.8)
    assert rec.decision == Decision.AUTO_APPROVED
    assert rec.extraction_confidence == 1.0


def test_low_confidence_single_witness_goes_to_review():
    rec = _rec()
    for f in rec.fields.values():
        f.agreement, f.grounded, f.witness_a_conf = 0.5, False, None
    apply_rules(rec, validate_record(rec, today=TODAY))
    score_and_decide(rec, tau=0.8)
    assert rec.decision == Decision.NEEDS_REVIEW
    assert "LOW_CONFIDENCE" in rec.reason_codes


# ---------------------------------------------------------------- state machine
@pytest.mark.parametrize(
    "cur,tgt",
    [
        ("queued", "preprocessing"),
        ("validating", "repairing"),
        ("repairing", "validating"),
        ("validating", "review_required"),
        ("review_required", "validating"),
        ("failed", "queued"),
        ("parsing", "failed"),
        ("queued", "cancelled"),
    ],
)
def test_legal_transitions(cur, tgt):
    assert check_transition(cur, tgt) == JobStatus(tgt)


@pytest.mark.parametrize(
    "cur,tgt",
    [
        ("completed", "queued"),
        ("cancelled", "queued"),
        ("queued", "completed"),
        ("review_required", "completed"),
        ("failed", "completed"),
    ],
)
def test_illegal_transitions(cur, tgt):
    with pytest.raises(InvalidTransition):
        check_transition(cur, tgt)
