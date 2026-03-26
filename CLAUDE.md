# CLAUDE.md - Technical Guide for AI Assistants

This document is for Claude Code (or other AI coding assistants) working on this project.

## Project Overview

**twcard** auto-downloads credit card PDF statements from Gmail, extracts payment due dates and amounts, and outputs structured data. Designed for Taiwan bank statements.

## Architecture

```
src/
  config.py           # Constants, BANK_RULES, SKIP_PATTERNS, list_pdfs()
  gmail_downloader.py # Gmail API: auth, download, dedup manifest
  pdf_extractor.py    # PDF text extraction with 4 methods + password decryption
  parsers.py          # 15 bank parsers via @register decorator
  pipeline.py         # Orchestration: download -> parse -> CSV
  cli.py              # argparse entry point
```

## Key Design Decisions

### PDF Extraction Methods

Each bank's PDF uses different encoding. There are 4 extraction methods:

| Method | When to Use | How It Works |
|--------|-------------|--------------|
| `pdfplumber` | Most banks - text is directly readable | Standard text extraction |
| `pdfminer` | Fubon new format - pdfplumber fails but pdfminer works | pdfminer.six high-level API |
| `fitz_cid` | Far Eastern, Fubon old - Type3 fonts with custom CID encoding | PyMuPDF raw bytes + CID-to-Unicode mapping from font Differences table |
| `pdfminer_or_fitz_cid` | Fubon - tries pdfminer first, falls back to fitz_cid if garbled | Combination strategy |

### CID Font Decoding (fitz_cid)

Some banks (Far Eastern, old Fubon) use Type3 fonts with custom Encoding Differences. pdfplumber and pdfminer output garbled text (e.g., `ææıa(cid:240)(cid:243)`).

The solution:
1. `get_cid_map()` reads the font Differences table from PDF metadata via pdfminer
2. Maps byte code -> Unicode character (e.g., code 240 -> '0', code 241 -> '1')
3. `fitz.open()` (PyMuPDF) extracts raw byte values as Latin-1 characters
4. Apply the CID map to decode: `chr(code) -> mapped_char`

This is in `pdf_extractor.py:get_cid_map()` and `_fitz_cid_extract()`.

### Parser Registry Pattern

Parsers use a decorator to self-register:

```python
@register("008 華南銀行", extract_method="pdfplumber")
def parse_008(text: str) -> tuple[str | None, int | None]:
    ...
```

The registry `PARSERS` maps `bank_folder_name -> (parser_func, extract_method)`.

### Bank Statement PDF Formats

Banks fall into two categories:

**Keyword-based** (text has labels like `繳款截止日`, `本期應繳總額`):
- 008 華南, 009 彰化, 053 台中, 103 新光, 807 永豐, 812 台新

**Position-based** (no labels, data at fixed line indices):
- 011 上海 (line 12=amount, line 15=due_date)
- 081 匯豐 (line 3=due_date, line 6=amount)
- 803 聯邦 (line 2=amount, line 3=due_date)
- 808 玉山 (line 2=amount+元, line 4=due_date)
- 809 凱基 (line 3=due_date, line 5=amount)
- 810 星展 (line 8=dates, line 10=amounts)
- 822 中國信託 (line 5=due_date, line 7=amount)

**IMPORTANT**: Position-based parsers are fragile. If a bank changes their PDF layout, the line indices must be updated. Always verify with multiple months of statements.

### Download Deduplication

`data/downloaded.json` stores `{message_id: {files, date}}`. On each run, already-downloaded message IDs are skipped. The manifest is written atomically (write to `.tmp` then `rename`).

### Error Isolation

Every individual PDF parse and every Gmail message download is wrapped in try/except. One failure does not crash the pipeline.

## Coding Patterns

### Shared Parser Helpers

Use these instead of writing raw regex:

```python
# Extract from a specific line index
line_at(lines, idx, r"(\d{2,3}/\d{2}/\d{2})")

# Find keyword then extract from next line
find_after_keyword(lines, "繳款截止日", r"(\d{2,3}/\d{1,2}/\d{1,2})")

# Normalize dates (handles ROC year and NO_PAYMENT sentinel)
normalize_date("115/03/20")  # -> "2026/03/20"
normalize_date("不需繳款")     # -> "不需繳款" (passthrough)

# Parse amount string
parse_amount("14,295")  # -> 14295
parse_amount("-411")    # -> -411

# No-payment return tuple
no_payment()  # -> ("不需繳款", 0)
```

### Constants

- `NO_PAYMENT = "不需繳款"` - use this constant, never hardcode the string
- `BANK_RULES` in config.py - email keyword -> bank folder mapping
- `SKIP_PATTERNS` - filename patterns to skip (payment slips)

### Type Hints

All functions must have type hints. Parser return type is always:
```python
tuple[str | None, int | None]
```

### Concurrency

- Gmail download: `ThreadPoolExecutor` (I/O bound)
- PDF parsing: `ThreadPoolExecutor` (mixed I/O + CPU, GIL limits true parallelism but simpler than ProcessPool)
- Thread safety: `_file_lock` for file writes, `_manifest_lock` for manifest updates

### File Permissions

Credential files (`token.json`, `passwords.json`, `.env`, `credentials.json`) must be created with `0o600` permissions. See `gmail_downloader.py` for the `os.open()` pattern.

## How to Add a New Bank Parser

1. **Get a sample PDF** and check which extraction method works:

```python
import pdfplumber
with pdfplumber.open("sample.pdf") as pdf:
    text = pdf.pages[0].extract_text()
    print(text[:500])
```

If garbled, try `fitz_cid`:
```python
from src.pdf_extractor import get_cid_map, _fitz_cid_extract
text = _fitz_cid_extract("sample.pdf")
```

2. **Identify the pattern** - print first 15 lines, find where due_date and amount are:

```python
for i, line in enumerate(text.split("\n")[:15]):
    print(f"{i:2}: {line}")
```

3. **Write the parser** in `src/parsers.py`:

```python
@register("XXX 新銀行", extract_method="pdfplumber")
def parse_xxx(text: str) -> tuple[str | None, int | None]:
    lines = text.split("\n")
    due_date = line_at(lines, 5, r"(\d{2,3}/\d{2}/\d{2})")
    amount = line_at(lines, 7, r"(-?[\d,]+)")
    return normalize_date(due_date), parse_amount(amount)
```

4. **Add bank email rule** in `src/config.py` `BANK_RULES`:

```python
("newbank.com.tw", "XXX 新銀行"),
```

5. **Add PDF password** in `passwords.json`:

```json
"XXX 新銀行": {
    "passwords": ["PASSWORD1"]
}
```

6. **Test** with all available statements for that bank:

```bash
python -m src.cli parse
```

7. **Verify** the output in `statements.csv` matches the actual PDF content.

## How to Add a New Notification Platform

Currently supports Apple Reminders via CalDAV (not yet integrated into the pipeline). To add a new platform:

1. Create `src/notifiers/base.py` with an abstract notifier:

```python
from abc import ABC, abstractmethod

class Notifier(ABC):
    @abstractmethod
    def create_reminder(self, bank: str, amount: int, due_date: str) -> bool:
        ...
```

2. Implement for each platform:

```python
# src/notifiers/caldav_notifier.py - Apple Reminders via CalDAV
# src/notifiers/line_notify.py - LINE Notify
# src/notifiers/telegram_bot.py - Telegram Bot
# src/notifiers/email_notifier.py - Email notification
```

3. CalDAV (Apple Reminders) works from Linux without macOS:
   - Endpoint: `https://caldav.icloud.com/`
   - Auth: Apple ID + app-specific password
   - Protocol: VTODO over CalDAV
   - Library: `caldav` Python package

4. Register notifiers in pipeline and add CLI flags.

## How to Handle Banks with CID Garbled PDFs

If a new bank's PDF is garbled (shows `(cid:XXX)` or Latin-like nonsense):

1. Check font type:
```python
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdftypes import resolve1

with open("sample.pdf", "rb") as f:
    parser = PDFParser(f)
    doc = PDFDocument(parser)
    for page in PDFPage.create_pages(doc):
        resources = resolve1(page.resources)
        fonts = resolve1(resources.get("Font", {}))
        for name, ref in fonts.items():
            font = resolve1(ref)
            print(f"Font {name}: Subtype={font.get('Subtype')}")
            enc = resolve1(font.get('Encoding'))
            print(f"  Encoding: {type(enc).__name__}")
            print(f"  ToUnicode: {font.get('ToUnicode')}")
        break
```

2. If **Type3 + Differences** -> use `fitz_cid` method (like Far Eastern, old Fubon)
3. If **Type0 + Identity-H + ToUnicode** -> may need custom CMap parsing (like old DBS, Citibank - currently unsupported)
4. If **Type0 + no ToUnicode** -> OCR is the only option

## Known Limitations

- **Citibank (021)**: All PDFs use Type0 fonts without usable CMap. Not parseable without OCR.
- **DBS old format (pre-2023/08)**: Type0 fonts, no Differences table. PyMuPDF can read the text but the parser may fail on very old layouts.
- **KGI old format (pre-2025/09)**: CID garbled, not parseable.
- **Position-based parsers**: If a bank changes PDF layout, the parser breaks silently (extracts wrong data). Always verify after bank format updates.

## Testing Changes

After any parser change, run:

```bash
python -m src.cli parse
```

Compare the total record count (currently 497) and spot-check the latest statement for each bank. The output should match the actual PDF content.

## Dependencies

- `google-api-python-client` + `google-auth-oauthlib` - Gmail API
- `pdfplumber` - Primary PDF text extraction
- `pdfminer.six` - Secondary PDF extraction + CID font metadata
- `PyMuPDF (fitz)` - CID font decoding + PDF decryption
- `python-dotenv` - Optional, for `.env` loading
- `caldav` - Optional, for Apple Reminders
