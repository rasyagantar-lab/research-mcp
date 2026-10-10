"""PDF → text for read_webpage. pypdf is imported inside the function, like trafilatura: importing it is slow."""

import io
import time
from collections.abc import Callable
from dataclasses import dataclass

from research_mcp.errors import ToolError

# Seconds spent extracting before the rest of the pages are left out. The tool deadline is 45 s, and
# pypdf reads roughly 55,000 characters a second (measured 2026-10-07: 4 pages in 0.55 s).
PDF_TIME_BUDGET = 25.0


@dataclass(frozen=True)
class PdfText:
    title: str
    text: str
    pages: int
    extracted_pages: int
    empty_pages: int


def extract_pdf(
    data: bytes, *, time_budget: float = PDF_TIME_BUDGET, clock: Callable[[], float] = time.monotonic
) -> PdfText:
    """The text layer of every page, each introduced by a `[page N]` line so the model can cite pages.

    A page without text becomes `[page N] (no text)`. Raises ToolError(unsupported_content) for files that
    are not readable PDFs, are password-protected, or have no text at all (scans need OCR, which this
    tool does not do).
    """
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):  # an empty password opens "owner-locked" files
            raise ToolError(
                "unsupported_content", "the PDF is protected by a password", "ask for an unlocked copy of the file"
            )
        pages = reader.pages
        total = len(pages)
        title = _title(reader)
    except ToolError:
        raise
    except Exception:
        raise ToolError(
            "unsupported_content", "the file could not be read as a PDF", "it may be damaged or not really a PDF"
        ) from None
    if total == 0:
        raise ToolError("unsupported_content", "the PDF has no pages", "check that the link points to the real file")

    parts: list[str] = []
    empty = 0
    started = clock()
    for index in range(total):
        if clock() - started >= time_budget:
            break
        try:
            text = (pages[index].extract_text() or "").strip()
        except Exception:  # one odd page must not cost the whole document
            text = ""
        if text:
            parts.append(f"[page {index + 1}]\n{text}")
        else:
            empty += 1
            parts.append(f"[page {index + 1}] (no text)")
    done = len(parts)
    if done and empty == done:
        raise ToolError(
            "unsupported_content",
            "the PDF has no text layer (probably a scan, so there is no text to read)",
            "OCR is not supported; ask for a text version of the document",
        )
    return PdfText(title=title, text="\n\n".join(parts), pages=total, extracted_pages=done, empty_pages=empty)


def _title(reader) -> str:
    try:
        return " ".join(str(reader.metadata.title or "").split()) if reader.metadata else ""
    except Exception:
        return ""
