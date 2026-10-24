"""Settings from RESEARCH_MCP_* environment variables (Claude Desktop passes them via the config `env` block)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from research_mcp import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_URL = "https://github.com/rasyagantar-lab/research-mcp"
RENDER_MODES = ("auto", "off")
SWITCH_VALUES = {"on": True, "off": False}
MAX_CONTACT_CHARS = 200


def default_download_dir() -> Path:
    return Path.home() / "research-files"


@dataclass(frozen=True)
class Settings:
    contact: str
    log_dir: Path
    render: str
    download_dir: Path = field(default_factory=default_download_dir)
    # Folders download_file may also save into (its save_dir), e.g. the projects a Claude Code session publishes from.
    save_dirs: tuple[Path, ...] = field(default=())
    diagnostics: bool = True
    warnings: tuple[str, ...] = field(default=())


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read settings; a bad value falls back to its default and is reported in `warnings`, never raised."""
    env = os.environ if env is None else env
    warnings: list[str] = []

    render = env.get("RESEARCH_MCP_RENDER", "").strip().lower() or "auto"
    if render not in RENDER_MODES:
        warnings.append(f"RESEARCH_MCP_RENDER={render!r} is not one of {RENDER_MODES}; using 'auto'")
        render = "auto"

    raw_contact = env.get("RESEARCH_MCP_CONTACT", "")
    contact = " ".join(raw_contact.encode("ascii", "ignore").decode("ascii").split())
    contact = "".join(ch for ch in contact if ch.isprintable())[:MAX_CONTACT_CHARS]
    if raw_contact.strip() and contact != " ".join(raw_contact.split()):
        warnings.append("RESEARCH_MCP_CONTACT had non-ASCII or control characters; they were removed")

    log_dir_text = env.get("RESEARCH_MCP_LOG_DIR", "").strip()
    log_dir = Path(log_dir_text) if log_dir_text else REPO_ROOT / "logs"

    download_dir_text = env.get("RESEARCH_MCP_DOWNLOAD_DIR", "").strip()
    download_dir = Path(download_dir_text).expanduser() if download_dir_text else default_download_dir()
    if not download_dir.is_absolute():
        warnings.append(f"RESEARCH_MCP_DOWNLOAD_DIR={download_dir_text!r} is not an absolute path; using the default")
        download_dir = default_download_dir()

    save_dirs: list[Path] = []
    for entry in env.get("RESEARCH_MCP_SAVE_DIRS", "").split(os.pathsep):
        if not entry.strip():
            continue
        path = Path(entry.strip()).expanduser()
        if path.is_absolute():
            save_dirs.append(path)
        else:
            warnings.append(f"RESEARCH_MCP_SAVE_DIRS entry {entry.strip()!r} is not an absolute path; ignored")

    diagnostics_text = env.get("RESEARCH_MCP_DIAGNOSTICS", "").strip().lower() or "on"
    if diagnostics_text not in SWITCH_VALUES:
        warnings.append(f"RESEARCH_MCP_DIAGNOSTICS={diagnostics_text!r} is not 'on' or 'off'; using 'on'")
        diagnostics_text = "on"

    return Settings(
        contact=contact, log_dir=log_dir, render=render, download_dir=download_dir, save_dirs=tuple(save_dirs),
        diagnostics=SWITCH_VALUES[diagnostics_text], warnings=tuple(warnings),
    )


def user_agent(settings: Settings) -> str:
    """One honest bot-style User-Agent naming the project: Wikimedia answers 403 to bot agents without a URL
    (seen 2026-10-23). The personal contact is opt-in: it is never filled from the user's account."""
    parts = [f"research-mcp/{__version__}", f"+{PROJECT_URL}"]
    if settings.contact:
        parts.append(f"+{settings.contact}")
    return f"Mozilla/5.0 (compatible; {'; '.join(parts)})"
