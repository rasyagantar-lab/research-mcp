import hashlib
import re

import pytest

from research_mcp.config import load_settings
from research_mcp.errors import ToolError
from research_mcp.tools import download
from research_mcp.tools.download import MAX_NAME_CHARS, clean_name, download_file

PICTURE = b"\x89PNG\r\n\x1a\n not a real picture, just bytes"


@pytest.fixture
def folder(services, tmp_path):
    root = tmp_path / "research-files"
    services.settings = load_settings({"RESEARCH_MCP_DOWNLOAD_DIR": str(root)})
    return root


def saved_path(output):
    return re.search(r"^saved: (.*)$", output, re.MULTILINE).group(1)


async def download_error(services, *args, **kwargs):
    with pytest.raises(ToolError) as caught:
        await download_file(services, *args, **kwargs)
    return caught.value


@pytest.mark.anyio
async def test_saves_the_file_under_its_url_name_with_size_and_sha256(site, services, folder):
    site.add("/img/cat.png", PICTURE, content_type="image/png")
    output = await download_file(services, site.url("/img/cat.png"))
    assert (folder / "cat.png").read_bytes() == PICTURE
    assert output.splitlines()[0] == f"[download_file] {site.url('/img/cat.png')}"
    assert saved_path(output) == str(folder.resolve() / "cat.png")
    assert "relative to the research folder: cat.png" in output
    assert f"size: {len(PICTURE):,} bytes   type: image/png" in output
    assert f"sha256: {hashlib.sha256(PICTURE).hexdigest()}" in output


@pytest.mark.anyio
async def test_never_overwrites_an_existing_file(site, services, folder):
    site.add("/cat.png", PICTURE, content_type="image/png")
    await download_file(services, site.url("/cat.png"))
    second = await download_file(services, site.url("/cat.png"))
    third = await download_file(services, site.url("/cat.png"))
    assert saved_path(second).endswith("cat (1).png")
    assert saved_path(third).endswith("cat (2).png")
    assert sorted(path.name for path in folder.iterdir()) == ["cat (1).png", "cat (2).png", "cat.png"]


@pytest.mark.anyio
async def test_name_from_content_disposition_including_rfc_5987(site, services, folder):
    site.add("/get?id=1", b"%PDF-1.4", content_type="application/pdf",
             headers={"Content-Disposition": "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"})
    site.add("/get?id=2", b"a,b", content_type="text/csv", headers={"Content-Disposition": 'inline; filename="data.csv"'})
    assert saved_path(await download_file(services, site.url("/get?id=1"))).endswith("résumé.pdf")
    assert saved_path(await download_file(services, site.url("/get?id=2"))).endswith("data.csv")


@pytest.mark.anyio
async def test_a_hostile_server_name_stays_inside_the_folder(site, services, folder):
    site.add("/x", b"MZ", content_type="application/octet-stream",
             headers={"Content-Disposition": 'attachment; filename="../../evil.exe"'})
    path = saved_path(await download_file(services, site.url("/x")))
    assert path == str(folder.resolve() / ".._.._evil.exe")


@pytest.mark.anyio
async def test_explicit_filename_and_folder_get_an_extension_from_the_content_type(site, services, folder):
    site.add("/a/b", PICTURE, content_type="image/png")
    output = await download_file(services, site.url("/a/b"), filename="kucing", folder="images\\cats")
    assert (folder / "images" / "cats" / "kucing.png").read_bytes() == PICTURE
    assert "relative to the research folder: images/cats/kucing.png" in output


@pytest.mark.anyio
async def test_no_name_anywhere_falls_back_to_download(site, services, folder):
    site.add("/", "<html><body>home</body></html>")
    site.add("/blob", b"\x00\x01", content_type="application/octet-stream")
    assert saved_path(await download_file(services, site.url("/"))).endswith("download.html")
    assert saved_path(await download_file(services, site.url("/blob"))).endswith("blob")


@pytest.mark.anyio
@pytest.mark.parametrize("bad_folder", ["../outside", "a/../../b", "C:/Windows", "/etc", "\\\\server\\share", "a/b/c/d/e/f"])
async def test_folders_outside_the_research_folder_are_refused(services, folder, bad_folder):
    err = await download_error(services, "https://example.com/file.txt", folder=bad_folder)
    assert err.code == "invalid_argument"


@pytest.mark.anyio
async def test_an_unusable_filename_is_refused_before_any_request(site, services, folder):
    err = await download_error(services, site.url("/cat.png"), filename=" ... ")
    assert err.code == "invalid_argument"
    assert site.requests == []


@pytest.mark.anyio
async def test_http_errors(site, services, folder):
    site.add("/missing", "nope", status=404, content_type="text/plain")
    site.add("/limited", "slow down", status=429, content_type="text/plain")
    assert (await download_error(services, site.url("/missing"))).code == "http_error"
    assert (await download_error(services, site.url("/limited"))).code == "rate_limited"
    assert (await download_error(services, "http://private.test/file.zip")).code == "blocked_private_address"
    assert not folder.exists() or not any(folder.iterdir())


@pytest.mark.anyio
async def test_files_over_the_limit_are_refused(site, services, folder, monkeypatch):
    monkeypatch.setattr(download, "DOWNLOAD_LIMIT", 1000)
    site.add("/big.bin", b"x" * 5000, content_type="application/octet-stream")
    assert (await download_error(services, site.url("/big.bin"))).code == "too_large"


@pytest.mark.anyio
async def test_an_unwritable_folder_is_save_failed(site, services, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("in the way")
    services.settings = load_settings({"RESEARCH_MCP_DOWNLOAD_DIR": str(blocker)})
    site.add("/cat.png", PICTURE, content_type="image/png")
    assert (await download_error(services, site.url("/cat.png"))).code == "save_failed"


@pytest.mark.parametrize(("raw", "clean"), [
    ("report.pdf", "report.pdf"),
    ('a<b>c:d"e|f?g*h.txt', "a_b_c_d_e_f_g_h.txt"),
    ("CON.txt", "_CON.txt"),
    ("nul", "_nul"),
    ("trailing dots... ", "trailing dots"),
    ("line\nbreak.txt", "line break.txt"),
    ("...", ""),
])
def test_clean_name(raw, clean):
    assert clean_name(raw) == clean


def test_long_names_are_shortened_but_keep_the_extension():
    name = clean_name("a" * 300 + ".pdf")
    assert len(name) == MAX_NAME_CHARS
    assert name.endswith("a.pdf")
