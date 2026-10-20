"""Confidence & decision (ARCHITECTURE.md §8)."""

from __future__ import annotations

from beeproof.contracts import Decision, FieldEvidence, RecordDraft
from beeproof.domain.validators import hard_failures


def validation_score(f: FieldEvidence) -> float:
    statuses = set(f.validators.values())
    if "fail" in statuses:
        return 0.0
    if "warn" in statuses:
        return 0.5
    return 1.0


def field_confidence(f: FieldEvidence, witness_a_enabled: bool, validators_enabled: bool) -> float:
    vs = validation_score(f) if validators_enabled else 1.0
    if not witness_a_enabled:
        parsed = 1.0 if f.value is not None else 0.0
        return round(0.5 * vs + 0.5 * parsed, 4)
    ocr = f.witness_a_conf if f.witness_a_conf is not None else 0.6
    return round(0.35 * f.agreement + 0.20 * ocr + 0.20 * (1.0 if f.grounded else 0.0) + 0.25 * vs, 4)


def score_and_decide(
    rec: RecordDraft, tau: float, witness_a_enabled: bool = True, validators_enabled: bool = True
) -> None:
    present = [f for f in rec.fields.values() if f.value is not None]
    for f in rec.fields.values():
        f.confidence = field_confidence(f, witness_a_enabled, validators_enabled) if f.value is not None else 0.0
    row_conf = min((f.confidence for f in present), default=0.0)
    rec.extraction_confidence = round(row_conf, 2)

    reasons: list[str] = []
    codes: list[str] = []
    hard = hard_failures(rec) if validators_enabled else []
    if hard:
        all_agree = True
        for rr in hard:
            involved = [rec.fields[n] for n in rr.fields if n in rec.fields and rec.fields[n].value is not None]
            if rr.rule_id == "MISSING_REQUIRED" or not involved or any(f.agreement < 1.0 for f in involved):
                all_agree = False
        rec.decision = Decision.DOCUMENT_INCONSISTENCY if all_agree else Decision.NEEDS_REVIEW
    elif row_conf < tau:
        rec.decision = Decision.NEEDS_REVIEW
    else:
        rec.decision = Decision.AUTO_APPROVED

    if validators_enabled:
        for rr in sorted((r for r in rec.rules if r.failed), key=lambda r: (r.severity != "HARD", r.rule_id)):
            codes.append(rr.rule_id)
            reasons.append(f"{rr.rule_id}: {rr.message}")
    for f in rec.fields.values():
        if f.unresolved:
            codes.append("FIELD_UNRESOLVED")
            reasons.append(f"FIELD_UNRESOLVED: {f.name} readings disagree after re-look")
    if rec.decision == Decision.NEEDS_REVIEW and not hard and row_conf < tau:
        low = min(present, key=lambda f: f.confidence) if present else None
        codes.append("LOW_CONFIDENCE")
        reasons.append(
            f"LOW_CONFIDENCE: row confidence {row_conf:.2f} below {tau:.2f}"
            + (f" (lowest field: {low.name})" if low else "")
        )
    for t in [t for f in rec.fields.values() for t in f.transformations if t.get("reason_code")]:
        if t["reason_code"] not in codes:
            codes.append(t["reason_code"])
            reasons.append(f"{t['reason_code']}: {t.get('summary', '')}")
    rec.reason_codes = codes
    rec.review_reasons = reasons


def fields_needing_review(rec: RecordDraft, tau: float) -> set[str]:
    """Fields a reviewer should look at for a flagged record."""
    if rec.decision == Decision.AUTO_APPROVED:
        return set()
    names: set[str] = set()
    for rr in rec.rules:
        if rr.failed:
            names.update(n for n in rr.fields if n in rec.fields)
    for f in rec.fields.values():
        if f.unresolved or (f.value is not None and f.confidence < tau):
            names.add(f.name)
    if not names:
        present = [f for f in rec.fields.values() if f.value is not None]
        if present:
            names.add(min(present, key=lambda f: f.confidence).name)
    return names
