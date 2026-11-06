"""Module 1 — Document loading.

Reads plain-text / Markdown files from a folder (or from an in-memory mapping)
and turns them into immutable ``Document`` objects.  The first non-empty line
of a file is its title; everything after it is the body.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Union

SUPPORTED_EXTENSIONS = (".txt", ".md")


@dataclass(frozen=True)
class Document:
    doc_id: int
    title: str
    body: str
    source: str

    @property
    def indexed_text(self) -> str:
        """Title and body are both searchable."""
        return f"{self.title}. {self.body}" if self.body else self.title


def split_title(raw: str, fallback: str) -> Tuple[str, str]:
    """Split raw file content into ``(title, body)``."""
    text = raw.strip()
    if not text:
        return fallback, ""
    first, _, rest = text.partition("\n")
    title = first.strip().lstrip("#").strip() or fallback
    return title, rest.strip()


def load_directory(path: Union[str, Path]) -> List[Document]:
    """Load every supported file in *path*, sorted by file name."""
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"Document folder not found: {folder}")

    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    documents: List[Document] = []
    for file in files:
        raw = file.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            continue
        fallback = file.stem.replace("-", " ").replace("_", " ").title()
        title, body = split_title(raw, fallback)
        documents.append(Document(len(documents), title, body, file.name))
    return documents


def load_texts(texts: Dict[str, str]) -> List[Document]:
    """Build documents from a ``{name: raw text}`` mapping (handy for tests)."""
    documents: List[Document] = []
    for name, raw in texts.items():
        if not raw.strip():
            continue
        title, body = split_title(raw, name)
        documents.append(Document(len(documents), title, body, name))
    return documents


# ---- uploaded files ---------------------------------------------------
UPLOAD_EXTENSIONS = (".txt", ".md", ".pdf", ".docx")


class UnsupportedFile(ValueError):
    """Raised when an uploaded file cannot be turned into text."""


def _docx_text(data: bytes) -> str:
    import io
    import re
    import zipfile
    from xml.etree import ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.getinfo("word/document.xml").file_size > 50_000_000:
                raise UnsupportedFile("That Word file is too large to read.")
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise UnsupportedFile("That file is not a valid .docx document.")
    paras = []
    for p in root.iter(ns + "p"):
        parts = []
        for node in p.iter():
            if node.tag == ns + "t" and node.text:
                parts.append(node.text)
            elif node.tag in (ns + "tab", ns + "br"):
                parts.append(" ")
        line = re.sub(r"[ \t]+", " ", "".join(parts)).strip()
        if line:
            paras.append(line)
    return "\n\n".join(paras)


def _pdf_text(data: bytes) -> str:
    import io

    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:  # pragma: no cover
        raise UnsupportedFile("PDF support is not installed on this server.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnsupportedFile("That PDF is password protected.")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except UnsupportedFile:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError):
        raise UnsupportedFile("That file is not a readable PDF.")
    text = "\n\n".join(p for p in pages if p)
    if not text.strip():
        raise UnsupportedFile("That PDF has no text layer (it may be a scan).")
    return text


def extract_text(filename: str, data: bytes) -> str:
    """Return the plain text of an uploaded file, based on its extension."""
    ext = Path(filename).suffix.lower()
    if ext in (".txt", ".md"):
        return data.decode("utf-8-sig", errors="replace").replace("\x00", "")
    if ext == ".docx":
        return _docx_text(data)
    if ext == ".pdf":
        return _pdf_text(data)
    raise UnsupportedFile(
        f"Unsupported file type. Use {', '.join(UPLOAD_EXTENSIONS)}.")


def documents_from_texts(items: List[Tuple[str, str]]) -> List[Document]:
    """Build documents from ordered ``(file name, raw text)`` pairs."""
    documents: List[Document] = []
    for name, raw in items:
        fallback = Path(name).stem.replace("-", " ").replace("_", " ").title() or name
        title, body = split_title(raw, fallback)
        documents.append(Document(len(documents), title, body, name))
    return documents
