"""Command line interface:  python main.py <command>

Typical flow:
  research -> filter -> generate -> approve -> send -> tick (cron) -> approve-replies -> send-replies
"""
import argparse
import os
import subprocess
import sys
import tempfile
from datetime import timedelta

from . import config as cfg
from . import pipeline as pl
from .gmail_client import MockGmail, get_gmail
from .instagram import get_instagram
from .store import get_store, now_iso


def _edit(text: str) -> str:
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as f:
        f.write(text)
        path = f.name
    subprocess.call([os.environ.get("EDITOR", "nano"), path])
    out = open(path).read()
    os.unlink(path)
    return out


def _review(store, rows, kind: str, approve_all: bool):
    """Human-in-the-loop gate. kind: 'email' or 'reply'."""
    body_f, appr_f = ("draft_body", "approval") if kind == "email" else ("reply_draft", "reply_approval")
    if not rows:
        print(f"No {kind}s waiting for approval.")
        return
    for r in rows:
        print("\n" + "=" * 70)
        print(f"@{r['username']}  <{r['email']}>  score={r['score'] or '-'}  status={r['status']}")
        if kind == "email":
            print(f"Why selected: {r['fit_reason']}\nSubject: {r['draft_subject']}")
        else:
            print(f"Their reply ({r['reply_class']}): {r['reply_text']}\nSuggested next action: {r['next_action']}")
        print("-" * 70 + "\n" + r[body_f])
        if approve_all:
            choice = "a"
        else:
            choice = input("\n[a]pprove / [e]dit / [r]eject / [s]kip > ").strip().lower() or "s"
        if choice == "e":
            store.update(r["username"], **{body_f: _edit(r[body_f])})
            choice = "a"
        if choice == "a":
            store.update(r["username"], **{appr_f: "approved"})
            print("  approved")
        elif choice == "r":
            store.update(r["username"], **{appr_f: "rejected"})
            print("  rejected")
    store.flush()


def main(argv=None):
    p = argparse.ArgumentParser(prog="outreach", description="AI influencer outreach automation")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("research", help="Step 1: find influencers (Instagram Graph API / test data)")
    s.add_argument("--hashtags", default="", help="comma list, e.g. skincare,cleanbeauty")
    s.add_argument("--usernames", default="", help="comma list of usernames to look up")
    s.add_argument("--min-followers", type=int)
    s.add_argument("--max-followers", type=int)
    s.add_argument("--limit", type=int, default=30)

    s = sub.add_parser("import-csv", help="Import creators you collected manually")
    s.add_argument("path")

    sub.add_parser("filter", help="Step 2: Claude scores + summarises new leads")
    sub.add_parser("generate", help="Step 4: Claude drafts cold emails (pending approval)")
    s = sub.add_parser("approve", help="Human approval of cold emails")
    s.add_argument("--all", action="store_true", help="approve everything without prompting (demo only)")
    s = sub.add_parser("send", help="Send approved emails via Gmail (respects daily limit)")
    s.add_argument("--limit", type=int)
    sub.add_parser("followups", help="Step 5: send due follow-ups")
    sub.add_parser("check-replies", help="Step 6: read + classify replies, draft responses")
    sub.add_parser("tick", help="check-replies + followups (run this from cron every ~30 min)")
    s = sub.add_parser("approve-replies", help="Human approval of AI reply drafts")
    s.add_argument("--all", action="store_true")
    sub.add_parser("send-replies", help="Send approved reply drafts")
    sub.add_parser("status", help="Pipeline summary + table")
    s = sub.add_parser("set-status", help="Manually set a CRM status")
    s.add_argument("username")
    s.add_argument("status", choices=cfg.STATUSES)

    s = sub.add_parser("simulate-reply", help="[mock Gmail] drop a reply into the fake inbox")
    s.add_argument("username")
    s.add_argument("text")
    s = sub.add_parser("age", help="[demo] pretend N days passed so follow-ups become due")
    s.add_argument("--days", type=int, required=True)

    a = p.parse_args(argv)
    print(cfg.banner())
    store = get_store()

    if a.cmd == "research":
        stats = pl.research(store, get_instagram(),
                            usernames=[u for u in a.usernames.split(",") if u],
                            hashtags=[h for h in a.hashtags.split(",") if h],
                            min_f=a.min_followers, max_f=a.max_followers, limit=a.limit)
        print("research:", stats)
    elif a.cmd == "import-csv":
        print("import:", pl.import_csv(store, a.path))
    elif a.cmd == "filter":
        print(f"scored {pl.ai_filter(store)} influencers")
    elif a.cmd == "generate":
        print(f"drafted {pl.generate_emails(store)} emails (status: pending approval)")
    elif a.cmd == "approve":
        _review(store, pl.pending(store), "email", a.all)
    elif a.cmd == "send":
        print(f"sent {pl.send_approved(store, get_gmail(), a.limit)} emails")
    elif a.cmd == "followups":
        print("followups:", pl.followups(store, get_gmail()))
    elif a.cmd == "check-replies":
        print(f"processed {pl.check_replies(store, get_gmail())} replies")
    elif a.cmd == "tick":
        g = get_gmail()
        print(f"replies processed: {pl.check_replies(store, g)}")
        print("followups:", pl.followups(store, g))
    elif a.cmd == "approve-replies":
        _review(store, pl.pending(store, "reply_approval"), "reply", a.all)
    elif a.cmd == "send-replies":
        print(f"sent {pl.send_replies(store, get_gmail())} replies")
    elif a.cmd == "set-status":
        store.update(a.username, status=a.status)
        store.flush()
        print(f"@{a.username} -> {a.status}")
    elif a.cmd == "simulate-reply":
        if not cfg.GMAIL_MOCK:
            sys.exit("simulate-reply only works with the mock mailbox (remove credentials.json or set MOCK_GMAIL=1)")
        r = store.find(a.username) or sys.exit("unknown username")
        MockGmail().add_reply(r["thread_id"], r["email"], a.text)
        print(f"reply from @{r['username']} placed in mock inbox")
    elif a.cmd == "age":
        for r in store.all():
            if r["next_followup_at"]:
                shifted = pl._parse(r["next_followup_at"]) - timedelta(days=a.days)
                store.update(r["username"], next_followup_at=shifted.isoformat())
        store.flush()
        print(f"time-travelled {a.days} days")

    if a.cmd in ("status", "research", "filter", "generate", "followups", "tick", "check-replies",
                 "send", "approve", "approve-replies", "send-replies", "set-status", "import-csv"):
        _table(store)


def _table(store):
    print("\nCRM summary:", {k: v for k, v in pl.summary(store).items() if v})
    print(f"{'username':<20}{'score':>6} {'status':<16}{'stage':>5}  {'reply':<22}{'approval':<9}")
    for r in sorted(store.all(), key=lambda r: -int(r["score"] or 0)):
        print(f"{r['username'][:19]:<20}{r['score'] or '-':>6} {r['status']:<16}{r['email_stage'] or '-':>5}  "
              f"{r['reply_class'] or '-':<22}{(r['reply_approval'] or r['approval'] or '-'):<9}")
    print(f"emails sent today: {pl.sent_today()}/{cfg.DAILY_SEND_LIMIT}")
