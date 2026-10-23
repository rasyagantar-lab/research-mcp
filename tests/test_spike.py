import io
import random
import re

import pytest

from research_mcp.tools.spike import FRUITS, HEIGHT, WIDTH, describe_image, draw_code, make_spike_image, pad_png, spike_sleep


def test_padded_png_has_the_exact_size_and_still_opens():
    from PIL import Image

    png = pad_png(draw_code("SALAK-7878"), 200 * 1024)
    assert len(png) == 200 * 1024
    with Image.open(io.BytesIO(png)) as image:
        image.verify()
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (WIDTH, HEIGHT)
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_a_png_already_over_the_target_is_left_alone():
    png = draw_code("JAMBU-8978")
    assert pad_png(png, 10) == png


def test_the_code_is_a_fruit_and_four_digits_and_stays_out_of_the_text():
    image = make_spike_image(30, random.Random(7))
    fruit, digits = image.code.split("-")
    assert fruit in FRUITS and re.fullmatch(r"\d{4}", digits)
    assert len(image.png) == 30 * 1024
    assert image.base64_bytes == len(image.base64())
    text = describe_image("spike_image", 30, image)
    assert image.code not in text and digits not in text
    assert "30,720 bytes" in text


def test_every_code_word_fits_in_the_picture():
    from PIL import Image, ImageDraw, ImageFont

    from research_mcp.tools.spike import FONT_SIZE

    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = ImageFont.load_default(size=FONT_SIZE)
    widest = max(draw.textbbox((0, 0), f"{fruit}-8888", font=font)[2] for fruit in FRUITS)
    assert widest < WIDTH - 40


@pytest.mark.anyio
async def test_spike_sleep_steps_and_reports_progress():
    slept, reports, now = [], [], [0.0]

    async def fake_sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    async def report(progress, total, message):
        reports.append((progress, total, message))

    text = await spike_sleep(12, report, sleep=fake_sleep, clock=lambda: now[0])
    assert slept == [5, 5, 2]
    assert [progress for progress, _, _ in reports] == [5, 10, 12]
    assert reports[-1] == (12, 12, "slept 12 of 12 s")
    assert text == "[spike_sleep] slept 12 s (measured 12.0 s); progress notifications: 3 sent"


@pytest.mark.anyio
async def test_spike_sleep_without_progress():
    async def fake_sleep(seconds):
        pass

    text = await spike_sleep(3, None, sleep=fake_sleep)
    assert "progress notifications: none" in text
