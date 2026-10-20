"""Benchmark generator and evaluator on a small slice of the organizer data."""

from __future__ import annotations

import json

from beeproof.config import REPO_DIR
from bench.evaluate import compute_metrics, match_rows, run_benchmark
from bench.generate import generate


def test_generate_and_evaluate(settings, tmp_path):
    out = tmp_path / "bench"
    manifest = generate(REPO_DIR / "ps06_sample_rate_sheets.csv", out, inject=0.0, images=False, limit=6)
    assert manifest["documents"] == 6
    truth = [json.loads(line) for line in (out / "truth.jsonl").read_text().splitlines()]
    assert {t["variant"] for t in truth} == {"digital"}
    report = run_benchmark(settings, out, ["extractor_only", "full"], ["digital"])
    full = report["results"]["full"]["overall"]
    assert full["row_recall"]["value"] == 1.0
    assert full["silent_error_rate"]["num"] == 0
    assert report["results"]["extractor_only"]["overall"]["review_recall"]["num"] == 0
    assert full["doc_inconsistency_recall"]["value"] == 1.0


def test_metrics_count_missed_rows_as_unflagged_problems():
    truth = [
        {
            "product_type": "CD",
            "term_months": 12,
            "institution_name": "B",
            "rate_pct": 1.0,
            "apy_pct": 1.0,
            "min_deposit": 0.0,
            "effective_date": "2026-07-01",
            "doc_inconsistent": False,
        }
    ]
    m = compute_metrics([{"truth": truth, "preds": [], "latency_ms": 10, "pages": 1, "cost": 0.0}])
    assert m["row_recall"]["value"] == 0.0 and m["review_recall"] == {"value": 0.0, "num": 0, "den": 1}
    assert match_rows([], truth)[1] == truth
