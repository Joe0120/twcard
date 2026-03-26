"""PDF text extraction with password decryption support.

Extraction methods:
- pdfplumber: default, works for most banks
- pdfminer: plain text extraction via pdfminer.six
- fitz_cid: PyMuPDF + CID font mapping from pdfminer Differences
- pdfminer_or_fitz_cid: tries pdfminer first, falls back to fitz_cid if garbled
"""

import logging
import re
from os import close as os_close
from pathlib import Path
from tempfile import mkstemp

import fitz  # PyMuPDF
import pdfplumber
from pdfminer.high_level import extract_text as pdfminer_extract
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdftypes import resolve1

from .config import load_passwords

logger = logging.getLogger(__name__)

_passwords: dict[str, list[str]] | None = None


def _get_passwords() -> dict[str, list[str]]:
    global _passwords
    if _passwords is None:
        _passwords = load_passwords() or {}
    return _passwords


# ── Password handling ─────────────────────────────────────────────────────

def _decrypt_pdf_if_needed(pdf_path: Path, bank_folder: str) -> Path:
    """Try to open PDF; if encrypted, decrypt to a temp file. Returns usable path."""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        logger.warning("Cannot open %s: %s", pdf_path.name, exc)
        return pdf_path

    if not doc.is_encrypted:
        doc.close()
        return pdf_path

    passwords = _get_passwords().get(bank_folder, [])
    for pw in passwords:
        try:
            if doc.authenticate(pw):
                fd, tmp_path = mkstemp(suffix=".pdf")
                os_close(fd)
                decrypted = Path(tmp_path)
                try:
                    doc.save(str(decrypted))
                except Exception:
                    decrypted.unlink(missing_ok=True)
                    raise
                doc.close()
                return decrypted
        except Exception as exc:
            logger.debug("Password attempt failed for %s: %s", pdf_path.name, exc)
            continue

    doc.close()
    logger.warning("Cannot decrypt %s (tried %d passwords)", pdf_path.name, len(passwords))
    return pdf_path


# ── CID map extraction ────────────────────────────────────────────────────

def get_cid_map(pdf_path: str | Path) -> dict[int, str]:
    """Extract code->char mapping from PDF font Differences."""
    cid_map: dict[int, str] = {}
    with open(pdf_path, "rb") as f:
        parser = PDFParser(f)
        doc = PDFDocument(parser)
        for page in PDFPage.create_pages(doc):
            resources = resolve1(page.resources)
            fonts = resolve1(resources.get("Font", {}))
            for _, fref in fonts.items():
                font = resolve1(fref)
                enc = resolve1(font.get("Encoding"))
                if isinstance(enc, dict) and "Differences" in enc:
                    diffs = enc["Differences"]
                    code = 0
                    for item in diffs:
                        if isinstance(item, int):
                            code = item
                        else:
                            name = getattr(item, "name", str(item))
                            if isinstance(name, bytes):
                                name = name.decode("ascii", errors="replace")
                            m = re.match(r"UNIC([0-9A-Fa-f]{4})", str(name))
                            if m:
                                cid_map[code] = chr(int(m.group(1), 16))
                            code += 1
            break  # only first page
    return cid_map


def _fitz_cid_extract(pdf_path: str | Path) -> str | None:
    """Extract text via PyMuPDF + CID font mapping."""
    cid_map = get_cid_map(pdf_path)
    if not cid_map:
        return None
    with fitz.open(str(pdf_path)) as doc:
        if len(doc) == 0:
            return None
        text = doc[0].get_text()
    decoded = (cid_map.get(ord(ch), ch) for ch in text)
    return "".join(c for c in decoded if c is not None)


# ── Public extraction API ─────────────────────────────────────────────────

def extract_text(pdf_path: Path, method: str, bank_folder: str) -> str:
    """Extract text from a PDF using the specified method.

    Handles decryption transparently before extraction.
    """
    usable = _decrypt_pdf_if_needed(pdf_path, bank_folder)

    try:
        text = _extract(usable, method)
    finally:
        # Clean up decrypted temp file
        if usable != pdf_path and usable.exists():
            usable.unlink()

    return text


def _extract(pdf_path: Path, method: str) -> str:
    path_str = str(pdf_path)

    if method == "pdfplumber":
        with pdfplumber.open(path_str) as pdf:
            return pdf.pages[0].extract_text() or ""

    elif method == "pdfminer":
        return pdfminer_extract(path_str)

    elif method == "fitz_cid":
        return _fitz_cid_extract(path_str) or ""

    elif method == "pdfminer_or_fitz_cid":
        text = pdfminer_extract(path_str)
        if "cid:" in text or len(text.strip()) < 50:
            text = _fitz_cid_extract(path_str) or ""
        return text

    raise ValueError(f"Unknown extraction method: {method!r}")
