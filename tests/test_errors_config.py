from pathlib import Path

import pytest

from research_mcp import __version__
from research_mcp.config import REPO_ROOT, load_settings, user_agent
from research_mcp.errors import ToolError


def test_tool_error_renders_code_message_and_suggestion():
    err = ToolError("invalid_url", "only http and https URLs can be read.", "check the URL")
    assert err.render() == "Error [invalid_url]: only http and https URLs can be read. Suggestion: check the URL"
    assert err.code == "invalid_url"


def test_tool_error_rejects_unknown_codes():
    with pytest.raises(ValueError):
        ToolError("no_such_code", "x", "y")


def test_defaults_without_environment():
    settings = load_settings({})
    assert settings.contact == ""
    assert settings.render == "auto"
    assert settings.log_dir == REPO_ROOT / "logs"
    assert settings.download_dir == Path.home() / "research-files"
    assert settings.diagnostics is True
    assert settings.warnings == ()


def test_repo_root_is_the_project_folder():
    assert (REPO_ROOT / "pyproject.toml").is_file()


def test_reads_environment_values(tmp_path):
    settings = load_settings({
        "RESEARCH_MCP_RENDER": " OFF ",
        "RESEARCH_MCP_CONTACT": "  https://example.org/contact  ",
        "RESEARCH_MCP_LOG_DIR": str(tmp_path),
        "RESEARCH_MCP_DOWNLOAD_DIR": f"  {tmp_path / 'files'}  ",
        "RESEARCH_MCP_DIAGNOSTICS": " Off",
    })
    assert settings.render == "off"
    assert settings.contact == "https://example.org/contact"
    assert settings.log_dir == Path(tmp_path)
    assert settings.download_dir == tmp_path / "files"
    assert settings.diagnostics is False
    assert settings.warnings == ()


def test_bad_download_dir_and_diagnostics_values_fall_back_with_warnings():
    settings = load_settings({"RESEARCH_MCP_DOWNLOAD_DIR": "relative/folder", "RESEARCH_MCP_DIAGNOSTICS": "maybe"})
    assert settings.download_dir == Path.home() / "research-files"
    assert settings.diagnostics is True
    assert len(settings.warnings) == 2


def test_bad_render_value_falls_back_with_a_warning():
    settings = load_settings({"RESEARCH_MCP_RENDER": "sometimes"})
    assert settings.render == "auto"
    assert "RESEARCH_MCP_RENDER" in settings.warnings[0]


def test_contact_cannot_break_the_user_agent_header():
    settings = load_settings({"RESEARCH_MCP_CONTACT": "me\r\nX-Evil: 1 ünïcode"})
    assert "\r" not in settings.contact and "\n" not in settings.contact
    assert settings.contact.isascii()
    assert settings.warnings


def test_user_agent_without_and_with_contact():
    project = "+https://github.com/rasyagantar-lab/research-mcp"
    assert user_agent(load_settings({})) == f"Mozilla/5.0 (compatible; research-mcp/{__version__}; {project})"
    with_contact = load_settings({"RESEARCH_MCP_CONTACT": "https://example.org/bot"})
    assert user_agent(with_contact) == (
        f"Mozilla/5.0 (compatible; research-mcp/{__version__}; {project}; +https://example.org/bot)"
    )
