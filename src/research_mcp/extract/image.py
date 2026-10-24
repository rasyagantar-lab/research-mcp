"""Picture facts for download_file: pixel size, format and frame count, so a page can lay a picture out
without guessing. Pillow is imported inside the function: loading it at startup costs time."""

import re
import warnings
from dataclasses import dataclass
from io import BytesIO

SVG_TYPE = "image/svg+xml"
_SVG_TAG = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE)
_PLAIN_LENGTH = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(?:px)?\s*$")


@dataclass(frozen=True)
class ImageInfo:
    format: str
    width: int | None
    height: int | None
    frames: int = 1
    note: str = ""

    def describe(self) -> str:
        if self.width is None or self.height is None:
            return f"{self.format}, no pixel size in the file"
        text = f"{self.width} × {self.height} px, {self.format}"
        if self.frames > 1:
            text += f", {self.frames} frames"
        return f"{text} ({self.note})" if self.note else text


def image_info(body: bytes, content_type: str) -> ImageInfo | None:
    """Facts about a picture, or None when the bytes are not a picture this can read."""
    if content_type == SVG_TYPE or _looks_like_svg(body):
        return _svg_info(body)
    from PIL import Image

    try:
        with warnings.catch_warnings():
            # Only the header is read, so a huge declared size costs nothing here.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(BytesIO(body)) as image:
                width, height = image.size
                return ImageInfo(image.format or "unknown format", width, height, getattr(image, "n_frames", 1))
    except Exception:  # not a picture, damaged, or a format Pillow cannot open: there are no facts to give
        return None


def _looks_like_svg(body: bytes) -> bool:
    head = body[:4096].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _svg_info(body: bytes) -> ImageInfo | None:
    """Size from the root element's width/height (plain or px lengths), else from its viewBox."""
    match = _SVG_TAG.search(body[:65536])
    if match is None:
        return None
    tag = match.group(0).decode("utf-8", "replace")
    width, height = _length(_attribute(tag, "width")), _length(_attribute(tag, "height"))
    if width is not None and height is not None:
        return ImageInfo("SVG", width, height)
    box = (_attribute(tag, "viewBox") or "").replace(",", " ").split()
    try:
        box_width, box_height = round(float(box[2])), round(float(box[3]))
    except (IndexError, ValueError):
        return ImageInfo("SVG", None, None)
    return ImageInfo("SVG", box_width, box_height, note="from the viewBox; it scales freely")


def _attribute(tag: str, name: str) -> str | None:
    # The lookbehind keeps stroke-width or xlink:width from passing as width.
    match = re.search(rf"""(?<![\w:-]){name}\s*=\s*["']([^"']*)["']""", tag)
    return match.group(1) if match else None


def _length(value: str | None) -> int | None:
    match = _PLAIN_LENGTH.match(value) if value else None
    return round(float(match.group(1))) if match else None
