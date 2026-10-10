import logging
import os
import time

import pytest

from research_mcp.logs import LOG_PREFIX, redact_url, setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(saved_level)


def test_writes_one_file_per_process(tmp_path):
    path = setup_logging(tmp_path)
    logging.getLogger("research_mcp").info("hello from the test")
    assert path is not None and path.parent == tmp_path
    assert path.name.startswith(f"{LOG_PREFIX}-") and path.name.endswith(f"-{os.getpid()}.log")
    assert "hello from the test" in path.read_text(encoding="utf-8")


def test_never_writes_to_stdout(tmp_path, capfd):
    setup_logging(tmp_path)
    logging.getLogger("research_mcp").warning("to stderr")
    out, err = capfd.readouterr()
    assert out == ""
    assert "to stderr" in err


def test_unwritable_folder_falls_back_to_stderr(tmp_path, capfd):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    assert setup_logging(blocker / "logs") is None
    logging.getLogger("research_mcp").info("still logged")
    assert "still logged" in capfd.readouterr().err


def test_calling_twice_does_not_duplicate_handlers(tmp_path):
    setup_logging(tmp_path)
    setup_logging(tmp_path)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "research_mcp", False)]
    assert len(ours) == 2  # one stderr + one file


def test_http_library_info_lines_with_query_strings_stay_out(tmp_path):
    path = setup_logging(tmp_path)
    logging.getLogger("httpx2").info('HTTP Request: GET https://example.com/?token=secret "HTTP/1.1 200 OK"')
    assert "token=secret" not in path.read_text(encoding="utf-8")


def test_urls_in_any_log_line_are_reduced_to_host_and_path(tmp_path):
    path = setup_logging(tmp_path)
    logging.getLogger("trafilatura.core").warning("discarding data: https://site.example/path?token=SECRET")
    text = path.read_text(encoding="utf-8")
    assert "token=SECRET" not in text
    assert "discarding data: site.example/path" in text


def test_old_log_files_are_pruned(tmp_path):
    old = tmp_path / f"{LOG_PREFIX}-20200101-000000-1.log"
    old.write_text("old", encoding="utf-8")
    month_ago = time.time() - 30 * 24 * 3600
    os.utime(old, (month_ago, month_ago))
    unrelated = tmp_path / "keep-me.log"
    unrelated.write_text("x", encoding="utf-8")
    os.utime(unrelated, (month_ago, month_ago))
    setup_logging(tmp_path)
    assert not old.exists()
    assert unrelated.exists()


@pytest.mark.parametrize(("url", "expected"), [
    ("https://Example.com/path/page?token=abc#frag", "example.com/path/page"),
    ("http://example.com", "example.com/"),
    ("not a url", "<invalid url>"),
])
def test_redact_url_keeps_host_and_path_only(url, expected):
    assert redact_url(url) == expected
