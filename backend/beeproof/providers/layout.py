"""Witness A: native PDF words or Document AI OCR/table cells (ARCHITECTURE.md §6.2)."""

from __future__ import annotations

import logging
import time

from beeproof.config import Settings
from beeproof.contracts import ActionEvent, Cell, LayoutReading, PageCtx, Word

log = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, code: str, message: str, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.transient = transient


class DocAIReader:
    def __init__(self, settings: Settings):
        from google.cloud import documentai  # lazy: optional dependency

        self._documentai = documentai
        self.client = documentai.DocumentProcessorServiceClient(
            client_options={"api_endpoint": f"{settings.docai_location}-documentai.googleapis.com"}
        )
        self.name = self.client.processor_path(settings.project, settings.docai_location, settings.docai_processor_id)

    def read(self, png_bytes: bytes) -> LayoutReading:
        from google.api_core import exceptions as gexc
        from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

        documentai = self._documentai

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(min=1, max=8),
            reraise=True,
            retry=retry_if_exception_type(
                (gexc.TooManyRequests, gexc.ServiceUnavailable, gexc.InternalServerError, gexc.DeadlineExceeded)
            ),
        )
        def call():
            return self.client.process_document(
                request=documentai.ProcessRequest(
                    name=self.name, raw_document=documentai.RawDocument(content=png_bytes, mime_type="image/png")
                )
            )

        try:
            doc = call().document
        except gexc.TooManyRequests as exc:
            raise ProviderError("PROVIDER_QUOTA_EXCEEDED", str(exc), transient=True) from exc
        except gexc.GoogleAPICallError as exc:
            raise ProviderError("PARSER_FAILED", str(exc), transient=True) from exc

        def text_of(layout) -> str:
            return "".join(
                doc.text[int(s.start_index or 0) : int(s.end_index)] for s in layout.text_anchor.text_segments
            ).strip()

        def bbox_of(layout) -> list[float]:
            vs = layout.bounding_poly.normalized_vertices
            if not vs:
                return [0, 0, 0, 0]
            xs, ys = [v.x for v in vs], [v.y for v in vs]
            return [min(xs), min(ys), max(xs), max(ys)]

        reading = LayoutReading(provider="docai")
        if not doc.pages:
            return reading
        page = doc.pages[0]
        for tok in page.tokens:
            b = bbox_of(tok.layout)
            reading.words.append(Word(b[0], b[1], b[2], b[3], text_of(tok.layout)))
        for t_idx, table in enumerate(page.tables):
            r_idx = 0
            for is_header, rows in ((True, table.header_rows), (False, table.body_rows)):
                for row in rows:
                    for c_idx, cell in enumerate(row.cells):
                        reading.cells.append(
                            Cell(
                                text=text_of(cell.layout),
                                bbox=bbox_of(cell.layout),
                                conf=float(cell.layout.confidence or 0.0),
                                row_idx=r_idx,
                                col_idx=c_idx,
                                is_header=is_header,
                                table_idx=t_idx,
                            )
                        )
                    r_idx += 1
        return reading


class LayoutWitness:
    """Chooses PyMuPDF words for digital pages and Document AI for scans/images."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._docai: DocAIReader | None = None

    @property
    def docai_available(self) -> bool:
        s = self.settings
        return bool(s.project and s.docai_processor_id) and s.layout_provider in {"auto", "docai"}

    def _docai_reader(self) -> DocAIReader:
        if self._docai is None:
            self._docai = DocAIReader(self.settings)
        return self._docai

    def read(self, ctx: PageCtx) -> tuple[LayoutReading, ActionEvent]:
        provider = self.settings.layout_provider
        t0 = time.perf_counter()
        if provider == "none":
            reading = LayoutReading(provider="none")
        elif ctx.kind == "digital" and provider in {"auto", "pymupdf"}:
            reading = LayoutReading(provider="pymupdf", words=list(ctx.words))
        elif self.docai_available:
            reading = self._docai_reader().read(ctx.png_bytes)
        else:
            reading = LayoutReading(provider="none")
        ms = int((time.perf_counter() - t0) * 1000)
        if reading.provider == "docai":
            cost = self.settings.docai_usd_per_page or None
        else:
            cost = 0.0
        ev = ActionEvent(
            stage="parsing",
            agent_name="layout_agent",
            action_type="read_layout",
            reason_code=f"PAGE_{ctx.kind.upper()}",
            summary=(
                f"page {ctx.page_no}: {reading.provider} returned {len(reading.words)} words, "
                f"{len(reading.cells)} table cells"
            ),
            result_status="succeeded" if reading.provider != "none" else "skipped",
            duration_ms=ms,
            model_name=reading.provider if reading.provider != "none" else None,
            estimated_cost_usd=cost,
        )
        return reading, ev
