"""Paging long text and the small in-memory page cache that keeps offset reads consistent and cheap."""

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from research_mcp.errors import ToolError

PARAGRAPH_LOOKBACK = 500


@dataclass(frozen=True)
class Window:
    start: int
    end: int
    total: int
    text: str

    @property
    def next_offset(self) -> int | None:
        return None if self.end >= self.total else self.end


def paginate(text: str, offset: int, max_chars: int) -> Window:
    """Characters [offset, offset+max_chars), cut back to the nearest paragraph break within 500 characters.

    Reading window after window from offset 0 via next_offset returns the whole text exactly once.
    """
    total = len(text)
    if total == 0 and offset == 0:
        return Window(0, 0, 0, "")
    if offset >= total:
        raise ToolError(
            "offset_out_of_range",
            f"offset {offset} is past the end of the page ({total} characters)",
            "use an offset below the total, or 0 to start again",
        )
    end = min(offset + max_chars, total)
    if end < total:
        cut = text.rfind("\n\n", max(offset + 1, end - PARAGRAPH_LOOKBACK), end)
        if cut != -1:
            end = cut + 2
    return Window(offset, end, total, text[offset:end])


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    markdown: str
    render: str
    fetched_at: str
    note: str | None = None


class PageCache:
    """LRU of extracted pages, keyed by (normalized requested URL, render mode)."""

    def __init__(self, max_entries: int = 20, ttl: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._entries: OrderedDict[tuple[str, str], tuple[float, Page]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl
        self._clock = clock

    def get(self, key: tuple[str, str]) -> Page | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, page = entry
        if self._clock() - stored_at > self._ttl:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return page

    def put(self, key: tuple[str, str], page: Page) -> None:
        self._entries[key] = (self._clock(), page)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
