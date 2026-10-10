import pytest
from pdfs import encrypt_pdf, make_pdf

from research_mcp.errors import ToolError
from research_mcp.extract.pdf import extract_pdf


def test_extracts_text_page_by_page_with_page_markers():
    result = extract_pdf(make_pdf(["alpha beta", "gamma delta"], title="Sample Paper"))
    assert result.title == "Sample Paper"
    assert (result.pages, result.extracted_pages, result.empty_pages) == (2, 2, 0)
    assert "[page 1]\nalpha beta" in result.text
    assert "[page 2]\ngamma delta" in result.text
    assert result.text.index("[page 1]") < result.text.index("[page 2]")


def test_a_pdf_without_metadata_has_an_empty_title():
    assert extract_pdf(make_pdf(["some text"])).title == ""


def test_pages_without_text_are_marked_and_counted():
    result = extract_pdf(make_pdf(["first", "", "third"]))
    assert (result.pages, result.empty_pages) == (3, 1)
    assert "[page 2] (no text)" in result.text
    assert "[page 3]\nthird" in result.text


def test_a_pdf_with_no_text_at_all_is_refused_as_probably_scanned():
    with pytest.raises(ToolError) as caught:
        extract_pdf(make_pdf(["", ""]))
    assert caught.value.code == "unsupported_content"
    assert "scan" in caught.value.message


@pytest.mark.parametrize("data", [b"", b"%PDF-1.4\nthis is not really a pdf", b"just some plain text"])
def test_garbage_is_reported_as_unreadable_not_raised_raw(data):
    with pytest.raises(ToolError) as caught:
        extract_pdf(data)
    assert caught.value.code == "unsupported_content"


def test_a_pdf_locked_only_against_editing_opens_with_the_empty_password():
    locked = encrypt_pdf(make_pdf(["secret words"]), user_password="")
    assert "secret words" in extract_pdf(locked).text


def test_a_password_protected_pdf_is_refused():
    locked = encrypt_pdf(make_pdf(["secret words"]), user_password="hunter2")
    with pytest.raises(ToolError) as caught:
        extract_pdf(locked)
    assert caught.value.code == "unsupported_content"
    assert "password" in caught.value.message


def test_extraction_stops_at_the_time_budget_and_says_how_far_it_got():
    ticks = iter(range(1000))  # the clock advances one second every time it is read
    result = extract_pdf(
        make_pdf([f"page text {i}" for i in range(10)]), time_budget=3.5, clock=lambda: float(next(ticks))
    )
    assert result.pages == 10
    assert 0 < result.extracted_pages < 10
    assert result.text.count("[page ") == result.extracted_pages
