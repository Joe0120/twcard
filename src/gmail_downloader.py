"""Gmail PDF attachment downloader with dedup via manifest."""

import base64
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import (
    DOWNLOAD_DIR,
    DOWNLOADED_MANIFEST,
    GMAIL_LABELS,
    NUM_THREADS,
    PROJECT_ROOT,
    SCOPES,
    SKIP_PATTERNS,
    get_bank_folder,
    list_pdfs,
)

logger = logging.getLogger(__name__)

_file_lock = threading.Lock()
_manifest_lock = threading.Lock()


# ── Manifest (dedup) ──────────────────────────────────────────────────────

def _load_manifest() -> dict:
    try:
        with open(DOWNLOADED_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ── Gmail auth ────────────────────────────────────────────────────────────

def get_gmail_credentials() -> Credentials:
    """Get or refresh Gmail OAuth2 credentials."""
    creds = None
    token_path = PROJECT_ROOT / "token.json"
    creds_path = PROJECT_ROOT / "credentials.json"

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        import os as _os
        fd = _os.open(str(token_path), _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
        with _os.fdopen(fd, "w") as f:
            f.write(creds.to_json())

    return creds


def _get_label_id(service, label_name: str) -> str | None:
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    return None


# ── Single message processing ─────────────────────────────────────────────

def _process_message(
    creds: Credentials,
    msg_id: str,
    manifest: dict,
    counter: list[int],
) -> list[str]:
    """Download PDF attachments from one message. Returns list of saved paths."""
    service = build("gmail", "v1", credentials=creds)
    message = service.users().messages().get(userId="me", id=msg_id).execute()

    subject = ""
    sender = ""
    for header in message["payload"].get("headers", []):
        if header["name"] == "Subject":
            subject = header["value"]
        elif header["name"] == "From":
            sender = header["value"]

    bank_folder = get_bank_folder(sender)
    saved_files: list[str] = []

    parts = message["payload"].get("parts", [])
    for part in parts:
        filename = part.get("filename", "")
        if not filename.lower().endswith(".pdf"):
            continue

        fname_lower = filename.lower()
        if any(p in fname_lower for p in SKIP_PATTERNS):
            continue

        att_id = part["body"].get("attachmentId")
        if not att_id:
            continue

        att = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=msg_id, id=att_id)
            .execute()
        )
        data = base64.urlsafe_b64decode(att["data"])

        with _file_lock:
            bank_dir = DOWNLOAD_DIR / bank_folder
            bank_dir.mkdir(parents=True, exist_ok=True)

            filepath = bank_dir / filename
            c = 1
            while filepath.exists():
                stem = Path(filename).stem
                suffix = Path(filename).suffix
                filepath = bank_dir / f"{stem}_{c}{suffix}"
                c += 1

            filepath.write_bytes(data)
            saved_files.append(str(filepath.relative_to(DOWNLOAD_DIR)))

            counter[0] += 1
            count = counter[0]

        logger.info(
            "  [%d] [%s] %s -> %s",
            count, bank_folder, subject, filepath.name,
        )

    # Update manifest atomically
    with _manifest_lock:
        manifest[msg_id] = {
            "files": saved_files,
            "date": datetime.now().isoformat(),
        }
        tmp = DOWNLOADED_MANIFEST.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        tmp.replace(DOWNLOADED_MANIFEST)

    return saved_files


# ── Main download entry point ─────────────────────────────────────────────

def download_pdfs(labels: list[str] | None = None) -> int:
    """Download PDF attachments from Gmail labels. Returns count of new PDFs."""
    labels = labels or GMAIL_LABELS
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()

    creds = get_gmail_credentials()
    service = build("gmail", "v1", credentials=creds)

    # Collect messages from all labels
    messages: list[dict] = []
    seen_ids: set[str] = set()

    for label_name in labels:
        label_id = _get_label_id(service, label_name)
        if not label_id:
            logger.error("Label not found: %s", label_name)
            continue

        page_token = None
        while True:
            results = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=[label_id],
                    q="has:attachment filename:pdf",
                    maxResults=500,
                    pageToken=page_token,
                )
                .execute()
            )
            for msg in results.get("messages", []):
                if msg["id"] not in seen_ids:
                    messages.append(msg)
                    seen_ids.add(msg["id"])
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        logger.info("Label '%s': %d messages", label_name, len(messages))

    if not messages:
        logger.info("No messages found")
        return 0

    # Filter out already-downloaded message IDs
    new_msgs = [m for m in messages if m["id"] not in manifest]
    logger.info(
        "Found %d messages total (%d new)",
        len(messages), len(new_msgs),
    )

    if not new_msgs:
        logger.info("All messages already downloaded")
        return 0

    counter = [0]  # mutable counter for threads
    msg_ids = [m["id"] for m in new_msgs]

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = {
            executor.submit(_process_message, creds, mid, manifest, counter): mid
            for mid in msg_ids
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("Error processing message %s", futures[future])

    # Summary
    total = counter[0]
    logger.info("Done! Downloaded %d new PDFs", total)
    if DOWNLOAD_DIR.exists():
        for folder in sorted(DOWNLOAD_DIR.iterdir()):
            if folder.is_dir():
                count = len(list_pdfs(folder))
                logger.info("  %s: %d PDFs", folder.name, count)

    return total
