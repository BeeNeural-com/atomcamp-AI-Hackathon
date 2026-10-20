"""Pipeline stages on real rendered PDFs, plus the re-look agent with fake extractors."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from beeproof.contracts import Decision, GeminiField, GeminiPage, GeminiRow, RelookAnswer, bbox_to_box2d
from beeproof.pipeline.orchestrator import Pipeline
from beeproof.pipeline.triage import estimate_skew, sniff, triage
from beeproof.providers.extractors import CallInfo, HeuristicExtractor


def test_sniff_by_signature():
    assert sniff(b"%PDF-1.7 ...") == "pdf"
    assert sniff(b"\x89PNG\r\n\x1a\n....") == "png"
    assert sniff(b"\xff\xd8\xff\xe0") == "jpeg"
    assert sniff(b"MZ\x90\x00") is None


def test_triage_digital_pdf(sample_pdf):
    pages = triage(sample_pdf, "x.pdf")
    assert len(pages) == 1 and pages[0].kind == "digital"
    assert all(0 <= w.x0 <= w.x1 <= 1 and 0 <= w.y0 <= w.y1 <= 1 for w in pages[0].words)


def test_skew_estimate_and_image_triage(sample_pdf):
    page = triage(sample_pdf, "x.pdf")[0]
    img = Image.open(io.BytesIO(page.png_bytes)).convert("RGB").rotate(2.0, fillcolor="white")
    assert 1.25 <= abs(estimate_skew(np.array(img.convert("L")))) <= 2.75
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert triage(buf.getvalue(), "scan.png")[0].kind == "image"


def test_heuristic_pipeline_end_to_end(settings, sample_pdf):
    res = Pipeline(settings).run(sample_pdf, "test_federal_bank_rates_page1.pdf")
    rows = {(r.value("product_type"), r.value("term_months")): r for r in res.records}
    assert set(rows) == {("Money Market", 0), ("CD", 36), ("Savings", 0)}
    cd = rows[("CD", 36)]
    assert (cd.value("rate_pct"), cd.value("apy_pct"), cd.value("min_deposit")) == (3.097, 3.119, 1000)
    assert cd.value("institution_name") == "Test Federal Bank" and cd.value("effective_date") == "2026-07-01"
    assert cd.decision == Decision.AUTO_APPROVED
    assert rows[("Money Market", 0)].decision == Decision.DOCUMENT_INCONSISTENCY
    for r in res.records:  # every extracted table value carries evidence coordinates
        for f in r.fields.values():
            if f.source_type == "table_cell" and f.value is not None:
                assert f.bbox and 0 <= f.bbox[0] < f.bbox[2] <= 1
    assert any(a.action_type == "reextract_region" for a in res.actions)


def test_extractor_only_never_flags(settings, sample_pdf):
    res = Pipeline(settings.with_flags(witness_a=False, validators=False, relook=False)).run(sample_pdf, "t.pdf")
    assert all(r.decision == Decision.AUTO_APPROVED for r in res.records)


class MisreadingExtractor(HeuristicExtractor):
    """Witness B misreads one APY; the re-look reads the true value from the page."""

    name = "fake-misreader"

    def extract(self, ctx, hints=""):
        page, info = super().extract(ctx, hints)
        for row in page.rows:
            if row.apy and row.apy.raw == "3.119%":
                row.apy = GeminiField(raw="3.179%", box_2d=row.apy.box_2d)
        return page, info


def test_relook_resolves_disagreement_by_majority(settings, sample_pdf):
    res = Pipeline(settings, extractor=MisreadingExtractor()).run(sample_pdf, "t.pdf")
    cd = next(r for r in res.records if r.value("product_type") == "CD")
    apy = cd.fields["apy_pct"]
    assert apy.witness_b == "3.179%" and apy.witness_a == "3.119%"
    assert apy.value == 3.119  # Witness A + re-look outvote Witness B
    assert apy.agreement == 0.67
    assert any(t.get("reason_code") == "RELOOK_CORRECTED" for t in apy.transformations)


class IllegibleRelook(MisreadingExtractor):
    name = "fake-illegible"

    def relook(self, ctx, crop_png, header_png, bbox, header, product_label, readings):
        return RelookAnswer(raw="", legible=False), CallInfo(model_name=self.name)


def test_unresolved_field_goes_to_review(settings, sample_pdf):
    res = Pipeline(settings, extractor=IllegibleRelook()).run(sample_pdf, "t.pdf")
    cd = next(r for r in res.records if r.value("product_type") == "CD")
    assert cd.fields["apy_pct"].unresolved
    assert cd.decision == Decision.NEEDS_REVIEW
    assert "FIELD_UNRESOLVED" in cd.reason_codes


class StaticExtractor:
    """Witness B returns a fixed page; used for pages without a text layer."""

    name = "fake-static"

    def __init__(self, page: GeminiPage):
        self.page = page

    def extract(self, ctx, hints=""):
        return self.page, CallInfo(model_name=self.name, estimated_cost_usd=0.0)

    def relook(self, ctx, crop_png, header_png, bbox, header, product_label, readings):
        return RelookAnswer(raw=readings[0] if readings else "", legible=True), CallInfo(model_name=self.name)


def _gf(raw: str, box: list[float]) -> GeminiField:
    return GeminiField(raw=raw, box_2d=bbox_to_box2d(box))


def test_image_with_single_witness_needs_review(settings, sample_pdf):
    page_png = triage(sample_pdf, "x.pdf")[0].png_bytes
    page = GeminiPage(
        institution_name=_gf("Test Federal Bank", [0.07, 0.05, 0.4, 0.08]),
        effective_date=_gf("July 1, 2026", [0.2, 0.14, 0.35, 0.16]),
        rows=[
            GeminiRow(
                product_label=_gf("CD", [0.07, 0.3, 0.1, 0.32]),
                term=_gf("36 Months", [0.35, 0.3, 0.45, 0.32]),
                rate=_gf("3.097%", [0.5, 0.3, 0.57, 0.32]),
                apy=_gf("3.119%", [0.63, 0.3, 0.7, 0.32]),
            )
        ],
    )
    res = Pipeline(settings, extractor=StaticExtractor(page)).run(page_png, "scan.png")
    rate = res.records[0].fields["rate_pct"]
    assert rate.witness_a is None  # no Witness A for images without Document AI
    assert rate.relook_values == ["3.097%"] and rate.agreement == 0.67  # B + re-look, never 3/3
    assert res.records[0].decision == Decision.NEEDS_REVIEW


def test_missing_institution_falls_back_to_filename(settings, sample_pdf):
    page = GeminiPage(
        rows=[
            GeminiRow(
                product_label=_gf("Savings", [0.1, 0.3, 0.2, 0.32]),
                rate=_gf("1.00%", [0.5, 0.3, 0.57, 0.32]),
                apy=_gf("1.00%", [0.63, 0.3, 0.7, 0.32]),
            )
        ]
    )
    res = Pipeline(settings, extractor=StaticExtractor(page)).run(sample_pdf, "summit_trust_rates_page4.pdf")
    inst = res.records[0].fields["institution_name"]
    assert inst.value == "Summit Trust" and inst.source_type == "filename"
    assert "INSTITUTION_FROM_FILENAME" in res.records[0].reason_codes
