import json

import pytest

from research_mcp.extract.mediawiki import parse_api_urls, parse_response

API_QUERY = "action=parse&page={}&prop=text%7Cdisplaytitle&format=json&formatversion=2&redirects=1"


@pytest.mark.parametrize(("url", "expected"), [
    ("https://blackclover.fandom.com/wiki/Chapter_316", [
        "https://blackclover.fandom.com/api.php?" + API_QUERY.format("Chapter_316"),
        "https://blackclover.fandom.com/w/api.php?" + API_QUERY.format("Chapter_316"),
    ]),
    ("https://vsbattles.fandom.com/wiki/Asta_(Post-Timeskip)#Powers", [
        "https://vsbattles.fandom.com/api.php?" + API_QUERY.format("Asta_%28Post-Timeskip%29"),
        "https://vsbattles.fandom.com/w/api.php?" + API_QUERY.format("Asta_%28Post-Timeskip%29"),
    ]),
    ("https://naruto.fandom.com/es/wiki/Kakashi%20Hatake", [
        "https://naruto.fandom.com/es/api.php?" + API_QUERY.format("Kakashi+Hatake"),
        "https://naruto.fandom.com/es/w/api.php?" + API_QUERY.format("Kakashi+Hatake"),
    ]),
    ("https://wiki.example.org/w/index.php?title=Main_Page&oldid=5", [
        "https://wiki.example.org/w/api.php?" + API_QUERY.format("Main_Page"),
    ]),
])
def test_api_urls_for_wiki_pages(url, expected):
    assert parse_api_urls(url) == expected


@pytest.mark.parametrize("url", [
    "https://example.com/news/article",
    "https://example.com/wiki/",
    "https://example.com/w/index.php?action=history",
])
def test_non_wiki_urls_have_no_api(url):
    assert parse_api_urls(url) == []


def test_parse_response_returns_title_and_html():
    body = json.dumps({"parse": {"title": "Chapter 316", "pageid": 1, "text": "<p>Asta looks on.</p>"}}).encode()
    assert parse_response(body) == ("Chapter 316", "<p>Asta looks on.</p>")


@pytest.mark.parametrize("body", [
    b'{"error": {"code": "missingtitle", "info": "The page you specified doesn\'t exist."}}',
    b'{"parse": {"title": "Empty", "text": "   "}}',
    b"",
    b"<html>not json</html>",
    b"[1, 2, 3]",
])
def test_parse_response_rejects_errors_and_garbage(body):
    assert parse_response(body) is None
