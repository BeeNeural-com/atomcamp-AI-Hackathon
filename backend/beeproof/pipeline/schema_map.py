"""Stage 4 - map Witness B output onto the rate-sheet schema (ARCHITECTURE.md §6.4)."""

from __future__ import annotations

from beeproof.contracts import (
    TERM_PRODUCTS,
    FieldEvidence,
    GeminiField,
    GeminiPage,
    ProductType,
    RecordDraft,
    box2d_to_bbox,
)
from beeproof.domain.normalizers import institution_from_filename, normalize_field
from beeproof.pipeline.geometry import union

_NORMALIZER = {
    "rate_pct": "parse_pct",
    "apy_pct": "parse_pct",
    "min_deposit": "parse_money",
    "term_months": "parse_term",
    "effective_date": "parse_date",
    "product_type": "map_product",
    "institution_name": "whitespace",
}


def make_field(name: str, gf: GeminiField | None, page: int, source_type: str = "table_cell") -> FieldEvidence:
    if gf is None:
        return FieldEvidence(name=name, page=page, source_type=source_type)
    value = normalize_field(name, gf.raw)
    f = FieldEvidence(
        name=name,
        value=value,
        raw=gf.raw,
        bbox=box2d_to_bbox(gf.box_2d),
        page=page,
        witness_b=gf.raw,
        source_type=source_type,
    )
    f.transformations.append({"step": "normalize", "normalizer": _NORMALIZER[name], "from": gf.raw, "to": value})
    return f


def schema_map(page: GeminiPage, source_file: str, page_no: int, start_index: int = 0) -> list[RecordDraft]:
    drafts: list[RecordDraft] = []
    inst_fallback = None
    if page.institution_name is None or not page.institution_name.raw.strip():
        inst_fallback = institution_from_filename(source_file)

    for i, row in enumerate(page.rows):
        fields: dict[str, FieldEvidence] = {}
        if inst_fallback is None:
            fields["institution_name"] = make_field("institution_name", page.institution_name, page_no, "page_header")
        else:
            fields["institution_name"] = FieldEvidence(
                name="institution_name",
                value=inst_fallback,
                raw=source_file,
                page=page_no,
                source_type="filename",
                agreement=0.5,
                transformations=[
                    {
                        "step": "fallback",
                        "normalizer": "institution_from_filename",
                        "from": source_file,
                        "to": inst_fallback,
                        "reason_code": "INSTITUTION_FROM_FILENAME",
                        "summary": "institution name not printed on page; taken from file name",
                    }
                ],
            )
        fields["product_type"] = make_field("product_type", row.product_label, page_no)
        if row.term is not None:
            fields["term_months"] = make_field("term_months", row.term, page_no)
        else:
            try:
                is_term = ProductType(fields["product_type"].value) in TERM_PRODUCTS
            except ValueError:
                is_term = False
            if is_term:
                fields["term_months"] = FieldEvidence(name="term_months", page=page_no)
            else:
                fields["term_months"] = FieldEvidence(
                    name="term_months",
                    value=0,
                    page=page_no,
                    source_type="derived",
                    transformations=[{"step": "derive", "normalizer": "non_term_product", "from": None, "to": 0}],
                )
        fields["rate_pct"] = make_field("rate_pct", row.rate, page_no)
        fields["apy_pct"] = make_field("apy_pct", row.apy, page_no)
        fields["min_deposit"] = make_field("min_deposit", row.min_deposit, page_no)
        fields["effective_date"] = make_field("effective_date", page.effective_date, page_no, "page_header")

        row_boxes = [f.bbox for n, f in fields.items() if f.bbox and n not in {"institution_name", "effective_date"}]
        drafts.append(
            RecordDraft(
                key=f"p{page_no}r{start_index + i}",
                source_file=source_file,
                page=page_no,
                fields=fields,
                row_bbox=union(row_boxes),
                product_label=row.product_label.raw,
            )
        )
    return drafts
