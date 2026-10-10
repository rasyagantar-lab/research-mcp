"""Entry point: `python -m research_mcp` serves MCP over stdio (how Claude Desktop starts it)."""

import logging

from research_mcp.config import load_settings
from research_mcp.logs import setup_logging
from research_mcp.server import build_server
from research_mcp.services import open_services


def main() -> None:
    settings = load_settings()
    log_file = setup_logging(settings.log_dir)
    log = logging.getLogger("research_mcp")
    for warning in settings.warnings:
        log.warning(warning)
    log.info("research-mcp starting; log file: %s", log_file)
    build_server(lambda: open_services(settings)).run()


if __name__ == "__main__":
    main()
