"""CSV / JSON exports bound to an immutable result version (FR-12, DOCUMENT_PIPELINE.md §13).

CSV: the organizer sample columns in order, then decision, record_id, result_version, then
provenance columns review_status, review_reasons, inferred_compounding, page. Nulls are empty
cells, decimals are written exactly as stored, UTF-8, CRLF rows, every cell quoted, and text that
could start a spreadsheet formula is prefixed with an apostrophe. An export is not an approval:
needs_review stays True for records that need review or were rejected.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json

from sqlalchemy.orm import Session

from beeproof.config import Settings
from beeproof.contracts import FIELDS
from beeproof.orm import ExportArtifact, utcnow
from beeproof.providers.storage import get_storage
from beeproof.services.errors import AppError, not_found
from beeproof.services.jobs import get_job, log_action
from beeproof.services.results import get_result_version

FORMATS = {"csv": "text/csv; charset=utf-8", "json": "application/json"}
CSV_COLUMNS = [
    "source_file",
    *FIELDS,
    "extraction_confidence",
    "needs_review",
    "decision",
    "record_id",
    "result_version",
    "review_status",
    "review_reasons",
    "inferred_compounding",
    "page",
]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(v) -> str:
    text = "" if v is None else str(v)
    if text.lstrip(" ").startswith(_FORMULA_START) and not _is_number(text):
        text = "'" + text  # neutralize spreadsheet formulas in document text
    return text


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _csv(snapshot: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    w.writerow(CSV_COLUMNS)
    for r in snapshot["records"]:
        v = r["values"]
        w.writerow(
            [
                _cell(r["source_file"]),
                *[_cell(v.get(n)) for n in FIELDS],
                _cell(r["extraction_confidence"]),
                "True" if r["status"] in {"needs_review", "rejected"} else "False",
                _cell(r["decision"]),
                r["id"],
                snapshot["result_version"],
                r["status"],
                _cell(" | ".join(r["review_reasons"])),
                _cell(r["inferred_compounding"]),
                r["page"],
            ]
        )
    return buf.getvalue().encode("utf-8")


def _evidence_manifest(snapshot: dict) -> list[dict]:
    return [
        {
            "record_id": r["id"],
            "field": name,
            "field_id": f["id"],
            "raw_value": f["raw_value"],
            "normalized_value": f["normalized_value"],
            "evidence": f["evidence"],
        }
        for r in snapshot["records"]
        for name, f in r["fields"].items()
        if f["normalized_value"] is not None
    ]


def create_export(
    session: Session,
    settings: Settings,
    job_id: str,
    fmt: str,
    result_version: int | None,
    include_evidence_manifest: bool,
) -> ExportArtifact:
    if fmt not in FORMATS:
        raise AppError("UNSUPPORTED_EXPORT_FORMAT", f"format must be one of {sorted(FORMATS)}", 422)
    job = get_job(session, job_id)
    snapshot = get_result_version(session, job, result_version)
    exp = ExportArtifact(
        job_id=job.id,
        result_version=snapshot["result_version"],
        format=fmt,
        include_evidence_manifest=include_evidence_manifest,
        status="pending",
    )
    session.add(exp)
    session.flush()
    if fmt == "csv":
        body = _csv(snapshot)
    else:
        payload = dict(snapshot)
        if include_evidence_manifest:
            payload["evidence_manifest"] = _evidence_manifest(snapshot)
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    storage = get_storage(settings)
    try:
        exp.storage_key = storage.put(f"jobs/{job.id}/exports/{exp.id}.{fmt}", body, FORMATS[fmt])
        if fmt == "csv" and include_evidence_manifest:
            storage.put(
                f"jobs/{job.id}/exports/{exp.id}.manifest.json",
                json.dumps(_evidence_manifest(snapshot), indent=2).encode("utf-8"),
                "application/json",
            )
        verified = hashlib.sha256(storage.get(exp.storage_key)).hexdigest() == digest
    except Exception as exc:  # storage failure
        exp.status, exp.error_code = "failed", "EXPORT_FAILED"
        session.flush()
        raise AppError("EXPORT_FAILED", f"export could not be written: {exc}", 500) from exc
    if not verified:  # never mark ready before the artifact is verified (DATA_MODEL.md §8)
        exp.status, exp.error_code = "failed", "EXPORT_FAILED"
        session.flush()
        raise AppError("EXPORT_FAILED", "stored export did not match its checksum", 500)
    exp.sha256, exp.size_bytes = digest, len(body)
    exp.status, exp.completed_at = "ready", utcnow()
    log_action(
        session,
        job,
        "export",
        "export_service",
        "export",
        "EXPORT_CREATED",
        f"{fmt} export of result version {exp.result_version} ({exp.size_bytes} bytes)",
        output_refs=[f"export:{exp.id}"],
    )
    return exp


def get_export(session: Session, export_id: str) -> ExportArtifact:
    exp = session.get(ExportArtifact, export_id)
    if exp is None:
        raise not_found("export", export_id)
    return exp


def export_payload(session: Session, settings: Settings, export_id: str, part: str = "data") -> tuple[bytes, str, str]:
    exp = get_export(session, export_id)
    if exp.status != "ready" or not exp.storage_key:
        raise AppError("EXPORT_NOT_READY", f"export is {exp.status}", 409)
    storage = get_storage(settings)
    if not storage.exists(exp.storage_key):
        raise AppError("EXPORT_FAILED", "the export artifact is missing from storage", 410)
    stem = f"beeproof_{exp.job_id[:8]}_v{exp.result_version}"
    if part == "manifest":
        if not (exp.format == "csv" and exp.include_evidence_manifest):
            raise AppError("EXPORT_NOT_FOUND", "this export has no separate evidence manifest", 404)
        return (
            storage.get(f"jobs/{exp.job_id}/exports/{exp.id}.manifest.json"),
            "application/json",
            f"{stem}_evidence.json",
        )
    return storage.get(exp.storage_key), FORMATS[exp.format], f"{stem}.{exp.format}"
