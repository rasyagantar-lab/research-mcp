"""Diagnostic tools: how big a picture and how long a call the client lets through.

spike_image draws a random code word (e.g. SALAK-7878) and pads the PNG to a target size. The code is only
in the picture (and the local log), so a model that reports it back proves the picture really arrived.
Measured on 2026-10-06 through Claude: 200 KB and 600 KB arrived, 900 KB (about 1200 KB as base64) did not.
spike_sleep waits on purpose; 30, 50 and 70 seconds all came back.
"""

import base64
import hashlib
import io
import logging
import os
import random
import struct
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio

log = logging.getLogger("research_mcp.spike")

FRUITS = ("SALAK", "SIRSAK", "JAMBU", "MANGGA", "DURIAN", "RAMBUTAN", "NANGKA", "PEPAYA", "MANGGIS", "DUKU")
WIDTH, HEIGHT = 1000, 320
FONT_SIZE = 110
# A private ancillary chunk: decoders skip it, so it adds bytes without changing the picture.
PAD_CHUNK = b"spAd"
PROGRESS_EVERY = 5

Progress = Callable[[float, float, str], Awaitable[None]]


@dataclass(frozen=True)
class SpikeImage:
    code: str
    png: bytes
    width: int
    height: int

    @property
    def base64_bytes(self) -> int:
        return 4 * ((len(self.png) + 2) // 3)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.png).hexdigest()

    def base64(self) -> str:
        return base64.b64encode(self.png).decode("ascii")

    def facts(self) -> dict[str, int | str]:
        return {
            "png_bytes": len(self.png), "base64_bytes": self.base64_bytes, "width": self.width,
            "height": self.height, "sha256": self.sha256,
        }


def make_spike_image(target_kb: int, rng: random.Random | None = None) -> SpikeImage:
    rng = rng or random.SystemRandom()
    code = f"{rng.choice(FRUITS)}-{rng.randint(1000, 9999)}"
    png = pad_png(draw_code(code), target_kb * 1024)
    log.info("spike image: code %s, %d bytes (target %d KB)", code, len(png), target_kb)
    return SpikeImage(code, png, WIDTH, HEIGHT)


def draw_code(code: str) -> bytes:
    """Black code word, centred on white. Pillow is imported here: loading it at startup costs time."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=FONT_SIZE)
    left, top, right, bottom = draw.textbbox((0, 0), code, font=font)
    draw.text(((WIDTH - (right - left)) // 2 - left, (HEIGHT - (bottom - top)) // 2 - top), code, fill="black", font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def pad_png(png: bytes, target: int) -> bytes:
    """Insert one padding chunk before IEND so the file is exactly `target` bytes; a PNG that is already
    that big is returned as it is. The padding is random, so no transport can compress it away."""
    room = target - len(png) - 12  # a chunk costs 12 bytes on top of its data: length, type, CRC
    if room < 0:
        return png
    data = os.urandom(room)
    chunk = struct.pack(">I", room) + PAD_CHUNK + data + struct.pack(">I", zlib.crc32(PAD_CHUNK + data))
    iend = png.rindex(b"IEND") - 4
    return png[:iend] + chunk + png[iend:]


def describe_image(tool: str, target_kb: int, image: SpikeImage) -> str:
    return "\n".join([
        f"[{tool}] target {target_kb} KB: PNG {image.width}x{image.height}, {len(image.png):,} bytes "
        f"({image.base64_bytes:,} as base64), sha256 {image.sha256[:16]}…",
        "Read the code word in the picture (a fruit name and four digits) and report it. "
        "It is not in this text, so reading it proves the picture arrived.",
    ])


async def spike_sleep(
    seconds: int,
    report: Progress | None = None,
    *,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Sleep in steps of PROGRESS_EVERY seconds, sending a progress notification after each step when the
    client asked for them (some clients keep a call alive only while progress arrives)."""
    started = clock()
    done = sent = 0
    while done < seconds:
        step = min(PROGRESS_EVERY, seconds - done)
        await sleep(step)
        done += step
        if report is not None:
            await report(done, seconds, f"slept {done} of {seconds} s")
            sent += 1
    progress = f"{sent} sent" if report is not None else "none (the client asked for no progress updates)"
    return f"[spike_sleep] slept {seconds} s (measured {clock() - started:.1f} s); progress notifications: {progress}"
