"""Payment reminders via Google Tasks API."""

import logging
import re
from datetime import date, datetime

from googleapiclient.discovery import build

from .gmail_downloader import get_gmail_credentials
from .parsers import NO_PAYMENT

logger = logging.getLogger(__name__)

TASK_LIST_TITLE = "信用卡繳費"


def _sanitize(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")


def _get_bank_code(bank: str) -> str:
    """Extract leading bank code, e.g. '808' from '808 玉山銀行'."""
    m = re.match(r"(\d+)", bank)
    return m.group(1) if m else bank


def _task_key(bank: str, due: date) -> str:
    return f"{_get_bank_code(bank)}:{due.isoformat()}"


def _latest_per_bank(results: list[dict]) -> list[tuple[date, str, int]]:
    """For each bank, keep only the latest statement (by due_date)."""
    latest: dict[str, tuple[date, str, int]] = {}

    for row in results:
        due_str = row.get("due_date", "")
        amount = row.get("amount", 0)
        bank = row.get("bank", "")

        if due_str == NO_PAYMENT or not isinstance(amount, int) or amount <= 0:
            continue

        try:
            due = datetime.strptime(due_str, "%Y/%m/%d").date()
        except (ValueError, TypeError):
            continue

        code = _get_bank_code(bank)
        if code not in latest or due > latest[code][0]:
            latest[code] = (due, bank, amount)

    return sorted(latest.values(), key=lambda x: x[0])


def _get_or_create_tasklist(service) -> str:
    results = service.tasklists().list().execute()
    for tl in results.get("items", []):
        if tl["title"] == TASK_LIST_TITLE:
            return tl["id"]

    tl = service.tasklists().insert(body={"title": TASK_LIST_TITLE}).execute()
    logger.info("Created task list: %s", TASK_LIST_TITLE)
    return tl["id"]


def _get_existing_keys(service, tasklist_id: str) -> set[str]:
    keys: set[str] = set()
    page_token = None

    while True:
        resp = service.tasks().list(
            tasklist=tasklist_id,
            showCompleted=True,
            showHidden=True,
            pageToken=page_token,
        ).execute()

        for task in resp.get("items", []):
            title = task.get("title", "")
            due_raw = task.get("due", "")

            code_match = re.match(r"(\d+)", title)
            if not code_match or not due_raw:
                continue

            try:
                due = datetime.fromisoformat(due_raw.replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                continue

            keys.add(f"{code_match.group(1)}:{due.isoformat()}")

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return keys


def create_reminders(results: list[dict]) -> int:
    """Create Google Tasks for latest statement per bank. Skip existing."""
    pending = _latest_per_bank(results)

    if not pending:
        logger.info("No payments to remind")
        return 0

    logger.info("Found %d banks with pending payments", len(pending))

    creds = get_gmail_credentials()
    service = build("tasks", "v1", credentials=creds)

    tasklist_id = _get_or_create_tasklist(service)
    existing = _get_existing_keys(service, tasklist_id)

    created = 0
    for due, bank, amount in pending:
        key = _task_key(bank, due)
        if key in existing:
            continue

        title = _sanitize(f"{bank} ${amount:,}")
        service.tasks().insert(
            tasklist=tasklist_id,
            body={
                "title": title,
                "due": f"{due.isoformat()}T00:00:00.000Z",
            },
        ).execute()

        created += 1
        logger.info("  %s $%s (due %s)", bank, f"{amount:,}", due)

    logger.info("Created %d new reminders (skipped %d existing)",
                created, len(pending) - created)
    return created
