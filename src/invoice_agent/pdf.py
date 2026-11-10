from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def pdf_to_text(path: str | Path) -> str:
    """Plain text in PDF stream order (not necessarily reading order), one marker per page."""
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        pages.append(f"[page {i}]\n{page.extract_text() or ''}".strip())
    return "\n\n".join(pages)
