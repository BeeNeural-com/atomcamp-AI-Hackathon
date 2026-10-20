"""Synthetic rate-sheet generator with ground truth (ARCHITECTURE.md §11).

The organizers ship only extracted rows, not source documents, so we render the
CSV back into rate sheets and keep what was actually printed as ground truth.

Deviations from §11, found while checking the sample data:
* rows are grouped by (source_file, effective_date): 28 of 30 files carry rows
  with more than one effective date, which cannot share one printed header;
* exact duplicates of (source_file, product_type, term_months, effective_date)
  are dropped (15 in the sample) and counted in manifest.json.

Usage: python -m bench.generate [--csv PATH] [--out DIR] [--inject 0.05] [--seed 7] [--no-images]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import zlib
from collections import OrderedDict
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from beeproof.config import get_settings
from beeproof.domain.validators import APY_MAX_TOL, apy_max

PAGE_W, PAGE_H = letter
TERM_PRODUCTS = {"CD", "IRA CD"}

CU_NAMES = {
    "Savings": "Share Savings",
    "CD": "Share Certificate",
    "IRA CD": "IRA Certificate",
    "Money Market": "Money Market",
    "High-Yield Savings": "High-Yield Savings",
    "Checking Plus": "Checking Plus",
    "Checking": "Interest Checking",
}


def _months_long(m: int) -> str:
    return f"{m} Months"


def _months_short(m: int) -> str:
    return f"{m} Mo"


def _months_dense(m: int) -> str:
    if m % 12 == 0:
        y = m // 12
        return f"{y} Year" if y == 1 else f"{y} Years"
    return f"{m}-Month"


def _money_full(v: float) -> str:
    return f"${v:,.0f}"


def _money_k(v: float) -> str:
    if v >= 1000 and v % 1000 == 0:
        return f"${int(v // 1000)}K"
    return f"${v:,.0f}"


def _date_long(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _date_us(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


def _date_us_padded(d: date) -> str:
    return d.strftime("%m/%d/%Y")


TEMPLATES = {
    "T1": {"name": "Classic", "decimals": 3, "term": _months_long, "money": _money_full, "date": _date_long},
    "T2": {"name": "Credit union", "decimals": 2, "term": _months_short, "money": _money_full, "date": _date_us_padded},
    "T3": {"name": "Grouped", "decimals": 3, "term": _months_long, "money": _money_k, "date": _date_us},
    "T4": {"name": "Dense", "decimals": 3, "term": _months_dense, "money": _money_full, "date": _date_long},
}


def load_groups(csv_path: Path) -> tuple[OrderedDict, int]:
    groups: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    seen, dropped = set(), 0
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["source_file"], row["product_type"], row["term_months"], row["effective_date"])
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            groups.setdefault((row["source_file"], row["effective_date"]), []).append(row)
    return groups, dropped


def printed_row(row: dict, tpl: dict, label: str) -> dict:
    d = tpl["decimals"]
    term = int(row["term_months"])
    return {
        "label": label,
        "term": tpl["term"](term) if term > 0 else "",
        "rate": f"{float(row['rate_pct']):.{d}f}%",
        "apy": f"{float(row['apy_pct']):.{d}f}%",
        "min": tpl["money"](float(row["min_deposit"])),
    }


def inject_error(p: dict, row: dict, rng: random.Random) -> str:
    """Corrupt one printed value; the truth keeps the corrupted print (what the page shows)."""
    kinds = ["swap_rate_apy", "drop_digit"] + (["cd_missing_term"] if row["product_type"] in TERM_PRODUCTS else [])
    kind = rng.choice(kinds)
    if kind == "swap_rate_apy":
        p["rate"], p["apy"] = p["apy"], p["rate"]
    elif kind == "drop_digit":
        s = p["apy"].rstrip("%")
        i = s.index(".") + 1
        p["apy"] = s[:i] + s[i + 1 :] + "%"
    else:
        p["term"] = ""
    return kind


BLANK_TERM_TEMPLATES = {"T2"}  # T2 leaves the cell empty; the others print "-"


def printed_term_months(term: str, product_type: str, tpl_id: str) -> int | None:
    if not term:
        # an empty CD term cell is absent; a printed "-" normalizes to 0 (then fails TERM_PRODUCT_CONSISTENT)
        return None if (product_type in TERM_PRODUCTS and tpl_id in BLANK_TERM_TEMPLATES) else 0
    n = int(term.split()[0].split("-")[0])
    return n * 12 if "Year" in term else n


# ---------------------------------------------------------------- rendering
def _table(
    c: canvas.Canvas,
    y: float,
    headers: list[str],
    widths: list[float],
    rows: list[list[str]],
    size: float,
    zebra: bool = False,
    x0: float = 54,
) -> float:
    lh = size * 1.9
    c.setFont("Helvetica-Bold", size)
    x = x0
    for h, w in zip(headers, widths):
        c.drawString(x, y, h)
        x += w
    c.setLineWidth(0.6)
    c.line(x0, y - size * 0.6, x0 + sum(widths), y - size * 0.6)
    y -= lh
    c.setFont("Helvetica", size)
    for i, r in enumerate(rows):
        if zebra and i % 2 == 0:
            c.setFillColor(Color(0.92, 0.94, 0.97))
            c.rect(x0 - 4, y - size * 0.45, sum(widths) + 8, lh * 0.95, stroke=0, fill=1)
            c.setFillColor(black)
        x = x0
        for v, w in zip(r, widths):
            c.drawString(x, y, v)
            x += w
        y -= lh
    return y


def render_pdf(institution: str, eff: date, tpl_id: str, items: list[dict]) -> bytes:
    tpl = TEMPLATES[tpl_id]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"{institution} rate sheet")
    y = PAGE_H - 72
    if tpl_id == "T1":
        c.setFont("Helvetica-Bold", 20)
        c.drawString(54, y, institution)
        c.setFont("Helvetica", 11)
        c.drawString(54, y - 22, "Deposit Rate Sheet")
        c.drawString(54, y - 38, f"Rates effective {tpl['date'](eff)}")
        _table(
            c,
            y - 80,
            ["Product", "Term", "Rate", "APY", "Minimum"],
            [170, 90, 80, 80, 90],
            [[p["label"], p["term"] or "-", p["rate"], p["apy"], p["min"]] for p in items],
            10.5,
        )
    elif tpl_id == "T2":
        c.setFont("Helvetica-Bold", 18)
        c.drawString(54, y, institution)
        c.setFont("Helvetica", 10)
        c.drawString(54, y - 20, "Dividend Rate Schedule")
        end = _table(
            c,
            y - 60,
            ["Account", "Term", "Dividend Rate", "APY*", "Min. to Open"],
            [160, 70, 95, 70, 100],
            [[p["label"], p["term"], p["rate"], p["apy"], p["min"]] for p in items],
            10,
        )
        c.setFont("Helvetica", 8.5)
        c.drawString(54, end - 20, f"*APY = Annual Percentage Yield. APY accurate as of {tpl['date'](eff)}.")
        c.drawString(54, end - 32, "Dividends are compounded and credited monthly. Fees may reduce earnings.")
    elif tpl_id == "T3":
        c.setFont("Helvetica-Bold", 18)
        c.drawString(54, y, institution)
        c.setFont("Helvetica", 10.5)
        c.drawString(54, y - 20, f"Personal Deposit Rates - Effective: {tpl['date'](eff)}")
        liquid = [p for p in items if not p["term"] and p["label"] not in TERM_PRODUCTS]
        terms = [p for p in items if p not in liquid]
        y2 = y - 58
        if liquid:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(54, y2, "Savings & Money Market")
            y2 = (
                _table(
                    c,
                    y2 - 24,
                    ["Account", "Rate", "APY", "Minimum"],
                    [190, 90, 90, 90],
                    [[p["label"], p["rate"], p["apy"], p["min"]] for p in liquid],
                    10,
                )
                - 18
            )
        if terms:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(54, y2, "Certificates")
            _table(
                c,
                y2 - 24,
                ["Certificate", "Term", "Rate", "APY", "Minimum"],
                [150, 90, 80, 80, 90],
                [[p["label"], p["term"] or "-", p["rate"], p["apy"], p["min"]] for p in terms],
                10,
            )
    else:  # T4 dense
        c.setFont("Helvetica-Bold", 15)
        c.drawString(54, y, institution)
        c.setFont("Helvetica", 8)
        c.drawString(54, y - 15, "Consumer deposit products")
        end = _table(
            c,
            y - 44,
            ["Product", "Term", "Annual Percentage Yield", "Interest Rate", "Minimum Balance"],
            [125, 70, 120, 85, 95],
            [[p["label"], p["term"] or "-", p["apy"], p["rate"], p["min"]] for p in items],
            8,
            zebra=True,
        )
        c.setFont("Helvetica", 7.5)
        c.drawString(54, end - 16, f"Rates as of {tpl['date'](eff)}. Rates may change after account opening.")
    c.showPage()
    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------- degradation
def _render(pdf: bytes, dpi: int) -> np.ndarray:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return cv2.cvtColor(img[:, :, :3], cv2.COLOR_RGB2BGR).copy()


def scan_variant(pdf: bytes, rng: random.Random) -> bytes:
    img = _render(pdf, 150)
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-2.5, 2.5), 1.0)
    img = cv2.warpAffine(img, m, (w, h), borderValue=(255, 255, 255))
    img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.6, 1.0))
    mask = np.random.default_rng(rng.randrange(1 << 30)).random(img.shape[:2])
    img[mask < 0.0025] = 0
    img[mask > 0.9975] = 255
    _, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 55])
    _, png = cv2.imencode(".png", cv2.imdecode(jpg, cv2.IMREAD_COLOR))
    return png.tobytes()


def photo_variant(pdf: bytes, rng: random.Random) -> bytes:
    img = _render(pdf, 150)
    h, w = img.shape[:2]

    def jit() -> float:
        return rng.uniform(-0.03, 0.03)

    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32(
        [
            [w * jit(), h * jit()],
            [w * (1 + jit()), h * jit()],
            [w * (1 + jit()), h * (1 + jit())],
            [w * jit(), h * (1 + jit())],
        ]
    )
    img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(200, 200, 200))
    grad = np.tile(np.linspace(0.8, 1.05, w, dtype=np.float32), (h, 1))[:, :, None]
    img = np.clip(img.astype(np.float32) * grad, 0, 255).astype(np.uint8)
    _, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 45])
    return jpg.tobytes()


# ---------------------------------------------------------------- main
def generate(
    csv_path: Path, out: Path, inject: float = 0.05, seed: int = 7, images: bool = True, limit: int | None = None
) -> dict:
    rng = random.Random(seed)
    groups, dropped = load_groups(csv_path)
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    if images:
        (out / "png").mkdir(parents=True, exist_ok=True)
    truth_lines, docs = [], []
    for n, ((source_file, eff_s), rows) in enumerate(groups.items()):
        if limit is not None and n >= limit:
            break
        eff = date.fromisoformat(eff_s)
        tpl_id = f"T{zlib.crc32(f'{source_file}|{eff_s}'.encode()) % 4 + 1}"
        tpl = TEMPLATES[tpl_id]
        stem = f"{Path(source_file).stem}__{eff_s}"
        institution = rows[0]["institution_name"]
        items, row_truth = [], []
        for row in rows:
            label = CU_NAMES.get(row["product_type"], row["product_type"]) if tpl_id == "T2" else row["product_type"]
            p = printed_row(row, tpl, label)
            injected = inject_error(p, row, rng) if rng.random() < inject else None
            items.append(p)
            rate, apy = float(p["rate"].rstrip("%")), float(p["apy"].rstrip("%"))
            term_months = printed_term_months(p["term"], row["product_type"], tpl_id)
            row_truth.append(
                {
                    "institution_name": institution,
                    "product_type": row["product_type"],
                    "term_months": term_months,
                    "rate_pct": rate,
                    "apy_pct": apy,
                    "min_deposit": float(row["min_deposit"]),
                    "effective_date": eff_s,
                    "printed": p,
                    "injected_error": injected,
                    "doc_inconsistent": apy > apy_max(rate) + APY_MAX_TOL,
                    "term_missing": row["product_type"] in TERM_PRODUCTS and not term_months,
                }
            )
        pdf = render_pdf(institution, eff, tpl_id, items)
        files = {"digital": f"pdf/{stem}.pdf"}
        (out / files["digital"]).write_bytes(pdf)
        if images:
            files["scan"] = f"png/{stem}__scan.png"
            files["photo"] = f"png/{stem}__photo.jpg"
            (out / files["scan"]).write_bytes(scan_variant(pdf, rng))
            (out / files["photo"]).write_bytes(photo_variant(pdf, rng))
        docs.append({"doc_id": stem, "template": tpl_id, "rows": len(rows), "files": files})
        for variant, rel in files.items():
            for i, t in enumerate(row_truth):
                truth_lines.append(
                    {"doc_id": stem, "file": rel, "variant": variant, "template": tpl_id, "row_index": i, **t}
                )
    with (out / "truth.jsonl").open("w", encoding="utf-8") as fh:
        for t in truth_lines:
            fh.write(json.dumps(t) + "\n")
    digital = [t for t in truth_lines if t["variant"] == "digital"]
    manifest = {
        "dataset_version": f"synthetic-v1-seed{seed}-inject{inject}",
        "source_csv": csv_path.name,
        "documents": len(docs),
        "rows_per_variant": len(digital),
        "duplicates_dropped": dropped,
        "variants": ["digital", "scan", "photo"] if images else ["digital"],
        "injected_errors": sum(1 for t in digital if t["injected_error"]),
        "doc_inconsistent_rows": sum(1 for t in digital if t["doc_inconsistent"]),
        "term_products_without_term": sum(1 for t in digital if t["term_missing"]),
        "docs": docs,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    s = get_settings()
    ap = argparse.ArgumentParser(description="Generate synthetic rate sheets with ground truth")
    ap.add_argument("--csv", type=Path, default=s.bench_source_csv)
    ap.add_argument("--out", type=Path, default=s.bench_dir)
    ap.add_argument("--inject", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-images", action="store_true")
    a = ap.parse_args()
    m = generate(a.csv, a.out, a.inject, a.seed, not a.no_images, a.limit)
    print(json.dumps({k: v for k, v in m.items() if k != "docs"}, indent=2))


if __name__ == "__main__":
    main()
