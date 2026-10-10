"""Long-lived objects shared by the tools for the life of one server process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from research_mcp.config import Settings, user_agent
from research_mcp.extract.pages import PageCache
from research_mcp.net.client import Fetcher
from research_mcp.tools.search import DdgsProvider, SearchProvider


@dataclass
class Services:
    settings: Settings
    fetcher: Fetcher
    pages: PageCache
    search: SearchProvider


@asynccontextmanager
async def open_services(settings: Settings) -> AsyncIterator[Services]:
    fetcher = Fetcher(user_agent(settings))
    try:
        yield Services(settings=settings, fetcher=fetcher, pages=PageCache(), search=DdgsProvider())
    finally:
        await fetcher.aclose()
