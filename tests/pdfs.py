"""Small real PDFs for tests, built by hand so no PDF-writing library is needed to produce them."""

import io


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str], *, title: str | None = None, pad: int = 0) -> bytes:
    """A valid PDF with one Helvetica line per page ("" gives a page without any text, like a scan).

    `pad` appends an unused stream of that many bytes, to make the file big without making it slow to read.
    """
    count = len(pages)
    page_ids = [4 + 2 * i for i in range(count)]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{pid} 0 R' for pid in page_ids)}] /Count {count} >>".encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, text in enumerate(pages):
        page_id, content_id = 4 + 2 * i, 5 + 2 * i
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        stream = f"BT /F1 12 Tf 72 720 Td ({_escape(text)}) Tj ET".encode() if text else b""
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    next_id = 4 + 2 * count
    info_id = None
    if title is not None:
        info_id = next_id
        objects[info_id] = f"<< /Title ({_escape(title)}) >>".encode()
        next_id += 1
    if pad:
        objects[next_id] = b"<< /Length %d >>\nstream\n" % pad + b"\0" * pad + b"\nendstream"
        next_id += 1

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += b"%d 0 obj\n" % object_id + objects[object_id] + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % next_id
    for object_id in range(1, next_id):
        out += b"%010d 00000 n \n" % offsets[object_id]
    trailer = f"<< /Size {next_id} /Root 1 0 R" + (f" /Info {info_id} 0 R" if info_id else "") + " >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def encrypt_pdf(data: bytes, *, user_password: str, owner_password: str = "owner") -> bytes:
    """The same PDF locked with AES-128. An empty user password still opens freely (only editing is locked)."""
    import pypdf

    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
