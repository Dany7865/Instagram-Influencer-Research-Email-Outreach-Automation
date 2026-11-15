"""Gmail integration (official Gmail API, OAuth) plus an offline mock mailbox for testing.

Interface used by the pipeline (identical for both implementations):
    send(to, subject, body, thread_id=None, in_reply_to=None) -> {"id", "thread_id", "message_id"}
    new_replies(thread_id, after_id="") -> [{"id", "from", "date", "body"}, ...]  (oldest first)
    create_draft(to, subject, body, thread_id) -> draft_id
    delete_draft(draft_id)
"""
import base64
import json
import re
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

from . import config as cfg

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]  # send + read + drafts


def build_message(to, subject, body, thread_in_reply_to=None, sender=None) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = sender or f"{cfg.SENDER_NAME} <{cfg.SENDER_EMAIL}>"
    msg["Subject"] = subject
    # one-click style opt-out header (mailto form), improves deliverability / compliance
    msg["List-Unsubscribe"] = f"<mailto:{cfg.SENDER_EMAIL}?subject=unsubscribe>"
    if thread_in_reply_to:
        msg["In-Reply-To"] = thread_in_reply_to
        msg["References"] = thread_in_reply_to
    msg.set_content(body)
    return msg


def strip_quoted(text: str) -> str:
    """Drop the quoted history from a reply so Claude only sees what the person just wrote."""
    out = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .+ wrote:\s*$", line.strip()):
            break
        out.append(line)
    return "\n".join(out).strip()


# ============================================================ REAL GMAIL
class GmailClient:
    def __init__(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if cfg.GMAIL_TOKEN.exists():
            creds = Credentials.from_authorized_user_file(str(cfg.GMAIL_TOKEN), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(cfg.GMAIL_CREDENTIALS), SCOPES)
                creds = flow.run_local_server(port=0)
            cfg.GMAIL_TOKEN.write_text(creds.to_json())
        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.me = self.svc.users().getProfile(userId="me").execute()["emailAddress"].lower()

    def _raw(self, msg, thread_id=None):
        body = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if thread_id:
            body["threadId"] = thread_id
        return body

    def send(self, to, subject, body, thread_id=None, in_reply_to=None):
        msg = build_message(to, subject, body, in_reply_to, sender=self.me)
        sent = self.svc.users().messages().send(userId="me", body=self._raw(msg, thread_id)).execute()
        meta = self.svc.users().messages().get(
            userId="me", id=sent["id"], format="metadata", metadataHeaders=["Message-ID"]).execute()
        hdr = next((h["value"] for h in meta["payload"]["headers"] if h["name"].lower() == "message-id"), "")
        return {"id": sent["id"], "thread_id": sent["threadId"], "message_id": hdr}

    def create_draft(self, to, subject, body, thread_id=None):
        msg = build_message(to, subject, body, sender=self.me)
        d = self.svc.users().drafts().create(userId="me", body={"message": self._raw(msg, thread_id)}).execute()
        return d["id"]

    def delete_draft(self, draft_id):
        try:
            self.svc.users().drafts().delete(userId="me", id=draft_id).execute()
        except Exception:
            pass

    @staticmethod
    def _text_of(payload) -> str:
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        for part in payload.get("parts", []) or []:
            t = GmailClient._text_of(part)
            if t:
                return t
        return ""

    def new_replies(self, thread_id, after_id=""):
        thread = self.svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        out, seen_marker = [], not after_id
        for m in thread.get("messages", []):
            if not seen_marker:
                seen_marker = m["id"] == after_id
                continue
            hdrs = {h["name"].lower(): h["value"] for h in m["payload"]["headers"]}
            sender = hdrs.get("from", "")
            if self.me in sender.lower() or "mailer-daemon" in sender.lower():
                continue
            out.append({"id": m["id"], "from": sender, "date": hdrs.get("date", ""),
                        "body": strip_quoted(self._text_of(m["payload"]))})
        return out


# ============================================================ MOCK MAILBOX
class MockGmail:
    """File-based fake Gmail so the full workflow can be demonstrated offline.
    state/mock_mailbox/sent.jsonl  = every email 'sent'
    state/mock_mailbox/inbox.json  = replies (add with `simulate-reply`)
    """

    def __init__(self):
        self.dir = cfg.MOCK_MAILBOX
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sent = self.dir / "sent.jsonl"
        self.inbox = self.dir / "inbox.json"
        self.drafts = self.dir / "drafts.json"

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _load(self, path):
        return json.loads(path.read_text()) if path.exists() else []

    def send(self, to, subject, body, thread_id=None, in_reply_to=None):
        mid = uuid.uuid4().hex[:10]
        rec = {"id": mid, "thread_id": thread_id or f"thr_{uuid.uuid4().hex[:8]}", "to": to,
               "subject": subject, "body": body, "in_reply_to": in_reply_to, "ts": self._now()}
        with open(self.sent, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return {"id": mid, "thread_id": rec["thread_id"], "message_id": f"<{mid}@mock.gmail>"}

    def create_draft(self, to, subject, body, thread_id=None):
        drafts = self._load(self.drafts)
        did = f"draft_{uuid.uuid4().hex[:8]}"
        drafts.append({"id": did, "to": to, "subject": subject, "body": body, "thread_id": thread_id})
        self.drafts.write_text(json.dumps(drafts, indent=2))
        return did

    def delete_draft(self, draft_id):
        drafts = [d for d in self._load(self.drafts) if d["id"] != draft_id]
        self.drafts.write_text(json.dumps(drafts, indent=2))

    def new_replies(self, thread_id, after_id=""):
        msgs = [m for m in self._load(self.inbox) if m["thread_id"] == thread_id]
        if after_id:
            ids = [m["id"] for m in msgs]
            msgs = msgs[ids.index(after_id) + 1:] if after_id in ids else msgs
        return msgs

    # demo helpers
    def add_reply(self, thread_id, sender, body):
        inbox = self._load(self.inbox)
        inbox.append({"id": f"in_{uuid.uuid4().hex[:8]}", "thread_id": thread_id, "from": sender,
                      "date": self._now(), "body": body})
        self.inbox.write_text(json.dumps(inbox, indent=2))


def get_gmail():
    return MockGmail() if cfg.GMAIL_MOCK else GmailClient()
