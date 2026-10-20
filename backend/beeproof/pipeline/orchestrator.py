"""Explicit state-machine pipeline (ARCHITECTURE.md §6, AGENT_DESIGN.md §1).

The LLM is called at two points (Witness B, re-look). The agentic loop is:
observe (reconcile/validate) -> pick fields to re-read -> act (crop/zoom/re-read)
-> re-evaluate, bounded by MAX_REPAIR_ATTEMPTS and a per-job call budget.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from beeproof.config import Settings
from beeproof.contracts import ActionEvent, LayoutReading, PageCtx, RecordDraft
from beeproof.domain.scoring import score_and_decide
from beeproof.domain.validators import apply_rules, validate_cross_records, validate_record
from beeproof.pipeline.reconcile import reconcile, rows_only_in_witness_a
from beeproof.pipeline.relook import RelookBudget, relook_round
from beeproof.pipeline.schema_map import schema_map
from beeproof.pipeline.triage import triage
from beeproof.providers.extractors import Extractor, build_extractor
from beeproof.providers.layout import LayoutWitness, ProviderError

log = logging.getLogger(__name__)

StageCallback = Callable[[str], None]


@dataclass
class PipelineResult:
    pages: list[PageCtx]
    readings: dict[int, LayoutReading]
    records: list[RecordDraft]
    actions: list[ActionEvent]
    latency_ms: int = 0
    provider_calls: int = 0
    estimated_cost_usd: float | None = 0.0
    extractor_name: str = ""
    page_errors: list[dict] = field(default_factory=list)


def _sum_cost(actions: list[ActionEvent]) -> float | None:
    total = 0.0
    for a in actions:
        if a.model_name and a.estimated_cost_usd is None:
            return None  # unknown stays unknown
        total += a.estimated_cost_usd or 0.0
    return round(total, 6)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        extractor: Extractor | None = None,
        layout: LayoutWitness | None = None,
        hints: Callable[[str], str] | None = None,
    ):
        self.settings = settings
        self.extractor = extractor or build_extractor(settings)
        self.layout = layout or LayoutWitness(settings)
        self.hints = hints

    def run(self, data: bytes, filename: str, on_stage: StageCallback | None = None) -> PipelineResult:
        s, flags = self.settings, self.settings.flags
        stage = on_stage or (lambda _name: None)
        t0 = time.perf_counter()
        actions: list[ActionEvent] = []

        stage("preprocessing")
        tt = time.perf_counter()
        pages = triage(data, filename, s.max_document_pages, s.max_image_pixels)
        actions.append(
            ActionEvent(
                stage="preprocessing",
                agent_name="layout_agent",
                action_type="triage",
                reason_code="TRIAGE",
                summary=f"{len(pages)} page(s): " + ", ".join(f"p{p.page_no}={p.kind}" for p in pages),
                duration_ms=int((time.perf_counter() - tt) * 1000),
            )
        )

        stage("parsing")
        workers = max(1, min(s.page_concurrency, len(pages)))
        readings: dict[int, LayoutReading] = {}
        if flags.witness_a:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for ctx, (reading, ev) in zip(pages, pool.map(self.layout.read, pages)):
                    readings[ctx.page_no] = reading
                    actions.append(ev)
        else:
            readings = {p.page_no: LayoutReading(provider="none") for p in pages}

        stage("extracting")
        hints = self.hints(filename) if (flags.memory and self.hints) else ""

        def extract(ctx: PageCtx):
            try:
                return ctx, self.extractor.extract(ctx, hints or ""), None
            except ProviderError as exc:
                return ctx, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(extract, pages))

        records: list[RecordDraft] = []
        page_errors: list[dict] = []
        for ctx, out, err in results:
            if err is not None:
                if err.transient:
                    raise err
                page_errors.append({"page": ctx.page_no, "code": err.code, "message": str(err)})
                actions.append(
                    ActionEvent(
                        stage="extracting",
                        agent_name="extraction_agent",
                        action_type="extract_page",
                        reason_code=err.code,
                        summary=f"page {ctx.page_no}: extraction failed: {err}",
                        result_status="failed",
                        model_name=self.extractor.name,
                    )
                )
                continue
            gpage, info = out
            drafts = schema_map(gpage, filename, ctx.page_no)
            if flags.witness_a:
                reading = readings[ctx.page_no]
                reconcile(drafts, reading)
                drafts += rows_only_in_witness_a(drafts, reading, filename, ctx.page_no, len(drafts))
            records.extend(drafts)
            note = ""
            if not gpage.rows and ctx.kind != "digital":
                note = " (page has no text layer; configure EXTRACTOR=gemini to read images)"
            actions.append(
                ActionEvent(
                    stage="extracting",
                    agent_name="extraction_agent",
                    action_type="extract_page",
                    reason_code="EXTRACT",
                    model_name=info.model_name,
                    duration_ms=info.duration_ms,
                    token_usage=info.token_usage,
                    estimated_cost_usd=info.estimated_cost_usd,
                    summary=f"page {ctx.page_no}: {len(gpage.rows)} row(s), headers {gpage.column_headers}{note}",
                )
            )
        if pages and len(page_errors) == len(pages):
            raise ProviderError(page_errors[0]["code"], page_errors[0]["message"])

        stage("validating")
        self._validate(records)

        if flags.relook and flags.validators:
            budget = RelookBudget(max_calls=s.max_relook_calls_per_job)
            ctx_by_page = {p.page_no: p for p in pages}
            for attempt in range(1, s.max_relook_loops + 1):
                stage("repairing")
                events = relook_round(ctx_by_page, records, self.extractor, budget, attempt)
                actions.extend(events)
                stage("validating")
                if not any(e.action_type == "reextract_region" for e in events):
                    break
                self._validate(records)

        for rec in records:
            score_and_decide(rec, s.tau, flags.witness_a, flags.validators)
        counts = {
            d: sum(r.decision.value == d for r in records)
            for d in ("AUTO_APPROVED", "NEEDS_REVIEW", "DOCUMENT_INCONSISTENCY")
        }
        actions.append(
            ActionEvent(
                stage="validating",
                agent_name="review_router",
                action_type="decide",
                reason_code="ACCEPTANCE_GATE",
                summary=(
                    f"{counts['AUTO_APPROVED']} auto-approved, {counts['NEEDS_REVIEW']} need review, "
                    f"{counts['DOCUMENT_INCONSISTENCY']} document inconsistencies (tau={s.tau})"
                ),
            )
        )

        provider_calls = sum(
            1
            for a in actions
            if a.model_name
            and a.result_status != "skipped"
            and a.action_type in {"extract_page", "reextract_region", "read_layout"}
        )
        return PipelineResult(
            pages=pages,
            readings=readings,
            records=records,
            actions=actions,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            provider_calls=provider_calls,
            estimated_cost_usd=_sum_cost(actions),
            extractor_name=self.extractor.name,
            page_errors=page_errors,
        )

    def _validate(self, records: list[RecordDraft]) -> None:
        if not self.settings.flags.validators:
            for rec in records:
                apply_rules(rec, [])
            return
        cross = validate_cross_records(records)
        for rec in records:
            apply_rules(rec, validate_record(rec) + cross.get(rec.key, []))
