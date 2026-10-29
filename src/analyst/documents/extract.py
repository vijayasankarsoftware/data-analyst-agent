"""Turn an uploaded file into plain text pages."""

import io
from dataclasses import dataclass
from pathlib import PurePath

SUPPORTED = {".pdf", ".docx", ".md", ".markdown", ".txt", ".csv", ".json", ".html"}


@dataclass
class Page:
    number: int | None
    text: str


def extract_pages(filename: str, data: bytes) -> list[Page]:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"unsupported file type {suffix!r}; supported: {sorted(SUPPORTED)}")

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [Page(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]

    if suffix == ".docx":
        from docx import Document

        doc = Document(io.BytesIO(data))
        return [Page(None, "\n\n".join(p.text for p in doc.paragraphs if p.text.strip()))]

    return [Page(None, data.decode("utf-8", errors="replace"))]
