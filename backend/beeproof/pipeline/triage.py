"""Stage 1 - file sniffing, page rendering and image clean-up (ARCHITECTURE.md §6.1)."""

from __future__ import annotations

import io

import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image, ImageOps

from beeproof.contracts import PageCtx, Word

RENDER_DPI = 200
DIGITAL_MIN_WORDS = 20

MIME_BY_KIND = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg", "tiff": "image/tiff"}
EXT_BY_KIND = {"pdf": {".pdf"}, "png": {".png"}, "jpeg": {".jpg", ".jpeg"}, "tiff": {".tif", ".tiff"}}


class TriageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sniff(data: bytes) -> str | None:
    """Detect file type from its signature, not its name."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def count_pages(data: bytes, kind: str) -> int:
    if kind == "pdf":
        with fitz.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    with Image.open(io.BytesIO(data)) as im:
        return getattr(im, "n_frames", 1)


def _pdf_pages(data: bytes, filename: str, max_pages: int) -> list[PageCtx]:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # corrupt / encrypted
        raise TriageError("INVALID_FILE", f"PDF could not be opened: {exc}") from exc
    if doc.needs_pass:
        raise TriageError("INVALID_FILE", "encrypted PDFs are not supported")
    if doc.page_count > max_pages:
        raise TriageError("PAGE_LIMIT_EXCEEDED", f"{doc.page_count} pages exceeds limit {max_pages}")
    pages = []
    for i, page in enumerate(doc):
        rect = page.rect  # rotated page rect (matches the rendered image)
        mat = page.rotation_matrix
        words = []
        for x0, y0, x1, y1, text, *_ in page.get_text("words"):
            r = fitz.Rect(x0, y0, x1, y1) * mat
            words.append(Word(r.x0 / rect.width, r.y0 / rect.height, r.x1 / rect.width, r.y1 / rect.height, text))
        pix = page.get_pixmap(dpi=RENDER_DPI)
        pages.append(
            PageCtx(
                file=filename,
                page_no=i + 1,
                kind="digital" if len(words) >= DIGITAL_MIN_WORDS else "scanned",
                png_bytes=pix.tobytes("png"),
                width=pix.width,
                height=pix.height,
                words=words if len(words) >= DIGITAL_MIN_WORDS else [],
                source_bytes=data,
                is_pdf=True,
                rotation=page.rotation,
            )
        )
    doc.close()
    return pages


def estimate_skew(gray: np.ndarray, max_angle: float = 5.0, step: float = 0.25) -> float:
    """Projection-profile skew estimate in degrees (positive = counter-clockwise)."""
    h, w = gray.shape
    scale = 1000 / max(h, w)
    small = cv2.resize(gray, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    _, binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    sh, sw = binary.shape
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_angle, max_angle + 1e-9, step):
        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), float(angle), 1.0)
        rotated = cv2.warpAffine(binary, m, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        score = float(np.var(rotated.sum(axis=1)))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return best_angle


def deskew(img: Image.Image) -> tuple[Image.Image, float]:
    gray = np.array(img.convert("L"))
    angle = estimate_skew(gray)
    if abs(angle) <= 0.5:
        return img, 0.0
    rgb = np.array(img.convert("RGB"))
    h, w = rgb.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(rgb, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    return Image.fromarray(out), angle


def _image_pages(data: bytes, filename: str, max_pages: int, max_pixels: int) -> list[PageCtx]:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as exc:
        raise TriageError("INVALID_FILE", f"image could not be decoded: {exc}") from exc
    n = getattr(im, "n_frames", 1)
    if n > max_pages:
        raise TriageError("PAGE_LIMIT_EXCEEDED", f"{n} frames exceeds limit {max_pages}")
    pages = []
    for i in range(n):
        im.seek(i)
        if im.width * im.height > max_pixels:
            raise TriageError("FILE_TOO_LARGE", f"image {im.width}x{im.height} exceeds pixel limit")
        frame = ImageOps.exif_transpose(im.copy()).convert("RGB")
        frame, _angle = deskew(frame)
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        pages.append(
            PageCtx(
                file=filename,
                page_no=i + 1,
                kind="image",
                png_bytes=buf.getvalue(),
                width=frame.width,
                height=frame.height,
                source_bytes=None,
                is_pdf=False,
            )
        )
    return pages


def triage(data: bytes, filename: str, max_pages: int = 30, max_pixels: int = 40_000_000) -> list[PageCtx]:
    kind = sniff(data)
    if kind is None:
        raise TriageError("UNSUPPORTED_FILE_TYPE", "file signature is not PDF, PNG, JPEG or TIFF")
    if kind == "pdf":
        return _pdf_pages(data, filename, max_pages)
    Image.MAX_IMAGE_PIXELS = max_pixels
    return _image_pages(data, filename, max_pages, max_pixels)


def crop_region(ctx: PageCtx, bbox: list[float], dpi: int = 400) -> bytes:
    """High-resolution crop of a normalized region (re-render for PDFs, 3x upscale for images)."""
    if ctx.is_pdf and ctx.source_bytes:
        with fitz.open(stream=ctx.source_bytes, filetype="pdf") as doc:
            page = doc[ctx.page_no - 1]
            rect = page.rect
            clip = fitz.Rect(bbox[0] * rect.width, bbox[1] * rect.height, bbox[2] * rect.width, bbox[3] * rect.height)
            # clip is in rotated space; map back to unrotated page space for rendering
            clip = clip * page.derotation_matrix
            clip.normalize()
            return page.get_pixmap(dpi=dpi, clip=clip).tobytes("png")
    with Image.open(io.BytesIO(ctx.png_bytes)) as im:
        w, h = im.size
        box = (
            int(bbox[0] * w),
            int(bbox[1] * h),
            max(int(bbox[2] * w), int(bbox[0] * w) + 1),
            max(int(bbox[3] * h), int(bbox[1] * h) + 1),
        )
        crop = im.crop(box)
        crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()
