"""Split text into overlapping chunks along natural boundaries.

Why chunk at all? Embeddings of a whole document blur many topics into one vector;
small chunks keep each vector about one thing, so retrieval is precise. Overlap keeps
a sentence that straddles a boundary findable from both sides.
"""

import re
from dataclasses import dataclass

from analyst.documents.extract import Page


@dataclass
class Chunk:
    index: int
    page: int | None
    text: str


def _split_long(text: str, size: int) -> list[str]:
    """Split an oversized paragraph by sentences, then hard-cut if still too long."""
    parts: list[str] = []
    buf = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(buf) + len(sentence) + 1 <= size:
            buf = f"{buf} {sentence}".strip()
            continue
        if buf:
            parts.append(buf)
        while len(sentence) > size:
            parts.append(sentence[:size])
            sentence = sentence[size:]
        buf = sentence
    if buf:
        parts.append(buf)
    return parts


def chunk_pages(pages: list[Page], *, size: int = 1800, overlap: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        paragraphs: list[str] = []
        for para in re.split(r"\n\s*\n", page.text):
            para = para.strip()
            if para:
                paragraphs.extend(_split_long(para, size) if len(para) > size else [para])

        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > size:
                chunks.append(Chunk(len(chunks), page.number, buf))
                buf = buf[-overlap:] + "\n\n" + para if overlap else para
            else:
                buf = f"{buf}\n\n{para}" if buf else para
        if buf.strip():
            chunks.append(Chunk(len(chunks), page.number, buf))
    return chunks
