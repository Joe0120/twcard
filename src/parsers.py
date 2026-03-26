"""Bank statement parsers using a registry pattern.

Each parser extracts (due_date, amount) from the first page of a bank statement PDF.
"""

import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Registry ──────────────────────────────────────────────────────────────

ParserFunc = Callable[[str], tuple[str | None, int | None]]
PARSERS: dict[str, tuple[ParserFunc, str]] = {}


def register(bank_folder: str, extract_method: str = "pdfplumber"):
    """Decorator to register a parser for a bank folder."""
    def decorator(func: ParserFunc) -> ParserFunc:
        PARSERS[bank_folder] = (func, extract_method)
        return func
    return decorator


# ── Shared helpers ────────────────────────────────────────────────────────

NO_PAYMENT = "不需繳款"


def normalize_date(date_str: str | None) -> str | None:
    """Convert ROC or western date string to YYYY/MM/DD. Passes through NO_PAYMENT sentinel."""
    if not date_str:
        return None
    if date_str == NO_PAYMENT:
        return NO_PAYMENT
    date_str = date_str.strip().replace("-", "/").replace(".", "/")

    m = re.match(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", date_str)
    if m:
        year = int(m.group(1))
        if year < 200:
            year += 1911
        return f"{year}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"

    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", date_str)
    if m:
        return f"{int(m.group(1))}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"

    return date_str


def parse_amount(text: str | None) -> int | None:
    """Parse amount string (with commas) to int."""
    if not text:
        return None
    text = text.strip().replace(",", "").replace("，", "")
    try:
        return int(float(text))
    except ValueError:
        return None


def no_payment(amount: int = 0) -> tuple[str, int]:
    """Standard return for statements that require no payment."""
    return (NO_PAYMENT, amount)


def line_at(lines: list[str], idx: int, pattern: str) -> str | None:
    """Extract first regex match from a specific line index."""
    if idx >= len(lines):
        return None
    m = re.match(pattern, lines[idx].strip())
    return m.group(1) if m else None


def find_after_keyword(
    lines: list[str], keyword: str, pattern: str, same_line: bool = False
) -> str | None:
    """Find keyword in lines, then extract pattern from the next line (or same line)."""
    for i, line in enumerate(lines):
        if keyword in line:
            target = line if same_line else (lines[i + 1] if i + 1 < len(lines) else "")
            m = re.search(pattern, target)
            return m.group(1) if m else None
    return None


# ── Bank Parsers ──────────────────────────────────────────────────────────


@register("008 華南銀行")
def parse_008(text: str) -> tuple[str | None, int | None]:
    """華南銀行: 表頭+數據行格式"""
    lines = text.split("\n")
    due_date = None
    amount = None

    for i, line in enumerate(lines):
        if "帳單結帳日" in line and "繳款截止日" in line and i + 1 < len(lines):
            dates = re.findall(r"\d{2,3}/\d{1,2}/\d{1,2}", lines[i + 1])
            if len(dates) >= 2:
                due_date = dates[1]
        elif "本期應繳總額" in line and "本期最低應繳金額" in line and i + 1 < len(lines):
            nums = re.findall(r"[\d,]+", lines[i + 1])
            if len(nums) >= 5:
                amount = nums[4]
            elif nums:
                amount = nums[-2] if len(nums) >= 2 else nums[0]

    return normalize_date(due_date), parse_amount(amount)


@register("009 彰化銀行")
def parse_009(text: str) -> tuple[str | None, int | None]:
    """彰化銀行: 結帳日+繳款截止日在同一行，金額在 '= XXX' 格式"""
    due_date = None
    amount = None

    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "結帳日" in line and "繳款截止日" in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                dates = re.findall(r"(\d{4}/\d{1,2}/\d{1,2})", lines[j])
                if len(dates) >= 2:
                    due_date = dates[1]
                    break
            break

    for i, line in enumerate(lines):
        if "本期應繳總額" in line:
            m2 = re.search(r"[=＝]\s*(-?[\d,]+)", line)
            if m2:
                amount = m2.group(1)
            elif i + 1 < len(lines):
                m2 = re.search(r"[=＝]\s*(-?[\d,]+)", lines[i + 1])
                if m2:
                    amount = m2.group(1)
            break

    return normalize_date(due_date), parse_amount(amount)


@register("011 上海銀行")
def parse_011(text: str) -> tuple[str | None, int | None]:
    """上海銀行: 行12=本期應繳金額, 行15=繳款截止日"""
    lines = text.split("\n")
    if len(lines) < 16:
        return None, None

    amount = line_at(lines, 12, r"(-?[\d,]+)")
    due_date = line_at(lines, 15, r"(\d{2,3}/\d{2}/\d{2})")

    return normalize_date(due_date), parse_amount(amount)


@register("012 台北富邦", extract_method="pdfminer_or_fitz_cid")
def parse_012(text: str) -> tuple[str | None, int | None]:
    """台北富邦: pdfminer 新格式 + PyMuPDF+CID 舊格式"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    due_date = None
    amount = None

    if any("本期應繳總額" in line for line in lines):
        # New format (pdfminer)
        for i, line in enumerate(lines):
            if "無需繳款" in line:
                due_date = NO_PAYMENT
            elif re.match(r"\d{2,3}/\d{2}/\d{2}$", line) and due_date is None:
                if i > 0 and re.match(r"\d{2,3}/\d{2}/\d{2}", lines[i - 1]):
                    due_date = line

            if line == "本期應繳總額":
                if i + 1 < len(lines):
                    m = re.match(r"(-?[\d,]+)元?", lines[i + 1])
                    if m:
                        amount = m.group(1)
                break
    else:
        # Old format (PyMuPDF+CID)
        date_lines = []
        for i, line in enumerate(lines[:15]):
            if re.match(r"\d{2,3}/\d{2}/\d{2}$", line):
                date_lines.append((i, line))
        if len(date_lines) >= 2:
            due_date = date_lines[1][1]
        else:
            due_date = NO_PAYMENT

        if len(lines) > 3:
            m = re.match(r"(-?[\d,]+)", lines[3])
            if m:
                amount = m.group(1)

    if due_date == NO_PAYMENT and amount is None:
        amount = "0"

    return (
        normalize_date(due_date),
        parse_amount(amount),
    )


@register("053 台中銀行")
def parse_053(text: str) -> tuple[str | None, int | None]:
    """台中銀行"""
    due_date = None
    amount = None

    m = re.search(r"繳款截止日\s+(\d{2,3}/\d{1,2}/\d{1,2})", text)
    if m:
        due_date = m.group(1)

    m = re.search(r"本期應繳總金額\s+(-?[\d,]+)", text)
    if m:
        amount = m.group(1)

    return normalize_date(due_date), parse_amount(amount)


@register("081 匯豐銀行")
def parse_081(text: str) -> tuple[str | None, int | None]:
    """匯豐銀行: 行3=繳款截止日, 行6=本期應繳金額"""
    lines = text.split("\n")
    if len(lines) < 7:
        return None, None

    due_date = line_at(lines, 3, r"(\d{4}/\d{2}/\d{2})")

    line6 = lines[6].strip()
    nums = re.findall(r"-?[\d,]+", line6)
    amount = nums[-1] if nums else None

    return normalize_date(due_date), parse_amount(amount)


@register("103 新光銀行")
def parse_103(text: str) -> tuple[str | None, int | None]:
    """新光銀行"""
    due_date = None
    amount = None

    m = re.search(r"繳\s*款\s*截\s*止\s*日\s+([\S]+)", text)
    if m:
        val = m.group(1)
        dm = re.search(r"(\d{2,3}/\d{1,2}/\d{1,2})", val)
        if dm:
            due_date = dm.group(1)
        elif NO_PAYMENT in val:
            due_date = NO_PAYMENT

    m = re.search(r"本期應繳總金額\s+([\d,]+)", text)
    if m:
        amount = m.group(1)

    return (
        normalize_date(due_date),
        parse_amount(amount),
    )


@register("803 聯邦銀行")
def parse_803(text: str) -> tuple[str | None, int | None]:
    """聯邦銀行: 行2=本期應繳金額, 行3=繳款截止日 or 無需繳款"""
    lines = text.split("\n")
    if len(lines) < 4:
        return None, None

    due_date = None
    line3 = lines[3].strip()
    if "無需繳款" in line3:
        due_date = NO_PAYMENT
    else:
        m = re.match(r"(\d{2,3}/\d{2}/\d{2})", line3)
        if m:
            due_date = m.group(1)

    amount = line_at(lines, 2, r"(-?[\d,]+)")

    return (
        normalize_date(due_date),
        parse_amount(amount),
    )


@register("805 遠東商銀", extract_method="fitz_cid")
def parse_805(text: str) -> tuple[str | None, int | None]:
    """遠東商銀 (PyMuPDF+CID): 行0=繳款截止日, 行6=本期應繳金額"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 7:
        return None, None

    due_date = None
    amount = None

    for line in lines[:3]:
        m = re.match(r"(\d{2,3}/\d{2}/\d{2})", line)
        if m:
            due_date = m.group(1)
            break

    if len(lines) > 6:
        m = re.match(r"(-?[\d,]+)", lines[6])
        if m:
            amount = m.group(1)

    if not due_date:
        due_date = NO_PAYMENT
        if amount:
            val = parse_amount(amount)
            if val is not None and val <= 0:
                amount = "0"

    return (
        normalize_date(due_date),
        parse_amount(amount),
    )


@register("807 永豐銀行")
def parse_807(text: str) -> tuple[str | None, int | None]:
    """永豐銀行"""
    due_date = None
    amount = None

    m = re.search(r"繳款截止日\s*(\d{4}/\d{1,2}/\d{1,2})", text)
    if m:
        due_date = m.group(1)

    m = re.search(r"本期應繳總金額\s*\n?\s*([\d,]+)", text)
    if not m:
        m = re.search(
            r"=\s*本期應繳總金額\s+本期最低應繳金額\s*\n\s*臺幣\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+=?\s*([\d,]+)",
            text,
        )
    if not m:
        lines = text.split("\n")
        for line in lines:
            if "臺幣" in line:
                nums = re.findall(r"[\d,]+", line)
                if len(nums) >= 6:
                    amount = nums[5]
                break
    if m:
        amount = m.group(1)

    return normalize_date(due_date), parse_amount(amount)


@register("808 玉山銀行")
def parse_808(text: str) -> tuple[str | None, int | None]:
    """玉山銀行: 行2=金額(XX元), 行4=繳款截止日 or 無需繳款"""
    lines = text.split("\n")
    if len(lines) < 5:
        return None, None

    due_date = None
    amount = None

    line2 = lines[2].strip()
    m = re.search(r"(-?[\d,]+)\s*元", line2)
    if m:
        amount = m.group(1)

    line4 = lines[4].strip()
    if "無需繳款" in line4:
        due_date = NO_PAYMENT
    else:
        m = re.match(r"(\d{2,3}/\d{2}/\d{2})", line4)
        if m:
            due_date = m.group(1)

    return (
        normalize_date(due_date),
        parse_amount(amount),
    )


@register("809 凱基銀行")
def parse_809(text: str) -> tuple[str | None, int | None]:
    """凱基銀行: 行3=繳款截止日, 行5=本期應繳金額 (CID garbled -> skip)"""
    if "cid:" in text:
        return None, None

    lines = text.split("\n")
    if len(lines) < 6:
        return None, None

    due_date = None
    line3 = lines[3].strip()
    m = re.search(r"(\d{2,3}/\d{2}/\d{2})", line3)
    if m:
        due_date = m.group(1)

    amount = line_at(lines, 5, r"(-?[\d,]+)")

    return normalize_date(due_date), parse_amount(amount)


@register("810 星展銀行")
def parse_810(text: str) -> tuple[str | None, int | None]:
    """星展銀行: 行8=繳款截止日(第2個日期), 行10=本期應繳(第4個數字)"""
    lines = text.split("\n")
    if len(lines) < 11:
        return None, None

    due_date = None
    amount = None

    line8 = lines[8].strip()
    dates = re.findall(r"(\d{4}/\d{2}/\d{2})", line8)
    if len(dates) >= 2:
        due_date = dates[1]

    line10 = lines[10].strip()
    nums = re.findall(r"-?[\d,]+", line10)
    if len(nums) >= 4:
        amount = nums[3]

    return normalize_date(due_date), parse_amount(amount)


@register("812 台新銀行")
def parse_812(text: str) -> tuple[str | None, int | None]:
    """台新銀行"""
    due_date = None
    amount = None

    m = re.search(r"繳款截止日\s+(\d{2,3}/\d{2}/\d{2})", text)
    if m:
        due_date = m.group(1)

    m = re.search(r"本期累計應繳金額\s+(-?[\d,]+)", text)
    if m:
        amount = m.group(1)

    return normalize_date(due_date), parse_amount(amount)


@register("822 中國信託")
def parse_822(text: str) -> tuple[str | None, int | None]:
    """中國信託: 行5=繳款截止日, 行7=金額 (format A); otherwise 不需繳款"""
    lines = text.split("\n")
    if len(lines) < 8:
        return None, None

    due_date = None
    amount = None

    line5 = lines[5].strip()
    line7 = lines[7].strip() if len(lines) > 7 else ""

    m = re.match(r"(\d{2,3}/\d{2}/\d{2})", line5)
    if m:
        due_date = m.group(1)
        m2 = re.match(r"(-?[\d,]+)", line7)
        if m2:
            amount = m2.group(1)
    else:
        line6 = lines[6].strip() if len(lines) > 6 else ""
        m2 = re.search(r"(-?[\d,]+)", line6)
        if m2:
            val = parse_amount(m2.group(1))
            if val is not None and val <= 0:
                amount = m2.group(1)
                due_date = NO_PAYMENT

        if due_date is None:
            due_date = NO_PAYMENT
            amount = "0"

    return (
        normalize_date(due_date),
        parse_amount(amount),
    )
