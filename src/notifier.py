"""Payment reminders for upcoming credit card due dates.

On macOS: uses AppleScript to create native Reminders.
On Linux: uses CalDAV to create iCloud Reminders.
"""

import logging
import os
import platform
import subprocess
from datetime import date, datetime

from .parsers import NO_PAYMENT

logger = logging.getLogger(__name__)

REMINDER_LIST = "信用卡繳費"


def _sanitize(text: str) -> str:
    """Remove characters that could cause injection in AppleScript or iCalendar."""
    return text.replace('"', "").replace("\\", "").replace("\r", "").replace("\n", "")


# ── Shared logic ─────────────────────────────────────────────────────────

def _collect_pending(results: list[dict]) -> list[tuple[date, str, int]]:
    """Filter unpaid statements with future due dates, sorted nearest first."""
    today = date.today()
    pending: list[tuple[date, str, int]] = []

    for row in results:
        due_str = row.get("due_date", "")
        amount = row.get("amount", 0)

        if due_str == NO_PAYMENT or not isinstance(amount, int) or amount <= 0:
            continue

        try:
            due = datetime.strptime(due_str, "%Y/%m/%d").date()
        except (ValueError, TypeError):
            continue

        if due < today:
            continue

        pending.append((due, row["bank"], amount))

    pending.sort(key=lambda x: x[0])
    return pending


# ── macOS: AppleScript ───────────────────────────────────────────────────

def _applescript_create(pending: list[tuple[date, str, int]]) -> int:
    """Create reminders via AppleScript on macOS."""
    safe_list = _sanitize(REMINDER_LIST)

    # Ensure list exists
    result = subprocess.run(
        ["osascript", "-e", f'''
        tell application "Reminders"
            if not (exists list "{safe_list}") then
                make new list with properties {{name:"{safe_list}"}}
            end if
        end tell
        '''],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("Cannot create/verify reminder list: %s", result.stderr.strip())
        return 0

    # Clear all reminders in list
    subprocess.run(
        ["osascript", "-e", f'''
        tell application "Reminders"
            tell list "{safe_list}"
                delete every reminder
            end tell
        end tell
        '''],
        capture_output=True, text=True,
    )

    # Build all reminders in a single AppleScript call
    if not pending:
        return 0

    make_lines = []
    for due, bank, amount in pending:
        title = _sanitize(f"{bank} ${amount:,}")
        date_str = due.strftime("%Y/%m/%d")
        make_lines.append(
            f'make new reminder with properties {{name:"{title}", due date:date "{date_str}"}}'
        )

    script = f'''
    tell application "Reminders"
        tell list "{safe_list}"
            {chr(10).join(make_lines)}
        end tell
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        for due, bank, amount in pending:
            logger.info("  %s $%s (due %s)", bank, f"{amount:,}", due)
        return len(pending)

    logger.error("Failed to create reminders: %s", result.stderr.strip())
    return 0


# ── Linux: CalDAV ────────────────────────────────────────────────────────

def _caldav_create(pending: list[tuple[date, str, int]]) -> int:
    """Create reminders via CalDAV (iCloud)."""
    try:
        import caldav
    except ImportError:
        logger.error("caldav package not installed, run: pip install caldav")
        return 0

    apple_id = os.environ.get("APPLE_ID")
    apple_pw = os.environ.get("APPLE_APP_PASSWORD")
    if not apple_id or not apple_pw:
        logger.warning("APPLE_ID or APPLE_APP_PASSWORD not set, skipping reminders")
        return 0

    try:
        client = caldav.DAVClient(
            url="https://caldav.icloud.com/",
            username=apple_id,
            password=apple_pw,
        )
        principal = client.principal()
    except Exception:
        logger.exception("CalDAV authentication failed")
        return 0

    # Find a list that accepts VTODO
    target = None
    for cal in principal.calendars():
        try:
            probe = (
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
                "BEGIN:VTODO\r\nSUMMARY:__probe__\r\n"
                "STATUS:NEEDS-ACTION\r\nEND:VTODO\r\nEND:VCALENDAR"
            )
            obj = cal.save_event(probe)
            obj.delete()
            target = cal
            break
        except Exception:
            continue

    if not target:
        logger.error("No writable reminder list found on iCloud")
        return 0

    logger.info("Using CalDAV list: %s", target.get_display_name())

    # Clear only VTODO items (avoid deleting calendar events)
    try:
        for obj in target.objects():
            try:
                if obj.data and "VTODO" in obj.data:
                    obj.delete()
            except Exception:
                continue
    except Exception:
        pass

    # Create reminders
    created = 0
    for due, bank, amount in pending:
        title = _sanitize(f"{bank} ${amount:,}")
        vtodo = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VTODO\r\n"
            f"SUMMARY:{title}\r\n"
            f"DUE;VALUE=DATE:{due.strftime('%Y%m%d')}\r\n"
            "STATUS:NEEDS-ACTION\r\n"
            "END:VTODO\r\n"
            "END:VCALENDAR"
        )
        try:
            target.save_event(vtodo)
            created += 1
            logger.info("  %s (due %s)", title, due)
        except Exception:
            logger.exception("  Failed: %s", title)

    return created


# ── Public API ───────────────────────────────────────────────────────────

def create_reminders(results: list[dict]) -> int:
    """Create payment reminders. Uses AppleScript on macOS, CalDAV on Linux."""
    pending = _collect_pending(results)

    if not pending:
        logger.info("No upcoming payments to remind")
        return 0

    logger.info("Found %d upcoming payments", len(pending))

    if platform.system() == "Darwin":
        created = _applescript_create(pending)
    else:
        created = _caldav_create(pending)

    logger.info("Created %d reminders", created)
    return created
