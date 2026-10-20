"""Logging: plain text locally, structured JSON on Cloud Run (LOG_FORMAT=json).

JSON lines use Cloud Logging's special fields (severity, logging.googleapis.com/trace)
so entries are grouped per request. Document contents are never logged.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
trace_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
        }
        if rid := request_id_var.get():
            entry["request_id"] = rid
        if trace := trace_var.get():
            entry["logging.googleapis.com/trace"] = trace
        if record.exc_info:
            entry["stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure(fmt: str = "text", level: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level or os.environ.get("LOG_LEVEL", "INFO"))
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.handlers = [handler]
    for noisy in ("uvicorn.access",):  # Cloud Run already records request logs
        logging.getLogger(noisy).setLevel(logging.WARNING)


def trace_from_header(header: str | None, project: str) -> str | None:
    """X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1 -> projects/<p>/traces/TRACE_ID"""
    if not header or not project:
        return None
    trace_id = header.split("/", 1)[0]
    return f"projects/{project}/traces/{trace_id}" if trace_id else None
