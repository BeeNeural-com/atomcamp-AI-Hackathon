"""Deterministic normalizers (ARCHITECTURE.md §6.4). Pure functions."""

from __future__ import annotations

import re
from datetime import date

from dateutil import parser as dateparser
from rapidfuzz import fuzz

from beeproof.contracts import ProductType

HEADER_SYNONYMS: dict[str, list[str]] = {
    "rate_pct": ["rate", "interest rate", "dividend rate", "int. rate", "nominal rate", "dividend"],
    "apy_pct": ["apy", "annual percentage yield", "apy*", "yield", "annual yield"],
    "min_deposit": [
        "minimum",
        "min. deposit",
        "minimum balance",
        "min to open",
        "min. to open",
        "minimum opening deposit",
        "balance to earn apy",
        "minimum deposit",
        "min deposit",
    ],
    "term_months": ["term", "maturity", "length"],
    "product_type": ["product", "account", "account type", "deposit type", "products"],
}

PRODUCT_SYNONYMS: dict[str, str] = {
    "ira share certificate": "IRA CD",
    "ira certificate": "IRA CD",
    "ira cd": "IRA CD",
    "ira certificate of deposit": "IRA CD",
    "share certificate": "CD",
    "certificate of deposit": "CD",
    "certificate": "CD",
    "cd": "CD",
    "money market account": "Money Market",
    "money market": "Money Market",
    "mma": "Money Market",
    "high yield savings": "High-Yield Savings",
    "high-yield savings": "High-Yield Savings",
    "checking plus": "Checking Plus",
    "interest checking": "Checking",
    "checking": "Checking",
    "share savings": "Savings",
    "regular savings": "Savings",
    "savings": "Savings",
}

_NONE_WORDS = {"none", "no minimum", "n/a", "na", "-", "--", "—", "no min", "$0", "0"}


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).replace(" ", " ").strip()


def parse_pct(s) -> float | None:
    """'3.55%', '3.550 %', '3.55*' -> 3.55"""
    text = _clean(s)
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not m:
        return None
    return float(m.group(0))


def parse_money(s) -> float | None:
    """'$1,000', '1K', '$25k', 'None', 'No minimum', '$0' -> number"""
    text = _clean(s).lower()
    if not text:
        return None
    if text in _NONE_WORDS or text.startswith("no min"):
        return 0.0
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([km])?\b", text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    suffix = m.group(2)
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return value


def parse_term(s) -> int | None:
    """'12 Mo', '1 Year', '18-Month', '6 mos', '365 days', 'N/A', '' -> months (0 for none)"""
    text = _clean(s).lower()
    if text in {"", "n/a", "na", "-", "--", "—", "none"}:
        return 0
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*(years?|yrs?|y|months?|mos?|m|days?|d)?\b", text)
    if not m:
        return None
    n = float(m.group(1))
    unit = (m.group(2) or "m")[0]
    if unit == "y":
        n *= 12
    elif unit == "d":
        n = n / 365 * 12
    return int(round(n))


def parse_date(s) -> str | None:
    """'7/1/2026', 'July 1, 2026', '2026-07-01', 'Effective 07/01/26' -> '2026-07-01'"""
    text = _clean(s)
    if not text or not re.search(r"\d", text):
        return None
    text = re.sub(r"(?i)\b(rates?|effective|as of|accurate|apy|date|on)\b[:]?", " ", text)
    try:
        dt = dateparser.parse(text, dayfirst=False, fuzzy=True, default=None)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt is None:
        return None
    return dt.date().isoformat()


def is_iso_date(s) -> bool:
    try:
        date.fromisoformat(str(s))
        return True
    except ValueError:
        return False


def _norm_label(s: str) -> str:
    s = _clean(s).lower()
    s = re.sub(r"[\*†‡¹²³]+", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\b\d+\s*-?\s*(months?|mos?|mo|years?|yrs?|year|days?)\b", "", s)
    s = re.sub(r"[^a-z\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def map_product(s) -> ProductType:
    """Fuzzy product mapping via PRODUCT_SYNONYMS, else OTHER."""
    label = _norm_label(s)
    if not label:
        return ProductType.OTHER
    for key, value in PRODUCT_SYNONYMS.items():  # ordered most specific first
        if re.search(rf"\b{re.escape(key)}\b", label):
            return ProductType(value)
    best, score = None, 0.0
    for key, value in PRODUCT_SYNONYMS.items():
        sc = fuzz.ratio(label, key)
        if sc > score:
            best, score = value, sc
    if best and score >= 85:
        return ProductType(best)
    return ProductType.OTHER


def map_header(header: str) -> str | None:
    """Map a printed column header to a schema field."""
    h = _clean(header).lower().rstrip(":")
    h_plain = re.sub(r"[\*†]+", "", h).strip()
    for fieldname, synonyms in HEADER_SYNONYMS.items():
        if h in synonyms or h_plain in synonyms:
            return fieldname
    # Containment, most specific fields first (APY headers often contain "rate").
    for fieldname in ["apy_pct", "min_deposit", "rate_pct", "term_months", "product_type"]:
        for syn in HEADER_SYNONYMS[fieldname]:
            if re.search(rf"\b{re.escape(syn.rstrip('*'))}\b", h_plain):
                return fieldname
    return None


def institution_from_filename(filename: str) -> str | None:
    """'summit_trust_rates_page4.pdf' -> 'Summit Trust'"""
    stem = re.sub(r"\.[a-z0-9]+$", "", filename.lower())
    stem = stem.split("__")[0]
    stem = re.sub(r"(_rates?)?(_page\d+)?$", "", stem)
    stem = re.sub(r"_rates?_.*$", "", stem)
    words = [w for w in re.split(r"[_\-\s]+", stem) if w and not w.isdigit()]
    if not words:
        return None
    return " ".join(w.capitalize() for w in words)


def normalize_field(name: str, raw) -> str | float | int | None:
    """Normalize a raw printed value for a schema field."""
    if raw is None:
        return None
    if name in {"rate_pct", "apy_pct"}:
        return parse_pct(raw)
    if name == "min_deposit":
        return parse_money(raw)
    if name == "term_months":
        return parse_term(raw)
    if name == "effective_date":
        return parse_date(raw)
    if name == "product_type":
        return map_product(raw).value
    if name == "institution_name":
        text = re.sub(r"\s+", " ", _clean(raw))
        return text or None
    return _clean(raw) or None


def decimals_in(raw) -> int | None:
    """Printed decimal precision, e.g. '3.55%' -> 2."""
    m = re.search(r"\d+\.(\d+)", _clean(raw))
    return len(m.group(1)) if m else (0 if re.search(r"\d", _clean(raw)) else None)


def values_agree(name: str, a, b, raw_a=None, raw_b=None) -> float:
    """1.0 exact, 0.5 fuzzy/rounding, 0.0 disagree (ARCHITECTURE.md §6.5)."""
    if a is None or b is None:
        return 0.0
    if name in {"rate_pct", "apy_pct", "min_deposit", "term_months"}:
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            return 0.0
        if abs(fa - fb) <= 1e-6:
            return 1.0
        da, db = decimals_in(raw_a), decimals_in(raw_b)
        if da is not None and db is not None and da != db:
            p = min(da, db)
            if round(fa, p) == round(fb, p):
                return 0.5
        return 0.0
    sa = re.sub(r"\s+", " ", str(a)).strip().lower()
    sb = re.sub(r"\s+", " ", str(b)).strip().lower()
    if sa == sb:
        return 1.0
    if fuzz.ratio(sa, sb) >= 85:
        return 0.5
    return 0.0
