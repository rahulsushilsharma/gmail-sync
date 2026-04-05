# import email
# import imaplib
# from dotenv import load_dotenv


"""
fetch_gmail.py — Fetch emails from Gmail using the Gmail API (OAuth2)

SETUP (one-time):
1. Go to https://console.cloud.google.com/
2. Create a project → Enable the Gmail API
3. Go to APIs & Services > Credentials > Create OAuth 2.0 Client ID
   - Application type: Desktop app
4. Download the credentials JSON and save it as `credentials.json`
   in the same directory as this script
5. Install dependencies:
   pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

On first run, a browser window will open asking you to log in and grant access.
A `token.json` file will be saved for future runs.
"""

import base64
import json
import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Configuration ─────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = "credentials.json"  # Downloaded from Google Cloud Console
TOKEN_FILE = "token.json"  # Auto-generated on first run

MAX_EMAILS = 10  # Number of emails to fetch
LABEL = "INBOX"  # Label/folder: INBOX, SENT, SPAM, TRASH, etc.
UNREAD_ONLY = False  # Set True to fetch only unread emails


# ── Auth ──────────────────────────────────────────────────────────────────────


def get_credentials():
    """Load or refresh OAuth2 credentials, prompting login if needed."""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"'{CREDENTIALS_FILE}' not found.\n"
                    "Download it from Google Cloud Console:\n"
                    "  APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON\n"
                    f"Then save it as '{CREDENTIALS_FILE}' next to this script."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return creds


# ── Gmail helpers ─────────────────────────────────────────────────────────────


def decode_body(payload):
    """Recursively extract plain-text body from a message payload."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Multipart: recurse into parts
    for part in payload.get("parts", []):
        text = decode_body(part)
        if text:
            return text

    return "(no plain-text body)"


def parse_headers(headers):
    """Return a dict of selected headers from the message."""
    keys = {"From", "To", "Subject", "Date"}
    return {h["name"]: h["value"] for h in headers if h["name"] in keys}


def fetch_emails(service, max_results=MAX_EMAILS, label=LABEL, unread_only=UNREAD_ONLY):
    """Fetch and return a list of parsed email dicts."""
    query = "is:unread" if unread_only else ""
    results = (
        service.users()
        .messages()
        .list(userId="me", labelIds=[label], q=query, maxResults=max_results)
        .execute()
    )

    messages = results.get("messages", [])
    if not messages:
        print("No emails found.")
        return []

    emails = []
    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )

        headers = parse_headers(msg["payload"].get("headers", []))
        body = decode_body(msg["payload"])
        snippet = msg.get("snippet", "")

        emails.append(
            {
                "id": msg["id"],
                "from": headers.get("From", "Unknown"),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": snippet,
                "body": body,
                "labels": msg.get("labelIds", []),
            }
        )

    return emails


# ── Display ───────────────────────────────────────────────────────────────────


def print_email(email, index):
    """Pretty-print a single email."""
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  #{index + 1}  |  {email['subject']}")
    print(sep)
    print(f"  From   : {email['from']}")
    print(f"  To     : {email['to']}")
    print(f"  Date   : {email['date']}")
    print(f"  Labels : {', '.join(email['labels'])}")
    print(f"\n  Preview: {email['snippet'][:120]}...")
    print(f"\n  Body:\n")
    # Print first 500 chars of body to keep output readable
    body_preview = email["body"][:500].strip()
    for line in body_preview.splitlines():
        print(f"    {line}")
    if len(email["body"]) > 500:
        print(f"\n    ... [{len(email['body']) - 500} more characters]")


# ── Save to file ──────────────────────────────────────────────────────────────


def save_to_json(emails, filename=None):
    """Save fetched emails to a JSON file."""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"emails_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(emails, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Emails saved to: {filename}")
    return filename


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print("Authenticating with Gmail...")
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    print(
        f"Fetching up to {MAX_EMAILS} emails from [{LABEL}]"
        + (" (unread only)..." if UNREAD_ONLY else "...")
    )

    emails = fetch_emails(service)

    if not emails:
        return

    print(f"\nFetched {len(emails)} email(s).\n")

    for i, email in enumerate(emails):
        print_email(email, i)

    # Optionally save to JSON
    save = input("\nSave emails to JSON? (y/n): ").strip().lower()
    if save == "y":
        save_to_json(emails)


if __name__ == "__main__":
    main()
