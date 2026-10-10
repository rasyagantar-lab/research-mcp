"""Settings from RESEARCH_MCP_* environment variables (Claude Desktop passes them via the config `env` block)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from research_mcp import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_MODES = ("auto", "off")
MAX_CONTACT_CHARS = 200


@dataclass(frozen=True)
class Settings:
    contact: str
    log_dir: Path
    render: str
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

    return Settings(contact=contact, log_dir=log_dir, render=render, warnings=tuple(warnings))


def user_agent(settings: Settings) -> str:
    """One honest bot-style User-Agent. The contact is opt-in: it is never filled from the user's account."""
    if settings.contact:
        return f"Mozilla/5.0 (compatible; research-mcp/{__version__}; +{settings.contact})"
    return f"Mozilla/5.0 (compatible; research-mcp/{__version__})"
