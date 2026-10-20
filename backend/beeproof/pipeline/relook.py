"""Stage 7 - targeted re-look repair agent (ARCHITECTURE.md §6.6, AGENT_DESIGN.md §4-6).

Only disputed or rule-failing fields are re-read: crop + zoom + column header,
then a majority vote across Witness A, Witness B and the re-look reading.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from beeproof.contracts import ActionEvent, FieldEvidence, PageCtx, RecordDraft
from beeproof.domain.normalizers import normalize_field, values_agree
from beeproof.domain.validators import hard_failures
from beeproof.pipeline.geometry import clamp, expand
from beeproof.pipeline.triage import crop_region
from beeproof.providers.extractors import Extractor

RELOOKABLE = {"table_cell", "page_header"}
HEADER_LABEL = {
    "rate_pct": "Rate",
    "apy_pct": "APY",
    "min_deposit": "Minimum",
    "term_months": "Term",
    "product_type": "Product",
    "institution_name": "Institution",
    "effective_date": "Effective date",
}


@dataclass
class RelookBudget:
    max_calls: int
    used: int = 0
    settled: set[tuple[str, str]] = field(default_factory=set)  # (record key, field) with no new info

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_calls


def select_targets(drafts: list[RecordDraft], budget: RelookBudget) -> list[tuple[RecordDraft, FieldEvidence, str]]:
    targets = []
    for rec in drafts:
        hard_fields: dict[str, str] = {}
        for rr in hard_failures(rec):
            for n in rr.fields:
                hard_fields.setdefault(n, rr.rule_id)
        for f in rec.fields.values():
            if f.source_type not in RELOOKABLE or not f.bbox or f.value is None:
                continue
            if (rec.key, f.name) in budget.settled:
                continue
            if f.agreement < 1.0:
                targets.append((rec, f, "AGREEMENT_LOW"))
            elif f.name in hard_fields:
                targets.append((rec, f, f"HARD_RULE_FAILED:{hard_fields[f.name]}"))
    return targets


def _header_region(drafts: list[RecordDraft], rec: RecordDraft, f: FieldEvidence) -> list[float] | None:
    tops = [
        r.fields[f.name].bbox[1] for r in drafts if r.page == rec.page and f.name in r.fields and r.fields[f.name].bbox
    ]
    if not tops:
        return None
    top = min(tops)
    region = clamp([f.bbox[0] - 0.02, max(0.0, top - 0.06), f.bbox[2] + 0.02, top])
    return region if region[3] - region[1] > 0.005 else None


def _vote(name: str, readings: list[tuple[str, object, str]]) -> tuple[object, str, int]:
    """Return (winning value, its raw text, votes for winner)."""
    best: tuple[object, str, int] = (None, "", 0)
    for _, val, raw in readings:
        votes = sum(1 for _, v2, r2 in readings if values_agree(name, val, v2, raw, r2) == 1.0)
        if votes > best[2]:
            best = (val, raw, votes)
    return best


def relook_field(
    ctx: PageCtx,
    drafts: list[RecordDraft],
    rec: RecordDraft,
    f: FieldEvidence,
    reason: str,
    extractor: Extractor,
    budget: RelookBudget,
    attempt: int,
) -> ActionEvent:
    crop_png = crop_region(ctx, expand(f.bbox, 0.2))
    header_box = _header_region(drafts, rec, f)
    header_png = crop_region(ctx, header_box) if header_box else None
    prior = [r for r in (f.witness_a, f.witness_b) if r] + f.relook_values
    answer, info = extractor.relook(
        ctx, crop_png, header_png, f.bbox, HEADER_LABEL.get(f.name, f.name), rec.product_label, prior
    )
    budget.used += 1

    raw = answer.raw.strip() if answer.legible else ""
    new_info = bool(raw) and raw not in f.relook_values
    f.relook_values.append(raw)

    readings: list[tuple[str, object, str]] = []
    for source, text in (("A", f.witness_a), ("B", f.witness_b), ("relook", raw)):
        if text:
            val = normalize_field(f.name, text)
            if val is not None:
                readings.append((source, val, text))

    old_value = f.value
    winner, winner_raw, votes = _vote(f.name, readings)
    if votes == 3:
        f.agreement, f.unresolved = 1.0, False
    elif votes == 2:
        f.agreement, f.unresolved = 0.67, False
    else:
        f.agreement, f.unresolved = 0.0, True
    if votes >= 2 and values_agree(f.name, winner, f.value, winner_raw, f.raw) < 1.0:
        f.value, f.raw = winner, winner_raw
        f.transformations.append(
            {
                "step": "relook",
                "from": old_value,
                "to": winner,
                "reason_code": "RELOOK_CORRECTED",
                "summary": f"{f.name} changed from {old_value} to {winner} by majority vote",
            }
        )
    if f.witness_a:
        a_val = normalize_field(f.name, f.witness_a)
        f.grounded = values_agree(f.name, a_val, f.value, f.witness_a, f.raw) == 1.0
    if not new_info or not f.unresolved:
        budget.settled.add((rec.key, f.name))

    outcome = "unresolved" if f.unresolved else ("corrected" if f.value != old_value else "confirmed")
    artifacts = [(f"{rec.key}_{f.name}_{attempt}_crop.png", crop_png, "image/png")]
    if header_png:
        artifacts.append((f"{rec.key}_{f.name}_{attempt}_header.png", header_png, "image/png"))
    return ActionEvent(
        stage="repairing",
        agent_name="repair_planner",
        action_type="reextract_region",
        reason_code=reason.split(":")[0],
        summary=(
            f"{rec.key}.{f.name}: re-read zoomed cell ({reason}); readings "
            f"{[r[2] for r in readings]} -> {outcome}, agreement {f.agreement}"
        ),
        result_status="unresolved" if f.unresolved else "succeeded",
        duration_ms=info.duration_ms,
        attempt_number=attempt,
        model_name=info.model_name,
        token_usage=info.token_usage,
        estimated_cost_usd=info.estimated_cost_usd,
        input_refs=[f"field:{rec.key}.{f.name}"],
        artifacts=artifacts,
    )


def relook_round(
    ctx_by_page: dict[int, PageCtx], drafts: list[RecordDraft], extractor: Extractor, budget: RelookBudget, attempt: int
) -> list[ActionEvent]:
    events: list[ActionEvent] = []
    for rec, f, reason in select_targets(drafts, budget):
        if budget.exhausted:
            events.append(
                ActionEvent(
                    stage="repairing",
                    agent_name="repair_planner",
                    action_type="request_human_review",
                    reason_code="REPAIR_BUDGET_EXHAUSTED",
                    summary=f"re-look budget of {budget.max_calls} calls used; remaining disputes go to review",
                    result_status="skipped",
                )
            )
            break
        ctx = ctx_by_page.get(rec.page)
        if ctx is not None:
            events.append(relook_field(ctx, drafts, rec, f, reason, extractor, budget, attempt))
    return events
