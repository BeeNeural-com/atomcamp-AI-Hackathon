"""Validator catalog (ARCHITECTURE.md §7). Deterministic, versioned rules."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

from beeproof.contracts import TERM_PRODUCTS, ProductType, RecordDraft, RuleResult
from beeproof.domain.normalizers import is_iso_date

RULES_VERSION = "1"

COMP = {"daily": 365, "monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}

APY_GE_RATE_TOL = 0.0005
APY_MAX_TOL = 0.0015
COMPOUNDING_TOL = 0.006

RULE_CATALOG: dict[str, dict] = {
    "RANGE_RATE": {"severity": "HARD", "fields": ["rate_pct"], "rule": "0 <= rate <= 15"},
    "RANGE_APY": {"severity": "HARD", "fields": ["apy_pct"], "rule": "0 <= apy <= 15"},
    "RANGE_MIN_DEPOSIT": {"severity": "HARD", "fields": ["min_deposit"], "rule": "0 <= x <= 10,000,000"},
    "RANGE_TERM": {"severity": "HARD", "fields": ["term_months"], "rule": "integer 0..120"},
    "FORMAT_DATE": {"severity": "HARD", "fields": ["effective_date"], "rule": "ISO yyyy-mm-dd"},
    "DATE_PLAUSIBLE": {"severity": "SOFT", "fields": ["effective_date"], "rule": "within [today-3y, today+1y]"},
    "PRODUCT_KNOWN": {"severity": "SOFT", "fields": ["product_type"], "rule": "not Other"},
    "MISSING_REQUIRED": {
        "severity": "HARD",
        "fields": ["institution_name", "product_type", "rate_pct", "apy_pct", "effective_date"],
        "rule": "institution, product, rate or apy, date present",
    },
    "APY_GE_RATE": {"severity": "HARD", "fields": ["rate_pct", "apy_pct"], "rule": "apy >= rate - 0.0005"},
    "APY_PHYSICS_MAX": {
        "severity": "HARD",
        "fields": ["rate_pct", "apy_pct"],
        "rule": "apy <= (e^(rate/100)-1)*100 + 0.0015",
    },
    "COMPOUNDING_MATCH": {
        "severity": "SOFT",
        "fields": ["rate_pct", "apy_pct"],
        "rule": "some n in {365,12,4,2,1} within 0.006",
    },
    "TERM_PRODUCT_CONSISTENT": {
        "severity": "HARD",
        "fields": ["term_months", "product_type"],
        "rule": "CD/IRA CD => term>0; others => term=0",
    },
    "DUPLICATE_ROW": {
        "severity": "SOFT",
        "fields": ["product_type", "term_months"],
        "rule": "unique (file, institution, product, term, date)",
    },
    "DATE_CONSISTENT_IN_DOC": {"severity": "SOFT", "fields": ["effective_date"], "rule": "one date per document"},
    "RATE_JUMP": {"severity": "SOFT", "fields": ["rate_pct"], "rule": "same product+term across dates changes <= 2x"},
}


def apy_from_rate(rate_pct: float, n: int) -> float:
    r = rate_pct / 100
    return ((1 + r / n) ** n - 1) * 100


def apy_max(rate_pct: float) -> float:
    return (math.exp(rate_pct / 100) - 1) * 100


def infer_compounding(rate: float, apy: float) -> str | None:
    best = min(COMP, key=lambda k: abs(apy_from_rate(rate, COMP[k]) - apy))
    if abs(apy_from_rate(rate, COMP[best]) - apy) <= COMPOUNDING_TOL:
        return best
    if abs(apy - rate) <= APY_GE_RATE_TOL:
        return "none"
    return None


def check_apy(rate: float, apy: float):
    out = [("APY_GE_RATE", apy >= rate - APY_GE_RATE_TOL), ("APY_PHYSICS_MAX", apy <= apy_max(rate) + APY_MAX_TOL)]
    return out, infer_compounding(rate, apy)


def _r(rule_id: str, ok: bool | None, message: str = "", expected=None, actual=None) -> RuleResult:
    meta = RULE_CATALOG[rule_id]
    if ok is None:
        status = "n/a"
    elif ok:
        status = "pass"
    else:
        status = "fail" if meta["severity"] == "HARD" else "warn"
    return RuleResult(
        rule_id=rule_id,
        severity=meta["severity"],
        status=status,
        fields=list(meta["fields"]),
        message=message if status in {"fail", "warn"} else "",
        expected=None if expected is None else str(expected),
        actual=None if actual is None else str(actual),
        rule_version=RULES_VERSION,
    )


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def validate_record(rec: RecordDraft, today: date | None = None) -> list[RuleResult]:
    """Per-row rules."""
    today = today or date.today()
    rate, apy = _num(rec.value("rate_pct")), _num(rec.value("apy_pct"))
    dep, term = _num(rec.value("min_deposit")), rec.value("term_months")
    eff, product = rec.value("effective_date"), rec.value("product_type")
    inst = rec.value("institution_name")
    out: list[RuleResult] = []

    out.append(
        _r("RANGE_RATE", None if rate is None else 0 <= rate <= 15, f"rate {rate}% outside 0-15%", "0..15", rate)
    )
    out.append(_r("RANGE_APY", None if apy is None else 0 <= apy <= 15, f"APY {apy}% outside 0-15%", "0..15", apy))
    out.append(
        _r(
            "RANGE_MIN_DEPOSIT",
            None if dep is None else 0 <= dep <= 10_000_000,
            f"minimum deposit {dep} outside 0-10,000,000",
            "0..10000000",
            dep,
        )
    )
    term_ok = None
    if term is not None:
        term_ok = isinstance(term, int) and 0 <= term <= 120
    out.append(_r("RANGE_TERM", term_ok, f"term {term} months outside 0-120", "0..120", term))

    out.append(
        _r(
            "FORMAT_DATE",
            None if eff is None else is_iso_date(eff),
            f"effective date '{eff}' is not a valid date",
            "yyyy-mm-dd",
            eff,
        )
    )
    plausible = None
    if eff is not None and is_iso_date(eff):
        d = date.fromisoformat(eff)
        plausible = today - timedelta(days=3 * 365) <= d <= today + timedelta(days=365)
    out.append(_r("DATE_PLAUSIBLE", plausible, f"effective date {eff} is far from today", None, eff))

    out.append(
        _r(
            "PRODUCT_KNOWN",
            None if product is None else product != ProductType.OTHER.value,
            "product label could not be mapped to a known product type",
            None,
            product,
        )
    )

    missing = [
        n
        for n, v in (("institution_name", inst), ("product_type", product), ("effective_date", eff))
        if v in (None, "")
    ]
    if rate is None and apy is None:
        missing.append("rate_pct|apy_pct")
    mr = _r("MISSING_REQUIRED", not missing, f"missing required: {', '.join(missing)}", None, ",".join(missing))
    if missing:
        mr.fields = [m for m in missing if "|" not in m] or ["rate_pct", "apy_pct"]
        if "rate_pct|apy_pct" in missing:
            mr.fields = list(dict.fromkeys(mr.fields + ["rate_pct", "apy_pct"]))
    out.append(mr)

    comp = None
    if rate is not None and apy is not None:
        mx = apy_max(rate)
        out.append(
            _r(
                "APY_GE_RATE",
                apy >= rate - APY_GE_RATE_TOL,
                f"printed APY {apy:g}% is below rate {rate:g}%",
                f">= {rate:g}",
                apy,
            )
        )
        out.append(
            _r(
                "APY_PHYSICS_MAX",
                apy <= mx + APY_MAX_TOL,
                f"printed APY {apy:g}% exceeds max possible {mx:.3f}% for rate {rate:g}%",
                f"<= {mx:.3f}",
                apy,
            )
        )
        comp = infer_compounding(rate, apy)
        out.append(
            _r(
                "COMPOUNDING_MATCH",
                comp is not None,
                f"APY {apy:g}% matches no standard compounding of rate {rate:g}%",
                None,
                apy,
            )
        )
    else:
        for rid in ("APY_GE_RATE", "APY_PHYSICS_MAX", "COMPOUNDING_MATCH"):
            out.append(_r(rid, None))
    rec.inferred_compounding = comp

    tpc = None
    if product is not None:
        try:
            is_term_product = ProductType(product) in TERM_PRODUCTS
        except ValueError:
            is_term_product = False
        if is_term_product:
            tpc = term is not None and term > 0  # a CD without a printed term is not acceptable
        elif term is not None:
            tpc = term == 0
    term_text = "no printed term" if term is None else f"term {term} months"
    out.append(_r("TERM_PRODUCT_CONSISTENT", tpc, f"{product} with {term_text} is inconsistent", None, term))
    return out


def validate_cross_records(records: list[RecordDraft]) -> dict[str, list[RuleResult]]:
    """Cross-row / cross-page rules. Returns extra results keyed by record key."""
    extra: dict[str, list[RuleResult]] = defaultdict(list)

    seen: dict[tuple, str] = {}
    for rec in records:
        key = (
            rec.source_file,
            rec.value("institution_name"),
            rec.value("product_type"),
            rec.value("term_months"),
            rec.value("effective_date"),
        )
        if key in seen:
            extra[rec.key].append(
                _r("DUPLICATE_ROW", False, f"duplicates row {seen[key]} (same product, term and date)")
            )
        else:
            seen[key] = rec.key
            extra[rec.key].append(_r("DUPLICATE_ROW", True))

    by_file: dict[str, set] = defaultdict(set)
    for rec in records:
        if rec.value("effective_date"):
            by_file[rec.source_file].add(rec.value("effective_date"))
    for rec in records:
        dates = by_file.get(rec.source_file, set())
        ok = None if not rec.value("effective_date") else len(dates) <= 1
        extra[rec.key].append(
            _r("DATE_CONSISTENT_IN_DOC", ok, f"document shows {len(dates)} different effective dates")
        )

    series: dict[tuple, list[RecordDraft]] = defaultdict(list)
    for rec in records:
        series[(rec.value("institution_name"), rec.value("product_type"), rec.value("term_months"))].append(rec)
    for group in series.values():
        rates = [(_num(r.value("rate_pct")), r) for r in group if _num(r.value("rate_pct"))]
        dates = {r.value("effective_date") for _, r in rates}
        for rate, rec in rates:
            others = [o for o, r in rates if r.value("effective_date") != rec.value("effective_date")]
            if len(dates) < 2 or not others:
                extra[rec.key].append(_r("RATE_JUMP", None))
                continue
            jump = any(max(rate, o) / max(min(rate, o), 1e-9) > 2 for o in others)
            extra[rec.key].append(
                _r("RATE_JUMP", not jump, "rate changes more than 2x for the same product across dates")
            )
    return extra


def apply_rules(rec: RecordDraft, rules: list[RuleResult]) -> None:
    """Attach rule results to the record and the per-field validator map."""
    rec.rules = rules
    for f in rec.fields.values():
        f.validators = {}
    for rr in rules:
        for name in rr.fields:
            if name in rec.fields:
                rec.fields[name].validators[rr.rule_id] = rr.status


def hard_failures(rec: RecordDraft) -> list[RuleResult]:
    return [r for r in rec.rules if r.severity == "HARD" and r.status == "fail"]
