import pytest

from analyst.documents.chunk import chunk_pages
from analyst.documents.extract import Page, extract_pages


def test_small_text_is_one_chunk() -> None:
    chunks = chunk_pages([Page(1, "Hello world.\n\nSecond paragraph.")])
    assert len(chunks) == 1 and chunks[0].page == 1


def test_long_text_is_split_with_overlap() -> None:
    paras = [f"Paragraph {i}. " + "word " * 80 for i in range(30)]
    chunks = chunk_pages([Page(None, "\n\n".join(paras))], size=1000, overlap=100)
    assert len(chunks) > 5
    assert all(len(c.text) <= 1000 + 100 + 2 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_giant_paragraph_is_hard_split() -> None:
    chunks = chunk_pages([Page(None, "x" * 5000)], size=1000, overlap=0)
    assert len(chunks) == 5


def test_extract_markdown() -> None:
    pages = extract_pages("notes.md", b"# Title\n\nbody")
    assert pages[0].text.startswith("# Title")


def test_rejects_unknown_types() -> None:
    with pytest.raises(ValueError):
        extract_pages("malware.exe", b"MZ")
