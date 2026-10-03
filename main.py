"""
fetch_gmail.py — Fetch today's emails from Gmail using the Gmail API (OAuth2)

SETUP (one-time):
1. Go to https://console.cloud.google.com/
2. Create a project → Enable the Gmail API
3. APIs & Services > Credentials > Create OAuth 2.0 Client ID  (Desktop app)
4. Download the JSON and save it as `credentials.json` next to this script
5. pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client pandas pyarrow

On first run a browser window opens for login; `token.json` is saved for reuse.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd
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
CREDENTIALS_FILE = "cred.json"
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


def get_credentials() -> Credentials:
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

        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)  # type: ignore

    if not creds:
        raise ValueError("No credentials obtained.")

    with open(TOKEN_FILE, "w") as fh:
        fh.write(creds.to_json())
    log.info("Credentials saved to %s", TOKEN_FILE)

    return creds


# ── Gmail helpers ─────────────────────────────────────────────────────────────


def _decode_body(payload: dict, mime: str = "text/plain") -> str:
    """Recursively extract body text from a message payload."""
    if payload.get("mimeType") == mime:
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _decode_body(part, mime)
        if text:
            return text

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
    return None


# ── Fetch ─────────────────────────────────────────────────────────────────────


def fetch_emails_for_day(
    service,
    target_date: date,
    label: str = "INBOX",
    unread_only: bool = False,
    batch_size: int = 50,
) -> list[Email]:
    """
    Fetch ALL emails from a single calendar day using Gmail's after:/before: filters.
    Automatically paginates until no more results are returned.
    """
    # Gmail's after:/before: use YYYY/MM/DD and are inclusive/exclusive respectively
    after = target_date.strftime("%Y/%m/%d")
    # "before" the next day ensures we only get the target day
    before = date(target_date.year, target_date.month, target_date.day)
    # build next day safely
    import datetime as dt

    next_day = (dt.datetime.combine(before, dt.time.min) + dt.timedelta(days=1)).date()
    before_str = next_day.strftime("%Y/%m/%d")

    query_parts = [f"after:{after}", f"before:{before_str}"]
    if unread_only:
        query_parts.append("is:unread")
    query = " ".join(query_parts)

    log.info("Gmail query: %r  |  label: %s", query, label)

    all_emails: list[Email] = []
    page_token: Optional[str] = None
    page = 0

    while True:
        page += 1
        list_kwargs: dict = dict(
            userId="me",
            labelIds=[label],
            q=query,
            maxResults=batch_size,
        )
        if page_token:
            list_kwargs["pageToken"] = page_token

        results = _api_call_with_retry(service.users().messages().list(**list_kwargs))
        if results is None:
            break

        messages = results.get("messages", [])
        page_token = results.get("nextPageToken")

        log.info("Page %d — %d message(s) listed.", page, len(messages))

        batch_results: dict[str, dict] = {}
        failed_ids: list[str] = []

        def _batch_callback(request_id, response, exception):
            if exception:
                status = getattr(getattr(exception, "resp", None), "status", None)
                if status == 429:
                    failed_ids.append(request_id)
                else:
                    log.error("Skipping message %s — %s", request_id, exception)
            elif response:
                batch_results[request_id] = response

        # Gmail batch limit is 100 per request; use smaller chunks to avoid 429s
        chunk_size = 20
        for i in range(0, len(messages), chunk_size):
            chunk = messages[i : i + chunk_size]
            batch = service.new_batch_http_request(callback=_batch_callback)
            for m in chunk:
                batch.add(
                    service.users().messages().get(userId="me", id=m["id"], format="full"),
                    request_id=m["id"],
                )
            batch.execute()
            if i + chunk_size < len(messages):
                time.sleep(0.5)

        # Retry 429-failed messages sequentially with backoff
        if failed_ids:
            log.warning("%d message(s) rate-limited — retrying sequentially…", len(failed_ids))
            time.sleep(2)
            for msg_id in failed_ids:
                try:
                    msg = _api_call_with_retry(
                        service.users().messages().get(userId="me", id=msg_id, format="full")
                    )
                    if msg:
                        batch_results[msg_id] = msg
                except HttpError as exc:
                    log.error("Skipping message %s — %s", msg_id, exc)

        for msg in batch_results.values():
            headers = _parse_headers(msg["payload"].get("headers", []))
            all_emails.append(
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

        if not page_token:
            break

    return all_emails


# ── Persistence ───────────────────────────────────────────────────────────────


def load_json_store(path: str) -> dict[str, Email]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        records: list[dict] = json.load(fh)
    return {r["id"]: Email.from_dict(r) for r in records}


def save_json_store(emails_by_id: dict[str, Email], path: str) -> None:
    records = [e.to_dict() for e in emails_by_id.values()]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    log.info("Saved %d email(s) to %s", len(records), path)


def upsert_emails(new_emails: list[Email], path: str) -> int:
    """Merge new emails into the JSON store; return count of truly new ones."""
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
    target_date: Optional[date] = None,
    label: str = "INBOX",
    unread_only: bool = False,
    output_file: str = "mails.json",
    output_parquet: str = "emails.parquet",
) -> None:
    if target_date is None:
        target_date = date.today()

    log.info("Authenticating with Gmail…")
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    log.info(
        "Fetching emails for %s from [%s]%s…",
        target_date,
        label,
        " (unread only)" if unread_only else "",
    )

    emails = fetch_emails_for_day(
        service,
        target_date=target_date,
        label=label,
        unread_only=unread_only,
    )

    log.info("Total fetched: %d email(s).", len(emails))

    if not emails:
        log.info("No emails found for %s.", target_date)
        return

    # ── Save / merge into JSON store ──────────────────────────────────────────
    added = upsert_emails(emails, path=output_file)
    log.info("%d new email(s) merged into %s (duplicates skipped).", added, output_file)

    # ── Save to parquet (merge with existing) ────────────────────────────────
    new_frame = pd.DataFrame([e.to_dict() for e in emails])
    if os.path.exists(output_parquet):
        existing = pd.read_parquet(output_parquet)
        frame = pd.concat([existing, new_frame]).drop_duplicates(subset="id").reset_index(drop=True)
    else:
        frame = new_frame
    frame.to_parquet(output_parquet, index=False)
    log.info("Parquet saved → %s  (%d rows total)", output_parquet, len(frame))

    # ── Pretty-print summary ──────────────────────────────────────────────────
    print(new_frame[["date", "from", "subject", "snippet"]].to_string(index=False))


if __name__ == "__main__":
    import argparse
    import datetime as dt

    parser = argparse.ArgumentParser(
        description="Fetch one day of Gmail to local files."
    )
    parser.add_argument(
        "--date",
        type=lambda s: dt.date.fromisoformat(s),
        default=None,
        help="Date to fetch in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument("--label", default="INBOX", help="Gmail label (default: INBOX)")
    parser.add_argument(
        "--unread", action="store_true", help="Fetch unread emails only"
    )
    parser.add_argument("--output", default="mails.json", help="JSON output file")
    parser.add_argument(
        "--parquet", default="emails.parquet", help="Parquet output file"
    )
    args = parser.parse_args()

    main(
        target_date=args.date,
        label=args.label,
        unread_only=args.unread,
        output_file=args.output,
        output_parquet=args.parquet,
    )
