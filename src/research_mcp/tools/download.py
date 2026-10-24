"""download_file: save one file from the public web into the local research folder, or into an allowed save_dir.

The research folder (RESEARCH_MCP_DOWNLOAD_DIR, default ~/research-files) is the hand-off point: once the user
connects it to a claude.ai, phone or Cowork session, its files appear in the desktop's VM under $HOME/mnt, and a
saved file can be uploaded into an artifact or doc; the SHA-256 in the result lets Claude check that the upload
arrived byte for byte. A Claude Code session can only publish files
that sit under its working directory, so save_dir puts the file straight there; it must lie inside the research
folder or a folder listed in RESEARCH_MCP_SAVE_DIRS, never anywhere the model names.
"""

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from email.message import Message
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import anyio

from research_mcp.config import Settings
from research_mcp.errors import ToolError
from research_mcp.extract.image import ImageInfo, image_info
from research_mcp.net.urls import check_url
from research_mcp.services import Services
from research_mcp.tools.common import one_line

DOWNLOAD_LIMIT = 50 * 1024 * 1024
MAX_NAME_CHARS = 150
MAX_FOLDER_DEPTH = 5
MAX_SAME_NAME = 999
FALLBACK_NAME = "download"
UNLABELED_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_DRIVE = re.compile(r"^[A-Za-z]:")
# Windows refuses these as file names, with or without an extension.
_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                             *(f"LPT{i}" for i in range(1, 10))})


@dataclass(frozen=True)
class Saved:
    path: Path
    relative: str
    size: int
    sha256: str


async def download_file(
    services: Services, url: str, filename: str | None = None, folder: str = "", save_dir: str | None = None
) -> str:
    target = check_url(url)
    requested = None
    if filename is not None and filename.strip():
        requested = clean_name(filename)
        if not requested:
            raise _invalid_name(f"the file name {filename[:100]!r} has no usable characters")
    settings = services.settings
    root = save_root(settings, save_dir)
    parts = folder_parts(folder)
    result = await services.fetcher.get(target, max_bytes=DOWNLOAD_LIMIT)
    if result.status == 429:
        raise ToolError("rate_limited", "the site answered 429 Too Many Requests", "wait a few seconds and try again")
    if result.status >= 400:
        raise ToolError("http_error", f"the site answered HTTP {result.status}", "check the URL or try again later")
    name = choose_name(requested, result.disposition, result.url, result.content_type)
    saved = await anyio.to_thread.run_sync(
        save_bytes, root, parts, name, result.body, allowed_roots(settings), abandon_on_cancel=True
    )
    info = None
    if result.content_type.startswith("image/") or result.content_type in UNLABELED_TYPES:
        info = await anyio.to_thread.run_sync(image_info, result.body, result.content_type, abandon_on_cancel=True)
    if save_dir and save_dir.strip():
        return format_saved(result.url, saved, result.content_type, info, save_dir=root)
    # Sessions from claude.ai, the phone or Cowork see a connected folder at $HOME/mnt/<folder name> in the
    # desktop's Linux VM, so the research folder's files are there once the user has connected it.
    device_path = f"$HOME/mnt/{settings.download_dir.name}/{saved.relative}"
    return format_saved(result.url, saved, result.content_type, info, device_path=device_path)


def format_saved(
    url: str,
    saved: Saved,
    content_type: str,
    info: ImageInfo | None = None,
    save_dir: Path | None = None,
    device_path: str | None = None,
) -> str:
    lines = [
        f"[download_file] {url}",
        f"saved: {saved.path}",
        f"relative to {'save_dir' if save_dir else 'the research folder'}: {saved.relative}",
    ]
    if device_path:
        lines.append(f"device path (once the research folder is a connected folder): {device_path}")
    lines.append(f"size: {saved.size:,} bytes   type: {content_type or 'unknown'}")
    if info is not None:
        lines.append(f"image: {info.describe()}")
    elif content_type.startswith("image/"):
        lines.append("image: its pixel size could not be read (damaged, or a format Pillow cannot open)")
    lines.append(f"sha256: {saved.sha256}")
    if save_dir:
        lines.append("Tip: publish it with a page from this folder, then compare the sha256 to check the upload.")
    else:
        lines.append(
            "Tip: in Claude Code on this computer, pass save_dir (a folder in your working directory) to publish it "
            "with an artifact; a doc takes it as an asset upload from here. In a claude.ai, phone or Cowork session, "
            "the research folder must be a connected folder (the desktop app's Add folder, or "
            "device_request_folder_access, which the user approves): then the file is at the device path, and "
            "device_stage_files brings it over."
        )
    return "\n".join(lines)


def allowed_roots(settings: Settings) -> tuple[Path, ...]:
    return tuple(path.resolve() for path in (settings.download_dir, *settings.save_dirs))


def save_root(settings: Settings, save_dir: str | None) -> Path:
    """The folder a download lands in: the research folder, or save_dir when it lies inside an allowed folder."""
    if save_dir is None or not save_dir.strip():
        return settings.download_dir
    path = Path(save_dir.strip()).expanduser()
    if not path.is_absolute():
        raise _invalid_save_dir(settings, f"{save_dir[:200]!r} is not an absolute path")
    if not _inside_any(path.resolve(), allowed_roots(settings)):
        raise _invalid_save_dir(settings, f"{save_dir[:200]!r} is outside the folders download_file may write to")
    return path


def clean_name(text: str) -> str:
    """A file or folder name Windows accepts: no path separators or reserved characters, no trailing dots or
    spaces, not a reserved device name, at most MAX_NAME_CHARS long (the extension is kept when shortening)."""
    name = _UNSAFE_CHARS.sub("_", one_line(text)).strip().rstrip(". ")
    if not name.strip("."):
        return ""
    if name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        name = "_" + name
    if len(name) > MAX_NAME_CHARS:
        stem, ext = os.path.splitext(name)
        ext = ext if len(ext) <= 16 else ""
        name = stem[: MAX_NAME_CHARS - len(ext)].rstrip(". ") + ext
    return name


def folder_parts(folder: str) -> list[str]:
    """The sub-folder of the research folder as clean path parts; absolute paths and '..' are refused."""
    text = folder.strip().replace("\\", "/")
    if not text:
        return []
    if text.startswith("/") or _DRIVE.match(text):
        raise _invalid_folder(f"{folder[:100]!r} is an absolute path")
    parts = [part for part in text.split("/") if part.strip() not in ("", ".")]
    if any(part.strip() == ".." for part in parts):
        raise _invalid_folder(f"{folder[:100]!r} leaves the research folder")
    if len(parts) > MAX_FOLDER_DEPTH:
        raise _invalid_folder(f"{folder[:100]!r} is more than {MAX_FOLDER_DEPTH} folders deep")
    cleaned = [clean_name(part) for part in parts]
    if not all(cleaned):
        raise _invalid_folder(f"{folder[:100]!r} has a folder name with no usable characters")
    return cleaned


def choose_name(requested: str | None, disposition: str | None, url: str, content_type: str) -> str:
    """The caller's name, else the server's Content-Disposition name, else the last URL segment; an
    extension from the content type is added when the name has none."""
    name = (
        requested
        or clean_name(_disposition_name(disposition) or "")
        or clean_name(unquote(PurePosixPath(urlsplit(url).path).name))
        or FALLBACK_NAME
    )
    if not os.path.splitext(name)[1]:
        name = clean_name(name + _extension(content_type)) or name
    return name


def save_bytes(root: Path, parts: list[str], name: str, body: bytes, allowed: tuple[Path, ...] = ()) -> Saved:
    """Write `body` under root/parts without ever overwriting: a taken name becomes 'name (1).ext' and so on.

    With `allowed`, root itself must still resolve inside one of those folders once it exists."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        real_root = root.resolve()
        if allowed and not _inside_any(real_root, allowed):
            raise _invalid_folder("save_dir leads outside the folders download_file may write to")
        directory = real_root.joinpath(*parts)
        _ensure_inside(directory.resolve(), real_root)  # a junction inside the folder must not lead out of it
        directory.mkdir(parents=True, exist_ok=True)
        directory = directory.resolve()
        _ensure_inside(directory, real_root)
        for path in _candidates(directory, name):
            try:
                handle = open(path, "xb")
            except FileExistsError:
                continue
            try:
                with handle:
                    handle.write(body)
            except OSError:
                path.unlink(missing_ok=True)
                raise
            return Saved(path, path.relative_to(real_root).as_posix(), len(body), hashlib.sha256(body).hexdigest())
    except OSError as exc:
        raise ToolError(
            "save_failed", f"could not write the file ({exc.strerror or type(exc).__name__})",
            "check that RESEARCH_MCP_DOWNLOAD_DIR points to a writable folder with free space",
        ) from None
    raise ToolError(
        "save_failed", f"more than {MAX_SAME_NAME} files are already named like {name!r}",
        "pass another filename or folder",
    )


def _candidates(directory: Path, name: str):
    stem, ext = os.path.splitext(name)
    yield directory / name
    for number in range(1, MAX_SAME_NAME + 1):
        yield directory / f"{stem} ({number}){ext}"


def _ensure_inside(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise _invalid_folder("the folder leads outside the save folder")


def _inside_any(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _disposition_name(header: str | None) -> str | None:
    if not header:
        return None
    message = Message()
    message["content-disposition"] = header
    try:
        return message.get_filename()
    except (ValueError, LookupError):  # a malformed RFC 2231 name must not sink the download
        return None


def _extension(content_type: str) -> str:
    if content_type in UNLABELED_TYPES:
        return ""
    return mimetypes.guess_extension(content_type, strict=False) or ""


def _invalid_name(message: str) -> ToolError:
    return ToolError("invalid_argument", message, "pass a plain file name such as 'report.pdf', or leave it out")


def _invalid_folder(message: str) -> ToolError:
    return ToolError(
        "invalid_argument", message, "pass a sub-folder of the save folder such as 'images/cats', or leave it out"
    )


def _invalid_save_dir(settings: Settings, message: str) -> ToolError:
    folders = "; ".join(str(path) for path in (settings.download_dir, *settings.save_dirs))
    return ToolError(
        "invalid_argument", message,
        f"pass an absolute folder inside one of these ({folders}), or leave save_dir out to use the research folder",
    )
