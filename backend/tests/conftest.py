"""Shared fixtures. No network calls: providers are the deterministic text-layer
extractor or in-test fakes; storage and database live in a temp directory."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from beeproof.config import Settings, set_settings
from beeproof.providers.queue import reset_queue
from beeproof.providers.storage import reset_storage


def make_rate_sheet(
    rows: list[tuple[str, str, str, str, str]],
    institution: str = "Test Federal Bank",
    date_line: str = "Rates effective July 1, 2026",
) -> bytes:
    """Classic one-table rate sheet: (product, term, rate, apy, minimum)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = letter[1] - 72
    c.setFont("Helvetica-Bold", 20)
    c.drawString(54, y, institution)
    c.setFont("Helvetica", 11)
    c.drawString(54, y - 22, "Deposit Rate Sheet")
    c.drawString(54, y - 38, date_line)
    y -= 80
    widths = [170, 90, 80, 80, 90]
    c.setFont("Helvetica-Bold", 10.5)
    x = 54
    for h, w in zip(["Product", "Term", "Rate", "APY", "Minimum"], widths):
        c.drawString(x, y, h)
        x += w
    c.setFont("Helvetica", 10.5)
    for row in rows:
        y -= 20
        x = 54
        for v, w in zip(row, widths):
            c.drawString(x, y, v)
            x += w
    c.showPage()
    c.save()
    return buf.getvalue()


SAMPLE_ROWS = [
    ("Money Market", "-", "1.076%", "1.162%", "$100"),  # physics violation (organizer row 1)
    ("CD", "36 Months", "3.097%", "3.119%", "$1,000"),  # clean
    ("Savings", "-", "2.994%", "3.084%", "$500"),  # physics violation
]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        local_store_dir=tmp_path / "store",
        worker_mode="off",
        extractor="heuristic",
        bench_dir=tmp_path / "bench",
    )
    set_settings(s)
    reset_storage()
    reset_queue()
    yield s
    reset_storage()
    reset_queue()


@pytest.fixture
def sample_pdf() -> bytes:
    return make_rate_sheet(SAMPLE_ROWS)


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from beeproof.api.app import create_app

    with TestClient(create_app(start_worker=False)) as c:
        yield c


@pytest.fixture
def run_worker(settings):
    from beeproof.worker import Worker

    def _run(extractor=None, layout=None) -> bool:
        return Worker(settings, extractor=extractor, layout=layout).run_once()

    return _run
