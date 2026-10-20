"""Benchmark + ablation (ARCHITECTURE.md §12, EVALUATION.md §5-9).

Runs the pipeline over the synthetic dataset under several flag configurations
and reports metrics with explicit denominators. Every number comes from a run;
nothing is filled in by hand.

Usage:
  python -m bench.evaluate                       # all configs, variants the extractor can read
  python -m bench.evaluate --configs full --variants digital,scan --limit 20
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from beeproof.config import PIPELINE_VERSION, Settings, get_settings
from beeproof.contracts import FIELDS, RecordDraft
from beeproof.pipeline.orchestrator import Pipeline
from beeproof.providers.extractors import HeuristicExtractor, build_extractor
from beeproof.providers.layout import LayoutWitness, ProviderError

_OFF = {"witness_a": False, "validators": False, "relook": False, "memory": False}
_FULL = {"witness_a": True, "validators": True, "relook": True, "memory": False}

# extractor: "configured" = settings.extractor; "heuristic" = text-layer parser; "llm" = configured, must not be heuristic
CONFIGS: dict[str, dict] = {
    "extractor_only": {"flags": _OFF, "extractor": "configured"},
    "validators": {"flags": {**_OFF, "validators": True}, "extractor": "configured"},
    "validators_witness_a": {"flags": {**_FULL, "relook": False}, "extractor": "configured"},
    "full": {"flags": _FULL, "extractor": "configured"},
    # frontend pipeline names (EVALUATION.md §4 baselines)
    "native": {"flags": _OFF, "extractor": "heuristic"},
    "single_pass": {"flags": _OFF, "extractor": "llm"},
    "evidence_first": {"flags": _FULL, "extractor": "configured"},
}
DEFAULT_CONFIGS = ["extractor_only", "validators", "validators_witness_a", "full"]
TABLE_SOURCES = {"table_cell", "page_header"}


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def default_variants(settings: Settings) -> list[str]:
    """The text-layer extractor cannot read images; do not report a meaningless 0."""
    if settings.extractor == "heuristic":
        return ["digital"]
    return ["digital", "scan", "photo"]


def field_correct(name: str, pred, truth) -> bool:
    if pred is None or truth is None:
        return pred is None and truth is None
    if name in {"rate_pct", "apy_pct", "min_deposit"}:
        return abs(float(pred) - float(truth)) <= 1e-6
    if name == "term_months":
        return int(pred) == int(truth)
    return str(pred).strip().lower() == str(truth).strip().lower()


def match_rows(preds: list[RecordDraft], truths: list[dict]):
    """Match by (product_type, term_months); leftovers greedily by number of agreeing fields."""
    pairs, used_p, used_t = [], set(), set()
    for ti, t in enumerate(truths):
        for pi, p in enumerate(preds):
            if (
                pi not in used_p
                and p.value("product_type") == t["product_type"]
                and p.value("term_months") == t["term_months"]
            ):
                pairs.append((p, t))
                used_p.add(pi)
                used_t.add(ti)
                break
    for ti, t in enumerate(truths):
        if ti in used_t:
            continue
        best, best_score = None, 2  # require at least 3 agreeing fields
        for pi, p in enumerate(preds):
            if pi in used_p:
                continue
            score = sum(field_correct(n, p.value(n), t[n]) for n in FIELDS)
            if score > best_score:
                best, best_score = pi, score
        if best is not None:
            pairs.append((preds[best], t))
            used_p.add(best)
            used_t.add(ti)
    missed = [t for ti, t in enumerate(truths) if ti not in used_t]
    spurious = [p for pi, p in enumerate(preds) if pi not in used_p]
    return pairs, missed, spurious


def _ratio(num: int, den: int) -> dict:
    return {"value": round(num / den, 4) if den else None, "num": int(num), "den": int(den)}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, max(0, round(q * (len(values) - 1))))], 1)


def compute_metrics(runs: list[dict]) -> dict:
    """runs: [{truth: [...], preds: [RecordDraft], latency_ms, pages, cost, error?}]"""
    field_hits, field_total = defaultdict(int), defaultdict(int)
    c = defaultdict(int)
    latencies, pages, costs = [], 0, []

    for run in runs:
        c["errors"] += bool(run.get("error"))
        pages += run["pages"]
        if run["pages"]:
            latencies.extend([run["latency_ms"] / run["pages"]] * run["pages"])
        costs.append(run.get("cost"))
        for p in run["preds"]:  # evidence coverage over every predicted printed value
            for f in p.fields.values():
                if f.value is not None and f.source_type in TABLE_SOURCES:
                    c["ev_needed"] += 1
                    c["ev_have"] += bool(f.bbox)
        pairs, missed, spurious = match_rows(run["preds"], run["truth"])
        c["rows"] += len(run["truth"])
        c["matched"] += len(pairs)
        c["preds"] += len(run["preds"])
        for t in missed:  # a missed row is an unflagged problem
            for n in FIELDS:
                field_total[n] += 1
            c["problems"] += 1
            c["di_label"] += t["doc_inconsistent"]
            c["inj"] += bool(t.get("injected_error"))
            c["nonincons"] += not t["doc_inconsistent"]
        for p, t in pairs:
            wrong = []
            for n in FIELDS:
                field_total[n] += 1
                ok = field_correct(n, p.value(n), t[n])
                field_hits[n] += ok
                if not ok:
                    wrong.append(n)
            flag = p.needs_review
            problem = (
                bool(wrong) or t["doc_inconsistent"] or bool(t.get("injected_error")) or bool(t.get("term_missing"))
            )
            c["exact"] += not wrong
            if p.decision.value == "AUTO_APPROVED":
                c["auto"] += 1
                c["auto_wrong"] += bool(wrong)
                c["auto_fields"] += len(FIELDS)
                c["auto_fields_wrong"] += len(wrong)
            c["flagged"] += flag
            c["flagged_true"] += flag and problem
            c["problems"] += problem
            c["problems_flagged"] += flag and problem
            if not t["doc_inconsistent"]:
                c["nonincons"] += 1
                c["nonincons_flagged"] += flag
            c["di_label"] += t["doc_inconsistent"]
            if p.decision.value == "DOCUMENT_INCONSISTENCY":
                c["di_pred"] += 1
                c["di_tp"] += t["doc_inconsistent"]
            if t.get("injected_error"):
                c["inj"] += 1
                c["inj_flagged"] += flag
        for p in spurious:
            c["flagged"] += p.needs_review
            if p.decision.value == "AUTO_APPROVED":
                c["auto"] += 1
                c["auto_wrong"] += 1  # a spurious auto-approved row is a silent error
                c["auto_fields"] += len(FIELDS)
                c["auto_fields_wrong"] += len(FIELDS)
            c["di_pred"] += p.decision.value == "DOCUMENT_INCONSISTENCY"

    total_seconds = sum(r["latency_ms"] for r in runs) / 1000
    known_costs = [x for x in costs if x is not None]
    per_field = {n: _ratio(field_hits[n], field_total[n]) for n in FIELDS}
    macro = [v["value"] for v in per_field.values() if v["value"] is not None]
    return {
        "documents": len(runs),
        "pages": pages,
        "documents_failed": c["errors"],
        "field_accuracy": {"macro": round(statistics.mean(macro), 4) if macro else None, "per_field": per_field},
        "row_exact_match": _ratio(c["exact"], c["rows"]),
        "row_recall": _ratio(c["matched"], c["rows"]),
        "row_precision": _ratio(c["matched"], c["preds"]),
        "auto_approve_rate": _ratio(c["auto"], c["preds"]),
        "review_precision": _ratio(c["flagged_true"], c["flagged"]),
        "review_recall": _ratio(c["problems_flagged"], c["problems"]),
        "review_rate_excluding_doc_inconsistent": _ratio(c["nonincons_flagged"], c["nonincons"]),
        "silent_error_rate": _ratio(c["auto_wrong"], c["auto"]),
        "false_auto_accept_rate": _ratio(c["auto_fields_wrong"], c["auto_fields"]),
        "evidence_coverage": _ratio(c["ev_have"], c["ev_needed"]),
        "fields_evaluated": sum(field_total.values()),
        "doc_inconsistency_precision": _ratio(c["di_tp"], c["di_pred"]),
        "doc_inconsistency_recall": _ratio(c["di_tp"], c["di_label"]),
        "injected_error_detection": _ratio(c["inj_flagged"], c["inj"]),
        "throughput_pages_per_min": round(pages / total_seconds * 60, 1) if total_seconds else None,
        "latency_ms_per_page": {"p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95)},
        "estimated_cost_usd": round(sum(known_costs), 6) if len(known_costs) == len(costs) else None,
        "cost_note": None if len(known_costs) == len(costs) else "provider pricing not configured; cost unknown",
    }


def run_benchmark(
    settings: Settings, dataset: Path, configs: list[str], variants: list[str], limit: int | None = None, progress=None
) -> dict:
    truth_by_file: dict[str, list[dict]] = defaultdict(list)
    for line in (dataset / "truth.jsonl").read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        truth_by_file[t["file"]].append(t)
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    files = [(f, ts) for f, ts in truth_by_file.items() if ts[0]["variant"] in variants]
    doc_ids = sorted({ts[0]["doc_id"] for _, ts in files})
    if limit:
        doc_ids = doc_ids[:limit]
        files = [(f, ts) for f, ts in files if ts[0]["doc_id"] in set(doc_ids)]

    extractor = build_extractor(settings)
    heuristic = HeuristicExtractor()
    layout = LayoutWitness(settings)
    started = datetime.now(UTC)
    results: dict[str, dict] = {}
    for cname in configs:
        cfg = CONFIGS[cname]
        if cfg["extractor"] == "llm" and extractor.name.startswith("heuristic"):
            results[cname] = {
                "skipped": "no LLM extractor configured (set EXTRACTOR=gemini); not run",
                "extractor": None,
                "overall": None,
                "by_variant": {},
            }
            continue
        cfg_extractor = heuristic if cfg["extractor"] == "heuristic" else extractor
        pipeline = Pipeline(
            replace(settings, flags=replace(settings.flags, **cfg["flags"])), extractor=cfg_extractor, layout=layout
        )
        by_variant: dict[str, list[dict]] = defaultdict(list)
        for i, (rel, truths) in enumerate(files):
            t0 = time.perf_counter()
            truths = sorted(truths, key=lambda t: t["row_index"])
            try:
                res = pipeline.run((dataset / rel).read_bytes(), Path(rel).name)
                run = {
                    "truth": truths,
                    "preds": res.records,
                    "latency_ms": res.latency_ms,
                    "pages": len(res.pages),
                    "cost": res.estimated_cost_usd,
                }
            except ProviderError as exc:
                run = {
                    "truth": truths,
                    "preds": [],
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "pages": 1,
                    "cost": None,
                    "error": f"{exc.code}: {exc}",
                }
            by_variant[truths[0]["variant"]].append(run)
            if progress:
                progress(cname, i + 1, len(files))
        results[cname] = {
            "extractor": cfg_extractor.name,
            "flags": cfg["flags"],
            "overall": compute_metrics([r for runs in by_variant.values() for r in runs]),
            "by_variant": {v: compute_metrics(runs) for v, runs in sorted(by_variant.items())},
        }
    notes = [
        "Synthetic documents rendered from the organizer CSV; not a claim about real-world accuracy.",
        "A row is a 'problem' if any field is wrong, the printed values break APY physics, a CD has no printed "
        "term, or an error was injected.",
        "review_rate_excluding_doc_inconsistent drops rows whose printed APY is impossible by construction.",
        "Without Witness A, agreement cannot reach 1.0, so DOCUMENT_INCONSISTENCY cannot fire in the first two configs.",
    ]
    if extractor.name.startswith("heuristic"):
        notes.append(
            "The heuristic extractor reads the PDF text layer and doubles as the deterministic-parsing "
            "baseline; on digital PDFs Witness A (PyMuPDF words) is not independent of it."
        )
    return {
        "dataset_version": manifest.get("dataset_version"),
        "dataset_dir": str(dataset),
        "pipeline_version": PIPELINE_VERSION,
        "git_commit": _git_commit(),
        "extractor": extractor.name,
        "layout_provider": settings.layout_provider,
        "docai_configured": bool(settings.docai_processor_id),
        "tau": settings.tau,
        "configs": configs,
        "variants": variants,
        "documents_per_variant": len(doc_ids),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "notes": notes,
        "results": results,
    }


def _fmt(m: dict | None) -> str:
    if not m or m.get("value") is None:
        return "n/a"
    return f"{m['value'] * 100:.1f}% ({m['num']}/{m['den']})"


def _acc(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render_markdown(report: dict) -> str:
    lines = [
        "# BeeProof benchmark report",
        "",
        f"- Dataset: `{report['dataset_version']}` · documents per variant: {report['documents_per_variant']}",
        f"- Pipeline {report['pipeline_version']} · commit `{report['git_commit']}` · extractor "
        f"`{report['extractor']}` · layout `{report['layout_provider']}` · tau {report['tau']}",
        f"- Variants: {', '.join(report['variants'])} · run {report['started_at']} → {report['finished_at']}",
        "",
        "## Ablation (all variants)",
        "",
        "| Pipeline | Field acc. (macro) | Row exact | Auto-approve | Review recall | Review precision "
        "| Silent errors | Doc-inconsistency recall | Pages/min |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, res in report["results"].items():
        o = res["overall"]
        if o is None:
            lines.append(f"| {name} | {res['skipped']} | | | | | | | |")
            continue
        lines.append(
            f"| {name} | {_acc(o['field_accuracy']['macro'])} | {_fmt(o['row_exact_match'])} | "
            f"{_fmt(o['auto_approve_rate'])} | {_fmt(o['review_recall'])} | {_fmt(o['review_precision'])} | "
            f"{_fmt(o['silent_error_rate'])} | {_fmt(o['doc_inconsistency_recall'])} | "
            f"{o['throughput_pages_per_min'] or 'n/a'} |"
        )
    lines += ["", "## By variant", ""]
    for name, res in report["results"].items():
        if res["overall"] is None:
            continue
        lines += [
            f"### {name}",
            "",
            "| Variant | Row recall | Field acc. | Silent errors | Review rate (excl. doc-inconsistent) "
            "| p50 / p95 ms per page |",
            "|---|---|---|---|---|---|",
        ]
        for v, m in res["by_variant"].items():
            lat = m["latency_ms_per_page"]
            lines.append(
                f"| {v} | {_fmt(m['row_recall'])} | {_acc(m['field_accuracy']['macro'])} | "
                f"{_fmt(m['silent_error_rate'])} | {_fmt(m['review_rate_excluding_doc_inconsistent'])} | "
                f"{lat['p50']} / {lat['p95']} |"
            )
        lines.append("")
    lines += ["## Notes", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(description="Run the BeeProof benchmark and ablation")
    ap.add_argument("--dataset", type=Path, default=s.bench_dir)
    ap.add_argument("--configs", default=",".join(DEFAULT_CONFIGS), help=f"comma list of {list(CONFIGS)}")
    ap.add_argument("--variants", default=None, help="comma list of digital,scan,photo")
    ap.add_argument("--limit", type=int, default=None, help="max documents")
    ap.add_argument("--out", type=Path, default=None, help="output dir (default: dataset dir)")
    a = ap.parse_args()
    configs = [x.strip() for x in a.configs.split(",") if x.strip()]
    unknown = [x for x in configs if x not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown config(s): {unknown}")
    variants = a.variants.split(",") if a.variants else default_variants(s)
    report = run_benchmark(
        s,
        a.dataset,
        configs,
        variants,
        a.limit,
        progress=lambda cn, i, n: print(f"\r{cn}: {i}/{n}", end="", flush=True),
    )
    print()
    out = a.out or a.dataset
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = render_markdown(report)
    (out / "report.md").write_text(md, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(md)


if __name__ == "__main__":
    main()
