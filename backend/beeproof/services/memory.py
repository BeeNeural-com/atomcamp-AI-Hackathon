"""Correction memory (ARCHITECTURE.md §1.6, P2).

Reviewer corrections for the same institution become prompt hints. They never
retrain a model and never change values without a fresh extraction + validation.
"""

from __future__ import annotations

from sqlalchemy import func, select

from beeproof.db import session_scope
from beeproof.domain.normalizers import institution_from_filename
from beeproof.orm import ExtractedField, ReviewDecision

MAX_HINTS = 5


def hints_for(filename: str) -> str:
    institution = institution_from_filename(filename)
    if not institution:
        return ""
    with session_scope() as s:
        rows = s.execute(
            select(ReviewDecision.previous_value, ReviewDecision.corrected_value, ExtractedField.field_name)
            .join(ExtractedField, ReviewDecision.field_id == ExtractedField.id)
            .where(ReviewDecision.decision == "correct", func.lower(ReviewDecision.institution) == institution.lower())
            .order_by(ReviewDecision.created_at.desc())
            .limit(MAX_HINTS)
        ).all()
    if not rows:
        return ""
    lines = [f'Reviewer notes for "{institution}" (read values exactly as printed; do not apply blindly):']
    for prev, corrected, name in rows:
        lines.append(f'- {name}: an earlier reading "{prev}" was corrected to "{corrected}".')
    return "\n".join(lines)
