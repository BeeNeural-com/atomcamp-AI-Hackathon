"""In-memory pipeline contracts (ARCHITECTURE.md §5).

Coordinates are always normalized [x0, y0, x1, y1] in 0..1, origin top-left,
relative to the rendered page image.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

BBox = list[float]


class ProductType(str, Enum):
    SAVINGS = "Savings"
    HIGH_YIELD_SAVINGS = "High-Yield Savings"
    MONEY_MARKET = "Money Market"
    CHECKING = "Checking"
    CHECKING_PLUS = "Checking Plus"
    CD = "CD"
    IRA_CD = "IRA CD"
    OTHER = "Other"


TERM_PRODUCTS = {ProductType.CD, ProductType.IRA_CD}


class Decision(str, Enum):
    AUTO_APPROVED = "AUTO_APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DOCUMENT_INCONSISTENCY = "DOCUMENT_INCONSISTENCY"


FIELDS = ["institution_name", "product_type", "term_months", "rate_pct", "apy_pct", "min_deposit", "effective_date"]
NUMERIC_FIELDS = {"term_months", "rate_pct", "apy_pct", "min_deposit"}
FIELD_TYPES = {
    "institution_name": "string",
    "product_type": "enum",
    "term_months": "integer",
    "rate_pct": "decimal",
    "apy_pct": "decimal",
    "min_deposit": "decimal",
    "effective_date": "date",
}
FIELD_UNITS = {"rate_pct": "percent", "apy_pct": "percent", "min_deposit": "USD", "term_months": "months"}


# ---------- Witness B (LLM) structured output ----------
class GeminiField(BaseModel):
    raw: str = Field(description="Text exactly as printed, e.g. '3.55%' or '$1,000'")
    box_2d: list[int] = Field(description="[ymin, xmin, ymax, xmax] normalized 0-1000")


class GeminiRow(BaseModel):
    product_label: GeminiField
    term: Optional[GeminiField] = None
    rate: Optional[GeminiField] = None
    apy: Optional[GeminiField] = None
    min_deposit: Optional[GeminiField] = None


class GeminiPage(BaseModel):
    institution_name: Optional[GeminiField] = None
    effective_date: Optional[GeminiField] = None
    column_headers: list[str] = []
    footnotes: list[str] = []
    rows: list[GeminiRow] = []


class RelookAnswer(BaseModel):
    raw: str = ""
    legible: bool = True


def box2d_to_bbox(box: list[int]) -> BBox | None:
    if not box or len(box) != 4:
        return None
    ymin, xmin, ymax, xmax = box
    bbox = [xmin / 1000, ymin / 1000, xmax / 1000, ymax / 1000]
    bbox = [min(max(v, 0.0), 1.0) for v in bbox]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def bbox_to_box2d(bbox: BBox) -> list[int]:
    x0, y0, x1, y1 = bbox
    return [round(y0 * 1000), round(x0 * 1000), round(y1 * 1000), round(x1 * 1000)]


# ---------- Page context & Witness A ----------
@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def bbox(self) -> BBox:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass
class Cell:
    text: str
    bbox: BBox
    conf: float
    row_idx: int
    col_idx: int
    is_header: bool
    table_idx: int = 0


@dataclass
class LayoutReading:
    """Witness A output for one page."""

    provider: str  # pymupdf | docai | none
    words: list[Word] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)


@dataclass
class PageCtx:
    file: str  # display filename
    page_no: int  # 1-based
    kind: Literal["digital", "scanned", "image"]
    png_bytes: bytes
    width: int
    height: int
    words: list[Word] = field(default_factory=list)
    source_bytes: bytes | None = None  # original file, for high-dpi re-render
    is_pdf: bool = False
    rotation: int = 0


# ---------- Evidence & records ----------
@dataclass
class FieldEvidence:
    name: str
    value: str | float | int | None = None
    raw: str | None = None
    bbox: BBox | None = None
    page: int = 1
    witness_a: str | None = None
    witness_a_conf: float | None = None
    witness_b: str | None = None
    agreement: float = 0.0
    grounded: bool = False
    relook_values: list[str] = field(default_factory=list)
    validators: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    source_type: str = "table_cell"  # table_cell | page_header | filename | derived
    transformations: list[dict] = field(default_factory=list)
    unresolved: bool = False


@dataclass
class RuleResult:
    rule_id: str
    severity: Literal["HARD", "SOFT"]
    status: Literal["pass", "fail", "warn", "n/a"]
    fields: list[str]
    message: str = ""
    expected: str | None = None
    actual: str | None = None
    rule_version: str = "1"

    @property
    def failed(self) -> bool:
        return self.status in {"fail", "warn"}


@dataclass
class RecordDraft:
    key: str  # stable within a job: p{page}r{row}
    source_file: str
    page: int
    fields: dict[str, FieldEvidence]
    row_bbox: BBox | None = None
    product_label: str = ""
    rules: list[RuleResult] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    inferred_compounding: str | None = None
    extraction_confidence: float = 0.0
    decision: Decision = Decision.NEEDS_REVIEW

    def value(self, name: str):
        f = self.fields.get(name)
        return f.value if f else None

    @property
    def needs_review(self) -> bool:
        return self.decision != Decision.AUTO_APPROVED

    def to_row(self) -> dict:
        return {
            "source_file": self.source_file,
            "institution_name": self.value("institution_name"),
            "product_type": self.value("product_type"),
            "term_months": self.value("term_months"),
            "rate_pct": self.value("rate_pct"),
            "apy_pct": self.value("apy_pct"),
            "min_deposit": self.value("min_deposit"),
            "effective_date": self.value("effective_date"),
            "extraction_confidence": self.extraction_confidence,
            "needs_review": self.needs_review,
            "decision": self.decision.value,
            "review_reasons": list(self.review_reasons),
            "inferred_compounding": self.inferred_compounding,
        }


@dataclass
class ActionEvent:
    """Observable pipeline action, persisted as an AgentAction."""

    stage: str
    agent_name: str
    action_type: str
    reason_code: str
    summary: str
    result_status: str = "succeeded"
    duration_ms: int = 0
    attempt_number: int = 1
    model_name: str | None = None
    token_usage: dict | None = None
    estimated_cost_usd: float | None = None
    input_refs: list[str] = field(default_factory=list)
    output_refs: list[str] = field(default_factory=list)
    artifacts: list[tuple[str, bytes, str]] = field(default_factory=list)  # (name, bytes, content_type)
