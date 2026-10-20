"""Witness B: schema-aware row extraction (ARCHITECTURE.md §6.3).

* GeminiExtractor - Vertex AI Gemini vision with structured output.
* HeuristicExtractor - deterministic table parser over the PDF text layer.
  Works offline for digital PDFs; also the "native text + deterministic parsing"
  baseline from EVALUATION.md §4. Returns nothing for pages without a text layer.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
from dataclasses import dataclass
from typing import Protocol

from beeproof.config import Settings
from beeproof.contracts import GeminiField, GeminiPage, GeminiRow, PageCtx, RelookAnswer, Word, bbox_to_box2d
from beeproof.domain.normalizers import map_header, parse_date
from beeproof.pipeline.geometry import union, words_in
from beeproof.prompts import EXTRACT_PROMPT, RELOOK_PROMPT
from beeproof.providers.layout import ProviderError

log = logging.getLogger(__name__)


@dataclass
class CallInfo:
    model_name: str
    duration_ms: int = 0
    token_usage: dict | None = None
    estimated_cost_usd: float | None = None


class Extractor(Protocol):
    name: str

    def extract(self, ctx: PageCtx, hints: str = "") -> tuple[GeminiPage, CallInfo]: ...

    def relook(
        self,
        ctx: PageCtx,
        crop_png: bytes,
        header_png: bytes | None,
        bbox: list[float],
        header: str,
        product_label: str,
        readings: list[str],
    ) -> tuple[RelookAnswer, CallInfo]: ...


# --------------------------------------------------------------------------- heuristic
_PCT = re.compile(r"\d+(?:\.\d+)?\s*%")
_DATE_PATTERNS = [
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", re.I),
]
_TERM_IN_LABEL = re.compile(r"\b(\d+)\s*-?\s*(months?|mos?|mo|years?|yrs?|year)\b", re.I)


def _lines(words: list[Word]) -> list[list[Word]]:
    """Cluster words into text lines by vertical center."""
    if not words:
        return []
    heights = [w.y1 - w.y0 for w in words if w.y1 > w.y0]
    tol = (statistics.median(heights) if heights else 0.01) * 0.5
    groups: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: (w.y0 + w.y1) / 2):
        yc = (w.y0 + w.y1) / 2
        if groups and abs(yc - groups[-1][0]) <= tol:
            groups[-1][1].append(w)
        else:
            groups.append((yc, [w]))
    return [sorted(ws, key=lambda w: w.x0) for _, ws in groups]


def _phrases(line: list[Word], aspect_hw: float) -> list[list[Word]]:
    """Group a line's words into phrases; a gap wider than ~1 line height splits columns.

    aspect_hw = page height / page width, used to compare normalized x-gaps with
    normalized heights in the same physical units.
    """
    if not line:
        return []
    groups = [[line[0]]]
    for prev, w in zip(line, line[1:]):
        h = max(prev.y1 - prev.y0, w.y1 - w.y0, 1e-6)
        gap = (w.x0 - prev.x1) / aspect_hw
        if gap > 1.0 * h:
            groups.append([w])
        else:
            groups[-1].append(w)
    return groups


def _text(ws: list[Word]) -> str:
    return " ".join(w.text for w in ws)


def _bbox(ws: list[Word]) -> list[float]:
    return union([w.bbox for w in ws])


def _field(ws: list[Word], raw: str | None = None) -> GeminiField:
    return GeminiField(raw=raw if raw is not None else _text(ws), box_2d=bbox_to_box2d(_bbox(ws)))


class HeuristicExtractor:
    name = "heuristic-textlayer-v1"

    def extract(self, ctx: PageCtx, hints: str = "") -> tuple[GeminiPage, CallInfo]:
        t0 = time.perf_counter()
        aspect_hw = ctx.height / ctx.width if ctx.width else 1.29
        page = self.parse_words(ctx.words, aspect_hw)
        return page, CallInfo(
            model_name=self.name, duration_ms=int((time.perf_counter() - t0) * 1000), estimated_cost_usd=0.0
        )

    def parse_words(self, words: list[Word], aspect_hw: float) -> GeminiPage:
        lines = _lines(list(words))
        page = GeminiPage()
        if not lines:
            return page

        headers: list[tuple[int, list[tuple[float, str | None, str]]]] = []
        for i, line in enumerate(lines):
            if _PCT.search(_text(line)):
                continue
            cols = [(ph[0].x0, map_header(_text(ph)), _text(ph)) for ph in _phrases(line, aspect_hw)]
            mapped = {c[1] for c in cols if c[1]}
            if len(mapped & {"rate_pct", "apy_pct", "min_deposit", "term_months"}) >= 2:
                headers.append((i, cols))
                for c in cols:
                    if c[2] not in page.column_headers:
                        page.column_headers.append(c[2])

        header_idx = {h[0] for h in headers}
        for h_no, (start, cols) in enumerate(headers):
            end = headers[h_no + 1][0] if h_no + 1 < len(headers) else len(lines)
            starts = [c[0] - 0.01 for c in cols]
            fields = [c[1] for c in cols]
            if fields and fields[0] is None:
                fields[0] = "product_type"
            for line in lines[start + 1 : end]:
                if not _PCT.search(_text(line)):
                    continue
                cells: dict[str, list[Word]] = {}
                for ph in _phrases(line, aspect_hw):
                    xc = (ph[0].x0 + ph[-1].x1) / 2
                    col = max((k for k, s in enumerate(starts) if s <= xc), default=0)
                    name = fields[col] or f"col{col}"
                    cells.setdefault(name, []).extend(ph)
                label_ws = cells.get("product_type")
                if not label_ws:
                    continue
                row = GeminiRow(product_label=_field(label_ws))
                if "term_months" in cells:
                    row.term = _field(cells["term_months"])
                else:
                    m = _TERM_IN_LABEL.search(_text(label_ws))
                    if m:
                        term_ws = [w for w in label_ws if w.text and w.text in m.group(0)]
                        row.term = _field(term_ws or label_ws, raw=m.group(0))
                if "rate_pct" in cells:
                    row.rate = _field(cells["rate_pct"])
                if "apy_pct" in cells:
                    row.apy = _field(cells["apy_pct"])
                if "min_deposit" in cells:
                    row.min_deposit = _field(cells["min_deposit"])
                page.rows.append(row)

        # institution: first top line that is not a header, date or number line
        for i, line in enumerate(lines[:6]):
            text = _text(line)
            if i in header_idx or re.search(r"\d", text) or len(text) < 3:
                continue
            if i > 0 and re.search(r"(?i)\b(rate sheet|rates|deposit|effective|schedule)\b", text):
                continue
            page.institution_name = _field(line)
            break

        # effective date: prefer lines mentioning "effective" / "as of"
        candidates = []
        for line in lines:
            text = _text(line)
            for pat in _DATE_PATTERNS:
                m = pat.search(text)
                if m and parse_date(m.group(0)):
                    score = 0 if re.search(r"(?i)effective|as of|accurate", text) else 1
                    candidates.append((score, line, m.group(0)))
                    break
        if candidates:
            _, line, raw = min(candidates, key=lambda c: c[0])
            raw_tokens = raw.replace(",", " ").split()
            date_ws = [w for w in line if w.text.strip(",.") in raw_tokens]
            page.effective_date = _field(date_ws or line, raw=raw)

        page.footnotes = [_text(line) for line in lines if _text(line).startswith(("*", "†"))]
        return page

    def relook(self, ctx, crop_png, header_png, bbox, header, product_label, readings):
        t0 = time.perf_counter()
        raw = _text(words_in(ctx.words, bbox, pad=0.0))
        return (
            RelookAnswer(raw=raw, legible=bool(raw)),
            CallInfo(model_name=self.name, duration_ms=int((time.perf_counter() - t0) * 1000), estimated_cost_usd=0.0),
        )


# --------------------------------------------------------------------------- gemini
class GeminiExtractor:
    def __init__(self, settings: Settings):
        from google import genai  # lazy: optional dependency

        if not settings.gemini_model:
            raise ProviderError("PROVIDER_NOT_CONFIGURED", "GEMINI_MODEL is not set")
        self.settings = settings
        self.name = settings.gemini_model
        self.client = genai.Client(vertexai=True, project=settings.project or None, location=settings.gemini_location)

    def _generate(self, contents, schema):
        from google.genai import errors, types
        from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

        def transient(exc: BaseException) -> bool:
            return isinstance(exc, errors.APIError) and getattr(exc, "code", None) in {429, 500, 502, 503, 504}

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(min=1, max=10),
            retry=retry_if_exception(transient),
            reraise=True,
        )
        def call():
            return self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0, response_mime_type="application/json", response_schema=schema
                ),
            )

        t0 = time.perf_counter()
        try:
            resp = call()
        except errors.APIError as exc:
            code = "PROVIDER_QUOTA_EXCEEDED" if getattr(exc, "code", None) == 429 else "PROVIDER_TIMEOUT"
            raise ProviderError(code, str(exc), transient=True) from exc
        usage = None
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            usage = {
                "prompt_tokens": getattr(um, "prompt_token_count", None),
                "output_tokens": getattr(um, "candidates_token_count", None),
                "total_tokens": getattr(um, "total_token_count", None),
            }
        info = CallInfo(
            model_name=self.settings.gemini_model,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            token_usage=usage,
            estimated_cost_usd=self.settings.gemini_usd_per_page or None,
        )
        return resp, info

    def extract(self, ctx: PageCtx, hints: str = "") -> tuple[GeminiPage, CallInfo]:
        from google.genai import types

        resp, info = self._generate(
            [types.Part.from_bytes(data=ctx.png_bytes, mime_type="image/png"), EXTRACT_PROMPT.format(hints=hints)],
            GeminiPage,
        )
        try:
            page = GeminiPage.model_validate_json(resp.text)
        except Exception as exc:
            raise ProviderError("EXTRACTION_OUTPUT_INVALID", f"model output failed schema: {exc}") from exc
        return page, info

    def relook(self, ctx, crop_png, header_png, bbox, header, product_label, readings):
        from google.genai import types

        parts = [types.Part.from_bytes(data=crop_png, mime_type="image/png")]
        if header_png:
            parts.append(types.Part.from_bytes(data=header_png, mime_type="image/png"))
        parts.append(RELOOK_PROMPT.format(header=header, product_label=product_label, readings=json.dumps(readings)))
        resp, info = self._generate(parts, RelookAnswer)
        try:
            ans = RelookAnswer.model_validate_json(resp.text)
        except Exception:
            ans = RelookAnswer(raw="", legible=False)
        return ans, info


def build_extractor(settings: Settings) -> Extractor:
    if settings.extractor == "gemini":
        return GeminiExtractor(settings)
    return HeuristicExtractor()
