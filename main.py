"""
fetch_gmail.py — Fetch emails from Gmail using the Gmail API (OAuth2)

SETUP (one-time):
1. Go to https://console.cloud.google.com/
2. Create a project → Enable the Gmail API
3. APIs & Services > Credentials > Create OAuth 2.0 Client ID  (Desktop app)
4. Download the JSON and save it as `credentials.json` next to this script
5. pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

On first run a browser window opens for login; `token.json` is saved for reuse.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

_HEADER_KEYS = {"From", "To", "Subject", "Date"}

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class Email:
    id: str
    from_: str
    to: str
    subject: str
    date: str
    snippet: str
    body: str
    labels: list[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from": self.from_,
            "to": self.to,
            "subject": self.subject,
            "date": self.date,
            "snippet": self.snippet,
            "body": self.body,
            "labels": self.labels,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Email":
        return cls(
            id=d["id"],
            from_=d.get("from", ""),
            to=d.get("to", ""),
            subject=d.get("subject", "(no subject)"),
            date=d.get("date", ""),
            snippet=d.get("snippet", ""),
            body=d.get("body", ""),
            labels=d.get("labels", []),
        )


# ── Auth ──────────────────────────────────────────────────────────────────────


def get_credentials() -> Optional[Credentials]:
    """Load, refresh, or obtain fresh OAuth2 credentials."""
    creds: Credentials | None = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.info("Refreshing expired credentials…")
        creds.refresh(Request())
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(
                f"'{CREDENTIALS_FILE}' not found.\n"
                "Download it from: Google Cloud Console → APIs & Services\n"
                "  → Credentials → OAuth 2.0 Client IDs → Download JSON\n"
                f"Save it as '{CREDENTIALS_FILE}' next to this script."
            )
        return None

    with open(TOKEN_FILE, "w") as fh:
        fh.write(creds.to_json())
    log.info("Credentials saved to %s", TOKEN_FILE)

    return creds


# ── Gmail helpers ─────────────────────────────────────────────────────────────


def _decode_body(payload: dict, mime: str = "text/plain") -> str:
    """
    Recursively extract body text from a message payload.
    Tries `mime` first (default text/plain); falls back to text/html.
    """
    if payload.get("mimeType") == mime:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _decode_body(part, mime)
        if text:
            return text

    # If plain-text not found, try HTML as fallback
    if mime == "text/plain":
        return _decode_body(payload, "text/html")

    return "(no body)"


def _parse_headers(headers: list[dict]) -> dict[str, str]:
    return {h["name"]: h["value"] for h in headers if h["name"] in _HEADER_KEYS}


def _api_call_with_retry(call, retries: int = 3, backoff: float = 2.0):
    """Execute a Google API callable with exponential-backoff retry on 429/5xx."""
    for attempt in range(retries):
        try:
            return call.execute()
        except HttpError as exc:
            status = exc.resp.status
            if status in (429, 500, 503) and attempt < retries - 1:
                wait = backoff**attempt
                log.warning("HTTP %s — retrying in %.1fs…", status, wait)
                time.sleep(wait)
            else:
                raise
    return None  # unreachable


# ── Fetch ─────────────────────────────────────────────────────────────────────


def fetch_emails(
    service,
    max_results: int = 10,
    label: str = "INBOX",
    unread_only: bool = False,
    page_token: Optional[str] = None,
) -> tuple[list[Email], Optional[str]]:
    """
    Fetch emails and return (emails, next_page_token).

    Pass the returned `next_page_token` back in to paginate.
    """
    query = "is:unread" if unread_only else ""
    list_kwargs: dict = dict(
        userId="me", labelIds=[label], q=query, maxResults=max_results
    )
    if page_token:
        list_kwargs["pageToken"] = page_token

    results = _api_call_with_retry(service.users().messages().list(**list_kwargs))
    if results is None:
        return [], None

    messages = results.get("messages", [])
    next_token: Optional[str] = results.get("nextPageToken")

    if not messages:
        log.info("No emails found.")
        return [], None

    emails: list[Email] = []
    for msg_ref in messages:
        try:
            msg = _api_call_with_retry(
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="full")
            )
        except HttpError as exc:
            log.error("Skipping message %s — %s", msg_ref["id"], exc)
            continue
        if msg is None:
            log.error("Skipping message %s — not found", msg_ref["id"])
            continue
        headers = _parse_headers(msg["payload"].get("headers", []))
        emails.append(
            Email(
                id=msg["id"],
                from_=headers.get("From", "Unknown"),
                to=headers.get("To", ""),
                subject=headers.get("Subject", "(no subject)"),
                date=headers.get("Date", ""),
                snippet=msg.get("snippet", ""),
                body=_decode_body(msg["payload"]),
                labels=msg.get("labelIds", []),
            )
        )

    return emails, next_token


# ── Persistence ───────────────────────────────────────────────────────────────


def load_json_store(path: str) -> dict[str, Email]:
    """Load saved emails from JSON, keyed by id (for deduplication)."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        records: list[dict] = json.load(fh)
    return {r["id"]: Email.from_dict(r) for r in records}


def save_json_store(emails_by_id: dict[str, Email], path: str) -> None:
    """Persist the email store to JSON."""
    records = [e.to_dict() for e in emails_by_id.values()]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    log.info("Saved %d email(s) to %s", len(records), path)


def upsert_emails(new_emails: list[Email], path: str = "mails.json") -> int:
    """
    Merge new emails into the JSON store (no duplicates by id).
    Returns the number of *new* emails actually added.
    """
    store = load_json_store(path)
    before = len(store)
    for email in new_emails:
        store[email.id] = email
    save_json_store(store, path)
    return len(store) - before


# ── Display ───────────────────────────────────────────────────────────────────

_SEP = "─" * 62


def print_email(email: Email, index: int, body_chars: int = 500) -> None:
    print(f"\n{_SEP}")
    print(f"  #{index + 1}  {email.subject}")
    print(_SEP)
    print(f"  From   : {email.from_}")
    print(f"  To     : {email.to}")
    print(f"  Date   : {email.date}")
    print(f"  Labels : {', '.join(email.labels)}")
    print(f"\n  Preview: {email.snippet[:120]}")
    print("\n  Body:\n")
    body_preview = email.body[:body_chars].strip()
    for line in body_preview.splitlines():
        print(f"    {line}")
    remaining = len(email.body) - body_chars
    if remaining > 0:
        print(f"\n    … [{remaining} more characters]")


# ── Main ──────────────────────────────────────────────────────────────────────


def main(
    max_emails: int = 10,
    label: str = "INBOX",
    unread_only: bool = False,
    output_file: str = "mails.json",
) -> None:
    log.info("Authenticating with Gmail…")
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    label_desc = f"[{label}]" + (" (unread)" if unread_only else "")
    log.info("Fetching up to %d email(s) from %s…", max_emails, label_desc)

    emails, next_page_token = fetch_emails(
        service,
        max_results=max_emails,
        label=label,
        unread_only=unread_only,
    )

    if not emails:
        return

    log.info("Fetched %d email(s).", len(emails))
    if next_page_token:
        log.info(
            "More emails available — pass page_token to fetch_emails() to paginate."
        )

    for i, email in enumerate(emails):
        print_email(email, i)

    added = upsert_emails(emails, path=output_file)
    log.info("%d new email(s) written to %s (duplicates skipped).", added, output_file)


if __name__ == "__main__":
    main()
