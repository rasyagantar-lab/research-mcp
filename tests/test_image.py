import io
import struct
import zlib

import pytest

from research_mcp.extract.image import ImageInfo, image_info


def picture(fmt, size=(30, 20), **save_args):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, format=fmt, **save_args)
    return out.getvalue()


def animated_gif(frames):
    from PIL import Image

    images = [Image.new("RGB", (16, 8), color) for color in ("red", "green", "blue", "white")[:frames]]
    out = io.BytesIO()
    images[0].save(out, format="GIF", save_all=True, append_images=images[1:], duration=100)
    return out.getvalue()


def png_header_only(width, height):
    """A PNG that declares a size but holds no pixels: enough for a header read."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))


@pytest.mark.parametrize(("fmt", "content_type"), [
    ("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif"),
])
def test_raster_pictures_report_size_and_format(fmt, content_type):
    info = image_info(picture(fmt), content_type)
    assert info == ImageInfo(fmt, 30, 20)
    assert info.describe() == f"30 × 20 px, {fmt}"


def test_an_unlabeled_picture_is_still_recognised():
    assert image_info(picture("PNG", (7, 5)), "application/octet-stream") == ImageInfo("PNG", 7, 5)


def test_animated_gif_counts_frames():
    info = image_info(animated_gif(3), "image/gif")
    assert info.frames == 3
    assert info.describe() == "16 × 8 px, GIF, 3 frames"


@pytest.mark.parametrize("body", [b"hello, not a picture", b"\x89PNG\r\n\x1a\n broken", b""])
def test_non_pictures_give_no_facts(body):
    assert image_info(body, "image/png") is None


def test_a_huge_declared_size_is_reported_without_decoding():
    assert image_info(png_header_only(12000, 9000), "image/png") == ImageInfo("PNG", 12000, 9000)


def test_an_absurd_declared_size_is_refused_quietly():
    assert image_info(png_header_only(40000, 40000), "image/png") is None


@pytest.mark.parametrize(("svg", "expected"), [
    ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80px"></svg>', "120 × 80 px, SVG"),
    ('<svg viewBox="0 0 24 24" stroke-width="2"></svg>', "24 × 24 px, SVG (from the viewBox; it scales freely)"),
    ('<svg width="100%" height="100%" viewBox="0,0,300.4,150"></svg>', "300 × 150 px, SVG (from the viewBox; it scales freely)"),
    ('<svg width="10em" height="4em"></svg>', "SVG, no pixel size in the file"),
])
def test_svg_size_from_attributes_or_viewbox(svg, expected):
    assert image_info(svg.encode(), "image/svg+xml").describe() == expected


def test_svg_is_sniffed_without_a_content_type():
    body = b'<?xml version="1.0"?>\n<svg width="5" height="6"></svg>'
    assert image_info(body, "") == ImageInfo("SVG", 5, 6)


def test_html_is_not_mistaken_for_svg():
    assert image_info(b"<html><body><svg width='5' height='6'></svg></body></html>", "") is None
