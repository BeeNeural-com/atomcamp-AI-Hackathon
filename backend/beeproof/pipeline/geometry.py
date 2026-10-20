"""BBox utilities. Boxes are normalized [x0, y0, x1, y1], origin top-left."""

from __future__ import annotations

from beeproof.contracts import BBox, Word


def area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: BBox, b: BBox) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return area([x0, y0, x1, y1])


def iou(a: BBox, b: BBox) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def expand(b: BBox, frac: float) -> BBox:
    """Pad by `frac` of width/height on each side, clamped to the page."""
    w, h = b[2] - b[0], b[3] - b[1]
    return clamp([b[0] - w * frac, b[1] - h * frac, b[2] + w * frac, b[3] + h * frac])


def clamp(b: BBox) -> BBox:
    return [min(max(b[0], 0.0), 1.0), min(max(b[1], 0.0), 1.0), min(max(b[2], 0.0), 1.0), min(max(b[3], 0.0), 1.0)]


def center(b: BBox) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def contains_point(b: BBox, pt: tuple[float, float]) -> bool:
    return b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]


def union(boxes: list[BBox]) -> BBox | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def words_in(words: list[Word], b: BBox, pad: float = 0.3) -> list[Word]:
    """Words whose center lies inside `b` expanded by `pad`, or with IoU >= 0.2."""
    region = expand(b, pad)
    hits = [w for w in words if contains_point(region, center(w.bbox)) or iou(w.bbox, b) >= 0.2]
    return sorted(hits, key=lambda w: (round(w.y0, 3), w.x0))


def to_xywh(b: BBox | None) -> dict | None:
    """Canonical evidence box (DATA_MODEL.md §7)."""
    if not b:
        return None
    return {
        "x": round(b[0], 5),
        "y": round(b[1], 5),
        "width": round(b[2] - b[0], 5),
        "height": round(b[3] - b[1], 5),
        "coordinate_space": "normalized_page",
    }
