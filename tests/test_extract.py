import pytest

from research_mcp.errors import ToolError
from research_mcp.extract import html as html_module
from research_mcp.extract.html import (
    Extracted,
    decode_body,
    extract_html,
    looks_like_challenge,
    plain_body_text,
)
from research_mcp.extract.pages import Page, PageCache, paginate

PARAGRAPHS = "".join(
    f"<p>Paragraph {i}: the quick brown fox jumps over the lazy dog near <a href='/link{i}'>link {i}</a>.</p>\n"
    for i in range(40)
)
ARTICLE = f"""<!doctype html><html><head><title>Sample Article Title</title></head>
<body><nav>Home | About | Contact</nav>
<article><h1>Sample Article</h1>{PARAGRAPHS}<img src="/pic.png" alt="A picture"></article>
<footer>Copyright footer text</footer><script>var tracking = 1;</script></body></html>"""
APP_SHELL = "<html><head><title>App</title></head><body><div id='root'></div><script>boot()</script></body></html>"


def test_extracts_article_markdown_with_absolute_links():
    extracted = extract_html(ARTICLE, "https://example.com/a")
    assert extracted.title.startswith("Sample Article")  # trafilatura may prefer the <h1> over <title>
    assert "Paragraph 0: the quick brown fox" in extracted.markdown
    assert "Paragraph 39" in extracted.markdown
    assert "(https://example.com/link7)" in extracted.markdown
    assert "Copyright footer text" not in extracted.markdown
    assert "tracking" not in extracted.markdown
    assert not extracted.thin


def test_app_shell_is_thin():
    extracted = extract_html(APP_SHELL, "https://example.com/app")
    assert extracted.thin
    assert extracted.title == "App"


def test_plain_body_text_drops_scripts_and_page_chrome():
    text = plain_body_text("<html><body><nav>menu</nav><div>First block</div><style>p{}</style>"
                           "<div>Second   block</div><script>x()</script><footer>foot</footer></body></html>")
    assert text == "First block\n\nSecond block"


def test_plain_body_text_survives_empty_input_and_xml_declarations():
    assert plain_body_text("") == ""
    assert plain_body_text('<?xml version="1.0" encoding="utf-8"?><html><body>x</body></html>') == "x"


def test_falls_back_to_body_text_when_trafilatura_finds_little(monkeypatch):
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *args, **kwargs: None)
    extracted = extract_html(ARTICLE, "https://example.com/a")
    assert "Paragraph 12" in extracted.markdown


def test_huge_html_skips_trafilatura(monkeypatch):
    import trafilatura

    def slow_path(*args, **kwargs):
        raise AssertionError("trafilatura must not see oversized HTML")

    monkeypatch.setattr(html_module, "MAX_EXTRACT_CHARS", 2000)
    monkeypatch.setattr(trafilatura, "extract", slow_path)
    monkeypatch.setattr(trafilatura, "extract_metadata", slow_path)
    extracted = extract_html(ARTICLE, "https://example.com/a")
    assert extracted.title == "Sample Article Title"
    assert "Paragraph 12" in extracted.markdown


def test_title_falls_back_to_the_url():
    assert extract_html("<html><body><p>x</p></body></html>", "https://example.com/x").title == "https://example.com/x"


def test_decode_prefers_header_then_meta_then_utf8():
    latin = "café".encode("latin-1")
    assert decode_body(latin, "iso-8859-1") == "café"
    assert decode_body(b'<meta charset="iso-8859-1">' + latin, None).endswith("café")
    assert decode_body("café".encode("utf-8"), "no-such-charset") == "café"


def test_decode_never_raises_on_undecodable_bytes():
    assert isinstance(decode_body(bytes(range(128, 256)) * 20, None), str)


@pytest.mark.parametrize(("status", "html", "blocked"), [
    (403, APP_SHELL, True),
    (503, APP_SHELL, True),
    (200, "<html><title>Just a moment...</title><body>checking</body></html>", True),
    (200, APP_SHELL, False),
    (403, ARTICLE, False),
])
def test_challenge_detection(status, html, blocked):
    assert looks_like_challenge(status, html, extract_html(html, "https://example.com/")) is blocked


def test_thin_threshold():
    assert Extracted("t", "x" * (html_module.THIN_CHARS - 1)).thin
    assert not Extracted("t", "x" * html_module.THIN_CHARS).thin


TEXT = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(60))


def test_windows_cover_the_text_exactly_once():
    pieces, offset = [], 0
    while offset is not None:
        window = paginate(TEXT, offset, 1000)
        assert window.end > window.start
        pieces.append(window.text)
        offset = window.next_offset
    assert "".join(pieces) == TEXT


def test_window_cuts_at_a_paragraph_break():
    window = paginate(TEXT, 0, 1000)
    assert window.text.endswith("\n\n")
    assert window.end <= 1000
    assert window.end > 1000 - 500


def test_window_without_nearby_break_cuts_hard():
    text = "x" * 5000
    window = paginate(text, 0, 1000)
    assert (window.start, window.end, window.total, window.next_offset) == (0, 1000, 5000, 1000)


def test_last_window_has_no_next_offset():
    window = paginate("short text", 0, 1000)
    assert window.text == "short text"
    assert window.next_offset is None


def test_empty_text_reads_as_an_empty_window():
    window = paginate("", 0, 1000)
    assert (window.total, window.text, window.next_offset) == (0, "", None)


def test_offset_past_the_end_is_an_error():
    with pytest.raises(ToolError) as caught:
        paginate("abc", 3, 1000)
    assert caught.value.code == "offset_out_of_range"
    assert "3 characters" in caught.value.message


def _page(name):
    return Page(url=f"https://example.com/{name}", title=name, markdown=name, render="static", fetched_at="t")


def test_cache_returns_what_was_put():
    cache = PageCache()
    cache.put(("https://example.com/a", "auto"), _page("a"))
    assert cache.get(("https://example.com/a", "auto")).title == "a"
    assert cache.get(("https://example.com/a", "never")) is None


def test_cache_entries_expire():
    now = [1000.0]
    cache = PageCache(ttl=600, clock=lambda: now[0])
    cache.put(("k", "auto"), _page("a"))
    now[0] += 601
    assert cache.get(("k", "auto")) is None


def test_cache_evicts_the_least_recently_used():
    cache = PageCache(max_entries=2)
    cache.put(("a", "auto"), _page("a"))
    cache.put(("b", "auto"), _page("b"))
    cache.get(("a", "auto"))
    cache.put(("c", "auto"), _page("c"))
    assert cache.get(("b", "auto")) is None
    assert cache.get(("a", "auto")) is not None
    assert cache.get(("c", "auto")) is not None
