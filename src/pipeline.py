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


def _load_existing_files(output: Path) -> set[str]:
    """Load already-parsed filenames from CSV."""
    if not output.exists():
        return set()
    with open(output, encoding="utf-8-sig") as f:
        return {row["file"] for row in csv.DictReader(f)}


def parse_all(pdf_dir: Path | None = None, output: Path | None = None) -> list[dict]:
    """Parse new PDFs only, append to CSV. Returns only new results."""
    pdf_dir = pdf_dir or DOWNLOAD_DIR
    output = output or OUTPUT_CSV

    if not pdf_dir.exists():
        logger.warning("PDF directory does not exist: %s", pdf_dir)
        return []

    existing_files = _load_existing_files(output)
    new_results: list[dict] = []
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
                if pdf_path.name in existing_files:
                    continue
                fut = executor.submit(
                    _parse_single_pdf, pdf_path, bank, parser_func, method
                )
                futures[fut] = bank

        for fut in as_completed(futures):
            bank = futures[fut]
            result = fut.result()
            if result:
                new_results.append(result)
                bank_stats[bank][0] += 1
            else:
                bank_stats[bank][1] += 1

    for bank in sorted(bank_stats):
        s, f = bank_stats[bank]
        if s or f:
            logger.info("  %s: %d success, %d fail", bank, s, f)

    new_results.sort(key=lambda r: (r["bank"], r["file"]))

    if new_results:
        write_header = not output.exists() or output.stat().st_size == 0
        with open(output, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=["bank", "file", "due_date", "amount"]
            )
            if write_header:
                writer.writeheader()
            writer.writerows(new_results)

    logger.info("New: %d records (total in CSV: %d)",
                len(new_results), len(existing_files) + len(new_results))
    return new_results


def load_results(path: Path | None = None) -> list[dict]:
    """Load parsed results from CSV, converting amount to int."""
    path = path or OUTPUT_CSV
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        try:
            r["amount"] = int(r["amount"])
        except (ValueError, KeyError):
            r["amount"] = 0
    return rows


def run(skip_download: bool = False, skip_notify: bool = False) -> list[dict]:
    """Full pipeline: download -> parse -> notify."""
    if not skip_download:
        download_pdfs()
    new_results = parse_all()
    # TODO: re-enable notify after full re-download
    # if not skip_notify and new_results:
    #     try:
    #         from .notifier import create_reminders
    #         create_reminders(new_results)
    #     except Exception:
    #         logger.exception("Failed to create reminders")
    return new_results
