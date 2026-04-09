"""Constants, bank rules, and environment loading."""

import json
import logging
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional on Linux server

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = PROJECT_ROOT / "pdfs"
DATA_DIR = PROJECT_ROOT / "data"
PASSWORDS_FILE = PROJECT_ROOT / "passwords.json"
DOWNLOADED_MANIFEST = DATA_DIR / "downloaded.json"
OUTPUT_CSV = PROJECT_ROOT / "statements.csv"

# ── Gmail ──────────────────────────────────────────────────────────────────
import os as _os
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/tasks",
]
# 多個標籤用逗號分隔，例如: "銀行/信用卡帳單,銀行/對帳單"
GMAIL_LABELS = [
    s.strip() for s in
    _os.environ.get("GMAIL_LABELS", "銀行/信用卡帳單").split(",")
    if s.strip()
]
NUM_THREADS = 10

# ── Bank email keyword -> folder mapping (order matters) ──────────────────
BANK_RULES = [
    ("tcb-bank.com", "006 合作金庫"),
    ("hncb.com.tw", "008 華南銀行"),
    ("chb", "009 彰化銀行"),
    ("scsb.com.tw", "011 上海銀行"),
    ("fubon", "012 台北富邦"),
    ("taipeifubon", "012 台北富邦"),
    ("cathaybk", "013 國泰世華"),
    ("cathaysec.com.tw", "013 國泰世華"),
    ("megabank.com.tw", "017 兆豐銀行"),
    ("citibank", "021 花旗銀行"),
    ("tcbbank.com", "053 台中銀行"),
    ("hsbc.com", "081 匯豐銀行"),
    ("skis", "103 新光銀行"),
    ("skbank", "103 新光銀行"),
    ("ebill.sk88.com.tw", "103 新光銀行"),
    ("post.gov", "700 中華郵政"),
    ("ubot", "803 聯邦銀行"),
    ("yesing", "803 聯邦銀行"),
    ("feib", "805 遠東商銀"),
    ("yuantabank", "806 元大銀行"),
    ("sinopac", "807 永豐銀行"),
    ("banksinopac", "807 永豐銀行"),
    ("esunbank", "808 玉山銀行"),
    ("kgibank", "809 凱基銀行"),
    ("kgi", "809 凱基銀行"),
    ("dbs", "810 星展銀行"),
    ("taishinbank", "812 台新銀行"),
    ("richart.tw", "812 台新銀行"),
    ("ctbcbank", "822 中國信託"),
    ("sk.ctbcbank.com", "822 中國信託"),
    ("nextbank.com.tw", "823 將來銀行"),
    ("linebank", "824 連線銀行"),
    ("rakuten-bank.com.tw", "826 樂天銀行"),
]

# ── Skip patterns for payment slips ───────────────────────────────────────
SKIP_PATTERNS = ["paymentslip", "payslip", "payment slip", "繳款聯"]


def load_passwords() -> dict[str, list[str]]:
    """Load bank -> passwords mapping from passwords.json."""
    try:
        with open(PASSWORDS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {bank: info["passwords"] for bank, info in data.items()}
    except FileNotFoundError:
        logger.warning("passwords.json not found at %s", PASSWORDS_FILE)
        return {}


def list_pdfs(directory: Path) -> list[Path]:
    """List all PDF files in a directory (case-insensitive)."""
    return sorted(p for p in directory.iterdir() if p.suffix.lower() == ".pdf")


def get_bank_folder(sender: str) -> str:
    """Determine bank folder from sender email address."""
    from email.utils import parseaddr
    _, addr = parseaddr(sender)
    email = (addr or sender).lower()
    for keyword, folder in BANK_RULES:
        if keyword in email:
            return folder
    return "其他"
