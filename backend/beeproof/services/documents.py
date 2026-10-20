"""Document ingestion (DOCUMENT_PIPELINE.md §2): validate, hash, store, render previews."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from beeproof.config import Settings
from beeproof.orm import Document, DocumentPage, new_id
from beeproof.pipeline.triage import EXT_BY_KIND, MIME_BY_KIND, TriageError, sniff, triage
from beeproof.providers.storage import get_storage
from beeproof.services.errors import AppError, not_found

ACTIVE_STATES = {"preprocessing", "parsing", "extracting", "validating", "repairing"}


def _safe_display_name(name: str | None) -> str:
    base = PurePath((name or "upload").replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ]+", "_", base).strip() or "upload"
    return base[:200]


def create_document(
    session: Session, settings: Settings, filename: str | None, data: bytes, declared_mime: str | None = None
) -> Document:
    if not data:
        raise AppError("INVALID_FILE", "file is empty", 400)
    if len(data) > settings.max_upload_bytes:
        raise AppError("FILE_TOO_LARGE", f"file exceeds {settings.max_upload_bytes} bytes", 413)
    kind = sniff(data)
    if kind is None:
        raise AppError("UNSUPPORTED_FILE_TYPE", "only PDF, PNG, JPEG and TIFF files are supported", 415)
    display = _safe_display_name(filename)
    ext = PurePath(display).suffix.lower()
    if ext and ext not in EXT_BY_KIND[kind]:
        raise AppError("INVALID_FILE", f"file extension {ext} does not match its content ({kind})", 400)
    if declared_mime and declared_mime not in {"application/octet-stream", MIME_BY_KIND[kind]}:
        raise AppError("INVALID_FILE", f"declared type {declared_mime} does not match content ({kind})", 400)

    try:
        pages = triage(data, display, settings.max_document_pages, settings.max_image_pixels)
    except TriageError as exc:
        status = 413 if exc.code in {"PAGE_LIMIT_EXCEEDED", "FILE_TOO_LARGE"} else 400
        raise AppError(exc.code, exc.message, status) from exc

    sha = hashlib.sha256(data).hexdigest()
    doc_id = new_id()
    storage = get_storage(settings)
    key = storage.put(f"documents/{doc_id}/original/{sha}", data, MIME_BY_KIND[kind])
    duplicate = session.scalar(select(Document.id).where(Document.sha256 == sha).limit(1))
    doc = Document(
        id=doc_id,
        original_filename=display,
        mime_type=MIME_BY_KIND[kind],
        size_bytes=len(data),
        sha256=sha,
        storage_key=key,
        page_count=len(pages),
        status="stored",
        metadata_json={"duplicate_of": duplicate} if duplicate else {},
    )
    session.add(doc)
    for p in pages:
        pkey = storage.put(f"documents/{doc_id}/pages/{p.page_no}.png", p.png_bytes, "image/png")
        session.add(
            DocumentPage(
                document_id=doc_id,
                page_number=p.page_no,
                width=p.width,
                height=p.height,
                rotation=p.rotation,
                kind=p.kind,
                preview_key=pkey,
                native_text_available=p.kind == "digital",
            )
        )
    session.flush()
    return doc


def get_document(session: Session, document_id: str) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise not_found("document", document_id)
    return doc


def list_documents(session: Session, limit: int, offset: int) -> tuple[list[Document], int | None]:
    rows = list(
        session.scalars(
            select(Document).order_by(Document.created_at.desc(), Document.id).offset(offset).limit(limit + 1)
        )
    )
    nxt = offset + limit if len(rows) > limit else None
    return rows[:limit], nxt


def get_page(session: Session, document_id: str, page_number: int) -> DocumentPage:
    get_document(session, document_id)
    page = session.scalar(
        select(DocumentPage).where(DocumentPage.document_id == document_id, DocumentPage.page_number == page_number)
    )
    if page is None:
        raise not_found("page", f"{document_id}/{page_number}")
    return page


def delete_document(session: Session, settings: Settings, document_id: str) -> None:
    doc = get_document(session, document_id)
    if any(j.status in ACTIVE_STATES for j in doc.jobs):
        raise AppError("JOB_STATE_CONFLICT", "document has a job in progress; cancel it first", 409)
    storage = get_storage(settings)
    storage.delete_prefix(f"documents/{doc.id}")
    for job in doc.jobs:
        storage.delete_prefix(f"jobs/{job.id}")
    session.delete(doc)
