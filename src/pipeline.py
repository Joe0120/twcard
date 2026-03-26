"""Orchestration: download -> decrypt -> parse -> output CSV."""

import csv
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import DOWNLOAD_DIR, NUM_THREADS, OUTPUT_CSV, list_pdfs
from .gmail_downloader import download_pdfs
from .parsers import PARSERS
from .pdf_extractor import extract_text

logger = logging.getLogger(__name__)


def _parse_single_pdf(
    pdf_path: Path, bank: str, parser_func, method: str
) -> dict | None:
    """Parse a single PDF. Returns a result dict or None on failure."""
    try:
        text = extract_text(pdf_path, method, bank)
        due_date, amount = parser_func(text)

        if due_date is None and amount is None:
            logger.warning("  FAIL %s: could not parse", pdf_path.name)
            return None
        elif due_date and amount is not None:
            logger.info(
                "  OK %s: due=%s amount=%s", pdf_path.name, due_date, amount
            )
            return {
                "bank": bank,
                "file": pdf_path.name,
                "due_date": due_date,
                "amount": amount,
            }
        else:
            logger.warning(
                "  FAIL %s: due=%s amount=%s", pdf_path.name, due_date, amount
            )
            return None
    except Exception:
        logger.exception("  ERROR %s", pdf_path.name)
        return None


def parse_all(pdf_dir: Path | None = None, output: Path | None = None) -> list[dict]:
    """Parse all PDFs in bank subdirectories. Returns list of result dicts."""
    pdf_dir = pdf_dir or DOWNLOAD_DIR
    output = output or OUTPUT_CSV

    if not pdf_dir.exists():
        logger.warning("PDF directory does not exist: %s", pdf_dir)
        return []

    results: list[dict] = []
    futures: dict = {}
    bank_stats: dict[str, list[int]] = {}

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        for bank in sorted(p.name for p in pdf_dir.iterdir() if p.is_dir()):
            config = PARSERS.get(bank)
            if not config:
                continue

            parser_func, method = config
            bank_stats[bank] = [0, 0]

            for pdf_path in list_pdfs(pdf_dir / bank):
                fut = executor.submit(
                    _parse_single_pdf, pdf_path, bank, parser_func, method
                )
                futures[fut] = bank

        for fut in as_completed(futures):
            bank = futures[fut]
            result = fut.result()
            if result:
                results.append(result)
                bank_stats[bank][0] += 1
            else:
                bank_stats[bank][1] += 1

    for bank in sorted(bank_stats):
        s, f = bank_stats[bank]
        logger.info("  %s: %d success, %d fail", bank, s, f)

    results.sort(key=lambda r: (r["bank"], r["file"]))

    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["bank", "file", "due_date", "amount"])
        writer.writeheader()
        writer.writerows(results)

    logger.info("Output: %s (%d records)", output, len(results))
    return results


def run(skip_download: bool = False) -> list[dict]:
    """Full pipeline: download then parse."""
    if not skip_download:
        download_pdfs()
    return parse_all()
