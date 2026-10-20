"""Stage 5 - align Witness A with Witness B and measure agreement (ARCHITECTURE.md §6.5)."""

from __future__ import annotations

from collections import defaultdict

from beeproof.contracts import (
    Cell,
    FieldEvidence,
    GeminiField,
    GeminiPage,
    GeminiRow,
    LayoutReading,
    RecordDraft,
    bbox_to_box2d,
)
from beeproof.domain.normalizers import map_header, normalize_field, values_agree
from beeproof.pipeline.geometry import center, contains_point, expand, iou, union, words_in
from beeproof.pipeline.schema_map import schema_map

READ_FROM_A = {"table_cell", "page_header"}


def read_witness_a(reading: LayoutReading, bbox: list[float]) -> tuple[str | None, float | None]:
    """Text and confidence Witness A sees at `bbox`."""
    if reading.provider == "none" or not bbox:
        return None, None
    if reading.cells:
        region = expand(bbox, 0.3)
        best: Cell | None = None
        best_iou = 0.0
        for cell in reading.cells:
            ov = iou(cell.bbox, bbox)
            if ov >= 0.2 or contains_point(region, center(cell.bbox)):
                if best is None or ov > best_iou:
                    best, best_iou = cell, ov
        if best is not None and best.text:
            return best.text, best.conf
    ws = words_in(reading.words, bbox, pad=0.3)
    if not ws:
        return None, None
    conf = 1.0 if reading.provider == "pymupdf" else None
    return " ".join(w.text for w in ws), conf


def reconcile_field(f: FieldEvidence, reading: LayoutReading) -> None:
    if f.source_type not in READ_FROM_A or f.value is None:
        return
    a_text, a_conf = read_witness_a(reading, f.bbox)
    f.witness_a, f.witness_a_conf = a_text, a_conf
    a_val = normalize_field(f.name, a_text) if a_text is not None else None
    if a_val is None:
        f.agreement, f.grounded = 0.5, False  # only one witness has a reading
        return
    f.agreement = values_agree(f.name, a_val, f.value, a_text, f.raw)
    raw_b = (f.raw or "").strip()
    f.grounded = f.agreement == 1.0 or (bool(raw_b) and raw_b in a_text)


def reconcile(drafts: list[RecordDraft], reading: LayoutReading) -> None:
    for rec in drafts:
        for f in rec.fields.values():
            reconcile_field(f, reading)
        # Derived fields inherit the evidence quality of the label they were derived from.
        label = rec.fields.get("product_type")
        for f in rec.fields.values():
            if f.source_type == "derived" and label is not None:
                f.agreement, f.grounded = label.agreement, label.grounded
                f.witness_a_conf = label.witness_a_conf


def rows_only_in_witness_a(
    drafts: list[RecordDraft], reading: LayoutReading, source_file: str, page_no: int, start_index: int
) -> list[RecordDraft]:
    """Table rows Document AI found that Witness B missed (reason ROW_ONLY_IN_WITNESS_A)."""
    if not reading.cells:
        return []
    field_boxes = [f.bbox for r in drafts for f in r.fields.values() if f.bbox]
    by_table: dict[int, list[Cell]] = defaultdict(list)
    for c in reading.cells:
        by_table[c.table_idx].append(c)

    extra_rows: list[GeminiRow] = []
    for cells in by_table.values():
        header_map = {c.col_idx: map_header(c.text) for c in cells if c.is_header}
        if not any(v in {"rate_pct", "apy_pct"} for v in header_map.values()):
            continue
        if header_map.get(0, "x") is None:
            header_map[0] = "product_type"
        rows: dict[int, list[Cell]] = defaultdict(list)
        for c in cells:
            if not c.is_header:
                rows[c.row_idx].append(c)
        for row_cells in rows.values():
            covered = any(
                iou(c.bbox, b) > 0 or contains_point(b, center(c.bbox)) for c in row_cells for b in field_boxes
            )
            if covered:
                continue
            by_field = {header_map.get(c.col_idx): c for c in row_cells if c.text}
            if by_field.get("product_type") is None or not ({"rate_pct", "apy_pct"} & by_field.keys()):
                continue

            def gf(name: str, by_field: dict = by_field) -> GeminiField | None:
                c = by_field.get(name)
                return GeminiField(raw=c.text, box_2d=bbox_to_box2d(c.bbox)) if c else None

            extra_rows.append(
                GeminiRow(
                    product_label=gf("product_type"),
                    term=gf("term_months"),
                    rate=gf("rate_pct"),
                    apy=gf("apy_pct"),
                    min_deposit=gf("min_deposit"),
                )
            )
    if not extra_rows:
        return []
    page_level = drafts[0].fields if drafts else {}

    def page_field(name: str) -> GeminiField | None:
        f = page_level.get(name)
        if f is None or f.raw is None or f.source_type == "filename":
            return None
        return GeminiField(raw=f.raw, box_2d=bbox_to_box2d(f.bbox) if f.bbox else [0, 0, 1, 1])

    new = schema_map(
        GeminiPage(
            institution_name=page_field("institution_name"),
            effective_date=page_field("effective_date"),
            rows=extra_rows,
        ),
        source_file,
        page_no,
        start_index,
    )
    for rec in new:
        for f in rec.fields.values():
            if f.source_type == "table_cell" and f.value is not None:
                f.witness_a, f.witness_b = f.raw, None
                f.witness_a_conf = next((c.conf for c in reading.cells if f.bbox and iou(c.bbox, f.bbox) > 0.9), None)
                f.agreement, f.grounded = 0.5, True
        rec.fields["product_type"].transformations.append(
            {
                "step": "recover_row",
                "reason_code": "ROW_ONLY_IN_WITNESS_A",
                "summary": "row found by Document AI but missed by the extractor",
            }
        )
        rec.row_bbox = union([f.bbox for f in rec.fields.values() if f.bbox and f.source_type == "table_cell"])
    return new
