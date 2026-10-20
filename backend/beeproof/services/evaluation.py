"""Benchmark runs triggered through the API (FR-14). One run at a time, in a background thread.

The wire shape is the frontend's EvaluationReport (lib/types.ts). Metrics are filled only from
actual runs; anything not measured is null.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from beeproof.config import PIPELINE_VERSION, Settings
from beeproof.db import session_scope
from beeproof.orm import EvaluationRun, utcnow
from beeproof.providers.storage import get_storage
from beeproof.services.errors import AppError, not_found
from bench.evaluate import CONFIGS, DEFAULT_CONFIGS, default_variants, render_markdown, run_benchmark
from bench.generate import generate

log = logging.getLogger(__name__)
_lock = threading.Lock()
VARIANTS = {"digital", "scan", "photo"}
FRONTEND_PIPELINES = ("native", "single_pass", "evidence_first")
PRIMARY_ORDER = ("evidence_first", "full", "validators_witness_a", "validators", "extractor_only", "native")
HEADLINE_METRICS = [
    "field_accuracy",
    "row_recall",
    "evidence_coverage",
    "false_auto_accept_rate",
    "silent_error_rate",
    "review_recall",
    "review_precision",
    "review_rate_excluding_doc_inconsistent",
    "doc_inconsistency_recall",
    "injected_error_detection",
]


def create_evaluation(
    session: Session,
    settings: Settings,
    configs: list[str] | None,
    variants: list[str] | None,
    limit: int | None,
    dataset_version: str | None = None,
) -> EvaluationRun:
    configs = configs or list(DEFAULT_CONFIGS)
    bad = [c for c in configs if c not in CONFIGS]
    if bad:
        raise AppError(
            "VALIDATION_ERROR",
            f"unknown configuration(s) {bad}",
            422,
            [{"field": "configurations", "reason": f"allowed: {list(CONFIGS)}"}],
        )
    variants = variants or default_variants(settings)
    if set(variants) - VARIANTS:
        raise AppError("VALIDATION_ERROR", f"variants must be within {sorted(VARIANTS)}", 422)
    if session.scalar(select(EvaluationRun.id).where(EvaluationRun.status.in_(["queued", "running"])).limit(1)):
        raise AppError("EVALUATION_IN_PROGRESS", "an evaluation is already running", 409)
    run = EvaluationRun(
        pipeline_version=PIPELINE_VERSION,
        status="queued",
        dataset_version=dataset_version,
        configuration_snapshot={
            "configs": configs,
            "variants": variants,
            "limit": limit,
            "requested_dataset_version": dataset_version,
            "extractor": settings.extractor,
            "tau": settings.tau,
            "layout_provider": settings.layout_provider,
        },
    )
    session.add(run)
    session.flush()
    return run


def start_evaluation(settings: Settings, run_id: str) -> None:
    """Dispatch a run: to the private worker via Cloud Tasks in production, else a local thread.

    On Cloud Run a background thread in the API would be CPU-throttled once the request returns.
    """
    from beeproof.providers.queue import CloudTasksQueue, get_queue

    queue = get_queue(settings)
    if isinstance(queue, CloudTasksQueue):
        if not queue.enqueue(f"/internal/evaluations/{run_id}/run"):
            with session_scope() as s:
                run = s.get(EvaluationRun, run_id)
                run.status, run.error_message = "failed", "could not enqueue the evaluation task"
        return
    threading.Thread(target=execute, args=(settings, run_id), name=f"eval-{run_id[:8]}", daemon=True).start()


def execute(settings: Settings, run_id: str) -> None:
    """Run one evaluation synchronously (worker task or local thread)."""
    with _lock:
        with session_scope() as s:
            run = s.get(EvaluationRun, run_id)
            if run is None or run.status not in {"queued", "running"}:
                return  # duplicate delivery of a finished run
            run.status, run.started_at = "running", utcnow()
            cfg = dict(run.configuration_snapshot)
        try:
            dataset = Path(settings.bench_dir)
            if not (dataset / "truth.jsonl").exists():
                generate(settings.bench_source_csv, dataset)
            report = run_benchmark(settings, dataset, cfg["configs"], cfg["variants"], cfg.get("limit"))
            storage = get_storage(settings)
            key = storage.put(
                f"evaluations/{run_id}/report.json", json.dumps(report, indent=2).encode(), "application/json"
            )
            storage.put(f"evaluations/{run_id}/report.md", render_markdown(report).encode(), "text/markdown")
            with session_scope() as s:
                run = s.get(EvaluationRun, run_id)
                run.status, run.completed_at = "completed", utcnow()
                run.dataset_version = report["dataset_version"]
                if report.get("git_commit"):
                    run.pipeline_version = f"{PIPELINE_VERSION}+{report['git_commit']}"
                run.metrics_json = {
                    "results": {
                        c: {k: r[k] for k in ("overall", "extractor") if k in r}
                        | ({"skipped": r["skipped"]} if r.get("skipped") else {})
                        for c, r in report["results"].items()
                    },
                    "documents_per_variant": report["documents_per_variant"],
                    "variants": report["variants"],
                    "notes": report["notes"],
                }
                run.report_key = key
        except Exception as exc:
            log.exception("evaluation %s failed", run_id)
            with session_scope() as s:
                run = s.get(EvaluationRun, run_id)
                run.status, run.completed_at = "failed", utcnow()
                run.error_message = f"{type(exc).__name__}: {exc}"


def get_evaluation(session: Session, run_id: str) -> EvaluationRun:
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise not_found("evaluation", run_id)
    return run


def list_evaluations(session: Session, limit: int, offset: int) -> tuple[list[EvaluationRun], int | None]:
    rows = list(
        session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).offset(offset).limit(limit + 1))
    )
    return rows[:limit], (offset + limit if len(rows) > limit else None)


def _value(m: dict | None) -> float | None:
    return None if not m else m.get("value")


def report_out(run: EvaluationRun) -> dict:
    """EvaluationReport wire shape (lib/types.ts) plus additive details."""
    results = (run.metrics_json or {}).get("results", {})
    primary_name = next((n for n in PRIMARY_ORDER if results.get(n, {}).get("overall")), None)
    primary = results[primary_name]["overall"] if primary_name else None
    metrics = []
    if primary:
        fa = primary["field_accuracy"]["per_field"]
        num = sum(v["num"] for v in fa.values())
        den = sum(v["den"] for v in fa.values())
        metrics.append(
            {
                "name": "field_accuracy",
                "numerator": num,
                "denominator": den,
                "value": round(num / den, 4) if den else None,
            }
        )
        for name in HEADLINE_METRICS[1:]:
            m = primary.get(name)
            if m:
                metrics.append({"name": name, "numerator": m["num"], "denominator": m["den"], "value": m["value"]})
    pipelines = []
    for name in FRONTEND_PIPELINES:
        o = results.get(name, {}).get("overall")
        if name in results:
            pipelines.append(
                {
                    "name": name,
                    "field_accuracy": o["field_accuracy"]["macro"] if o else None,
                    "evidence_coverage": _value(o["evidence_coverage"]) if o else None,
                    "false_auto_accept_rate": _value(o["false_auto_accept_rate"]) if o else None,
                    "p95_latency_ms": o["latency_ms_per_page"]["p95"] if o else None,
                    "skipped": results[name].get("skipped"),
                }
            )
    cost = primary.get("estimated_cost_usd") if primary else None
    return {
        "id": run.id,
        "status": run.status,
        "dataset_version": run.dataset_version
        or (run.configuration_snapshot or {}).get("requested_dataset_version")
        or "ps06-synthetic",
        "pipeline_version": run.pipeline_version,
        "document_count": (primary or {}).get("documents", 0),
        "field_count": (primary or {}).get("fields_evaluated", 0),
        "exclusions": 0,
        "metrics": metrics,
        "pipelines": pipelines,
        "estimated_cost_usd": None if cost is None else f"{cost:.6f}",
        # additive
        "primary_pipeline": primary_name,
        "configurations": (run.configuration_snapshot or {}).get("configs", []),
        "variants": (run.configuration_snapshot or {}).get("variants", []),
        "notes": (run.metrics_json or {}).get("notes", []),
        "error_message": run.error_message,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "results": results,
    }


def evaluation_markdown(session: Session, settings: Settings, run_id: str) -> bytes:
    run = get_evaluation(session, run_id)
    if run.status != "completed" or not run.report_key:
        raise AppError("EVALUATION_NOT_READY", f"evaluation is {run.status}", 409)
    return get_storage(settings).get(run.report_key.replace("report.json", "report.md"))
