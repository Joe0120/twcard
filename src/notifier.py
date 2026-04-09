"""Payment reminders via Google Tasks API."""

import logging
from datetime import datetime

from googleapiclient.discovery import build

from .gmail_downloader import get_gmail_credentials
from .parsers import NO_PAYMENT

logger = logging.getLogger(__name__)

TASK_LIST_TITLE = "信用卡繳費"


def _get_or_create_tasklist(service) -> str:
    results = service.tasklists().list().execute()
    for tl in results.get("items", []):
        if tl["title"] == TASK_LIST_TITLE:
            return tl["id"]

    tl = service.tasklists().insert(body={"title": TASK_LIST_TITLE}).execute()
    logger.info("Created task list: %s", TASK_LIST_TITLE)
    return tl["id"]


def create_reminders(results: list[dict]) -> int:
    """Create Google Tasks for the given results. No filtering, no dedup."""
    pending = []
    for row in results:
        due_str = row.get("due_date", "")
        amount = row.get("amount", 0)

        if due_str == NO_PAYMENT or not isinstance(amount, int) or amount <= 0:
            continue

        try:
            due = datetime.strptime(due_str, "%Y/%m/%d").date()
        except (ValueError, TypeError):
            continue

        pending.append((due, row["bank"], amount))

    if not pending:
        logger.info("No payments to remind")
        return 0

    creds = get_gmail_credentials()
    service = build("tasks", "v1", credentials=creds)
    tasklist_id = _get_or_create_tasklist(service)

    # Dedup within this batch (CSV may have duplicate PDFs)
    seen: set[str] = set()
    created = 0
    for due, bank, amount in pending:
        key = f"{bank}:{due.isoformat()}"
        if key in seen:
            continue
        seen.add(key)

        title = f"{bank} ${amount:,}"
        service.tasks().insert(
            tasklist=tasklist_id,
            body={
                "title": title,
                "due": f"{due.isoformat()}T00:00:00.000Z",
            },
        ).execute()

        created += 1
        logger.info("  %s $%s (due %s)", bank, f"{amount:,}", due)

    logger.info("Created %d reminders", created)
    return created
