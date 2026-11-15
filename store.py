"""CRM storage. One row per influencer, de-duplicated by username AND email.

Backends: local CSV (default / offline) and Google Sheets (service account).
Both share the same in-memory logic; call flush() to persist.
"""
import csv
from datetime import datetime, timezone

from . import config as cfg

COLUMNS = [
    "username", "name", "profile_url", "followers", "location", "email", "bio", "source",
    "ai_category", "ai_summary", "score", "fit_reason", "qualified",
    "status", "email_stage", "approval", "draft_subject", "draft_body",
    "thread_id", "last_message_id", "last_sent_at", "next_followup_at",
    "reply_id_last", "reply_text", "reply_class", "reply_summary", "next_action",
    "reply_draft", "reply_approval", "reply_draft_id",
    "opted_out", "created_at", "updated_at",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_username(u: str) -> str:
    u = (u or "").strip().lower()
    u = u.replace("https://www.instagram.com/", "").replace("https://instagram.com/", "")
    return u.strip("@/ ")


def blank_record() -> dict:
    return {c: "" for c in COLUMNS}


class BaseStore:
    def __init__(self):
        self._rows = None

    # --- backend hooks
    def _read(self) -> list:
        raise NotImplementedError

    def _write(self, rows: list) -> None:
        raise NotImplementedError

    # --- public API
    @property
    def rows(self) -> list:
        if self._rows is None:
            self._rows = self._read()
        return self._rows

    def all(self) -> list:
        return self.rows

    def find(self, username: str):
        u = norm_username(username)
        return next((r for r in self.rows if norm_username(r["username"]) == u), None)

    def add_if_new(self, rec: dict) -> bool:
        """Insert unless username or email already exists. Returns True if added."""
        u = norm_username(rec["username"])
        email = (rec.get("email") or "").strip().lower()
        for r in self.rows:
            if norm_username(r["username"]) == u:
                return False
            if email and r["email"].strip().lower() == email:
                return False
        row = blank_record()
        row.update({k: str(v) for k, v in rec.items() if k in row})
        row["username"] = u
        row["created_at"] = row["updated_at"] = now_iso()
        self.rows.append(row)
        return True

    def update(self, username: str, **fields) -> dict:
        r = self.find(username)
        if r is None:
            raise KeyError(username)
        for k, v in fields.items():
            if k in r:
                r[k] = "" if v is None else str(v)
        r["updated_at"] = now_iso()
        return r

    def flush(self) -> None:
        if self._rows is not None:
            self._write(self._rows)

    def suppressed_emails(self) -> set:
        return {r["email"].lower() for r in self.rows if r["opted_out"] == "yes" and r["email"]}


class CSVStore(BaseStore):
    def __init__(self, path=cfg.LOCAL_SHEET):
        super().__init__()
        self.path = path

    def _read(self):
        if not self.path.exists():
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            rows = []
            for row in csv.DictReader(f):
                rec = blank_record()
                rec.update({k: v for k, v in row.items() if k in rec})
                rows.append(rec)
            return rows

    def _write(self, rows):
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)


class SheetsStore(BaseStore):
    """Google Sheets backend. Share the sheet with the service-account e-mail (Editor)."""

    def __init__(self):
        super().__init__()
        import gspread

        gc = gspread.service_account(filename=str(cfg.SERVICE_ACCOUNT_FILE))
        self.ws = gc.open_by_key(cfg.GOOGLE_SHEET_ID).sheet1

    def _read(self):
        values = self.ws.get_all_values()
        if not values:
            self.ws.update(range_name="A1", values=[COLUMNS])
            return []
        header, body = values[0], values[1:]
        rows = []
        for line in body:
            rec = blank_record()
            for k, v in zip(header, line):
                if k in rec:
                    rec[k] = v
            if rec["username"]:
                rows.append(rec)
        return rows

    def _write(self, rows):
        data = [COLUMNS] + [[r[c] for c in COLUMNS] for r in rows]
        self.ws.clear()
        self.ws.update(range_name="A1", values=data)


def get_store() -> BaseStore:
    return CSVStore() if cfg.SHEETS_MOCK else SheetsStore()
