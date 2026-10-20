"""Stable, machine-readable application errors (API_CONTRACTS.md §6)."""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or []


def not_found(kind: str, ident: str) -> AppError:
    code = {
        "document": "DOCUMENT_NOT_FOUND",
        "schema": "SCHEMA_NOT_FOUND",
        "evidence": "EVIDENCE_NOT_FOUND",
        "job": "JOB_NOT_FOUND",
        "field": "FIELD_NOT_FOUND",
        "export": "EXPORT_NOT_FOUND",
        "evaluation": "EVALUATION_NOT_FOUND",
        "artifact": "ARTIFACT_NOT_FOUND",
        "page": "PAGE_NOT_FOUND",
    }.get(kind, "NOT_FOUND")
    return AppError(code, f"{kind} {ident} not found", 404)
