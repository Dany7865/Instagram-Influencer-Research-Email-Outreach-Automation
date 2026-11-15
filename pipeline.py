"""Workflow steps. Each step is idempotent and safe to re-run (cron-friendly)."""
import json
import random
import time
from datetime import datetime, timedelta, timezone

from . import config as cfg
from . import llm
from .instagram import extract_email
from .store import blank_record, norm_username, now_iso

SEND_LOG = cfg.STATE_DIR / "send_log.jsonl"
WATCH = ("Email Sent", "Follow-up Due", "No Response", "Replied", "Interested")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str):
    return datetime.fromisoformat(ts) if ts else None


# ------------------------------------------------------------------ send budget
def sent_today() -> int:
    if not SEND_LOG.exists():
        return 0
    today = _now().date().isoformat()
    return sum(1 for line in SEND_LOG.read_text().splitlines() if json.loads(line)["ts"][:10] == today)


def _log_send(username: str, kind: str):
    with open(SEND_LOG, "a") as f:
        f.write(json.dumps({"ts": now_iso(), "username": username, "kind": kind}) + "\n")


def _pause():
    if not cfg.GMAIL_MOCK:
        time.sleep(cfg.SEND_DELAY_SECONDS + random.uniform(0, 10))


# ------------------------------------------------------------------ step 1: research
def research(store, ig, usernames=(), hashtags=(), min_f=None, max_f=None, limit=30) -> dict:
    min_f = cfg.MIN_FOLLOWERS if min_f is None else min_f
    max_f = cfg.MAX_FOLLOWERS if max_f is None else max_f
    candidates = [norm_username(u) for u in usernames]
    for h in hashtags:
        candidates += [norm_username(u) for u in ig.candidates(h, limit)]
    stats = {"candidates": 0, "added": 0, "duplicates": 0, "out_of_range": 0, "not_found": 0}
    for u in dict.fromkeys(candidates):  # de-duplicate candidate list, keep order
        stats["candidates"] += 1
        if store.find(u):
            stats["duplicates"] += 1
            continue
        p = ig.profile(u)
        if not p:
            stats["not_found"] += 1
            continue
        if not (min_f <= int(p["followers"]) <= max_f):
            stats["out_of_range"] += 1
            continue
        rec = blank_record()
        rec.update({
            "username": p["username"], "name": p["name"], "profile_url": p["profile_url"],
            "followers": p["followers"], "location": p.get("location", ""), "bio": p["bio"],
            "email": p.get("email") or extract_email(p["bio"]), "source": "instagram" if not cfg.IG_MOCK else "test-data",
        })
        rec["status"] = "Email Available" if rec["email"] else "New Lead"
        if store.add_if_new(rec):
            stats["added"] += 1
        else:
            stats["duplicates"] += 1  # same email under another username
        if stats["added"] >= limit:
            break
    store.flush()
    return stats


def import_csv(store, path) -> dict:
    import csv

    stats = {"added": 0, "duplicates": 0}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rec = blank_record()
            rec.update({k: v for k, v in row.items() if k in rec})
            rec["username"] = norm_username(row.get("username", ""))
            rec["email"] = (row.get("email") or extract_email(row.get("bio", ""))).lower()
            rec["profile_url"] = rec["profile_url"] or f"https://www.instagram.com/{rec['username']}/"
            rec["source"] = "csv-import"
            rec["status"] = "Email Available" if rec["email"] else "New Lead"
            stats["added" if store.add_if_new(rec) else "duplicates"] += 1
    store.flush()
    return stats


# ------------------------------------------------------------------ step 2: AI filtering
def ai_filter(store) -> int:
    n = 0
    for r in store.all():
        if r["score"] != "":
            continue
        res = llm.score_influencer(r)
        store.update(
            r["username"], ai_category=res["category"], ai_summary=res["summary"], score=res["score"],
            fit_reason=res["fit_reason"], location=r["location"] or res.get("location", ""),
            qualified="yes" if res["score"] >= cfg.MIN_SCORE else "no")
        n += 1
        store.flush()
    return n


# ------------------------------------------------------------------ step 4a: draft cold emails
def generate_emails(store) -> int:
    n = 0
    suppressed = store.suppressed_emails()
    ranked = sorted(store.all(), key=lambda r: -int(r["score"] or 0))
    for r in ranked:
        if (r["status"] == "Email Available" and r["qualified"] == "yes" and r["approval"] == ""
                and r["opted_out"] != "yes" and r["email"].lower() not in suppressed):
            d = llm.write_initial_email(r)
            store.update(r["username"], draft_subject=d["subject"], draft_body=d["body"], approval="pending")
            n += 1
    store.flush()
    return n


def pending(store, field="approval"):
    return [r for r in store.all() if r[field] == "pending"]


# ------------------------------------------------------------------ step 4b: send approved emails
def send_approved(store, gmail, limit=None) -> int:
    sent = 0
    for r in sorted(store.all(), key=lambda r: -int(r["score"] or 0)):
        if r["approval"] != "approved" or r["status"] != "Email Available" or r["opted_out"] == "yes":
            continue
        if sent_today() >= cfg.DAILY_SEND_LIMIT or (limit is not None and sent >= limit):
            print(f"  daily send limit reached ({cfg.DAILY_SEND_LIMIT}); remaining emails stay queued")
            break
        res = gmail.send(r["email"], r["draft_subject"], r["draft_body"])
        store.update(
            r["username"], status="Email Sent", email_stage=1, approval="sent", thread_id=res["thread_id"],
            last_message_id=res["message_id"], last_sent_at=now_iso(),
            next_followup_at=(_now() + timedelta(days=cfg.FOLLOWUP1_DAYS)).replace(microsecond=0).isoformat())
        _log_send(r["username"], "initial")
        store.flush()
        sent += 1
        print(f"  sent -> @{r['username']} <{r['email']}>")
        _pause()
    return sent


# ------------------------------------------------------------------ step 5: follow-ups
def followups(store, gmail) -> dict:
    check_replies(store, gmail)  # always look for replies first so we never nudge someone who answered
    stats = {"sent": 0, "marked_due": 0, "no_response": 0}
    for r in store.all():
        if r["status"] not in ("Email Sent", "Follow-up Due") or r["opted_out"] == "yes":
            continue
        due = _parse(r["next_followup_at"])
        if not due or due > _now():
            continue
        stage = int(r["email_stage"] or 1)  # 1 = initial sent, 2 = FU1 sent, 3 = FU2 sent
        if stage > cfg.MAX_FOLLOWUPS:
            store.update(r["username"], status="No Response", next_followup_at="")
            stats["no_response"] += 1
            continue
        if r["status"] != "Follow-up Due":
            store.update(r["username"], status="Follow-up Due")
            stats["marked_due"] += 1
        if sent_today() >= cfg.DAILY_SEND_LIMIT:
            print("  daily send limit reached; follow-ups stay 'Follow-up Due'")
            continue
        body = llm.write_followup(r, followup_no=stage)
        res = gmail.send(r["email"], "Re: " + r["draft_subject"], body, thread_id=r["thread_id"],
                         in_reply_to=r["last_message_id"])
        nxt = (_now() + timedelta(days=cfg.FOLLOWUP2_DAYS if stage == 1 else 7)).replace(microsecond=0).isoformat()
        store.update(r["username"], status="Email Sent", email_stage=stage + 1, last_message_id=res["message_id"],
                     last_sent_at=now_iso(), next_followup_at=nxt)
        _log_send(r["username"], f"followup{stage}")
        store.flush()
        stats["sent"] += 1
        print(f"  follow-up {stage} -> @{r['username']}")
        _pause()
    store.flush()
    return stats


# ------------------------------------------------------------------ step 6: replies
def check_replies(store, gmail) -> int:
    handled = 0
    for r in store.all():
        if not r["thread_id"] or r["status"] not in WATCH or r["opted_out"] == "yes":
            continue
        msgs = gmail.new_replies(r["thread_id"], r["reply_id_last"])
        if not msgs:
            continue
        last = msgs[-1]
        cls = llm.classify_reply(r, last["body"])
        cat = cls["category"]
        status = {"interested": "Interested", "pricing_enquiry": "Interested",
                  "collaboration_request": "Interested", "not_interested": "Not Interested",
                  "opt_out": "Not Interested"}.get(cat, "Replied")
        fields = dict(status=status, reply_id_last=last["id"], reply_text=last["body"], reply_class=cat,
                      reply_summary=cls["summary"], next_action=cls["next_action"],
                      next_followup_at="", reply_draft="", reply_approval="")  # replies STOP follow-ups
        if cat == "opt_out":
            fields.update(opted_out="yes", next_action="Opted out - never contact again")
        else:
            body = llm.draft_reply(r, last["body"], cat)
            subj = "Re: " + r["draft_subject"]
            fields.update(reply_draft=body, reply_approval="pending",
                          reply_draft_id=gmail.create_draft(r["email"], subj, body, r["thread_id"]))
        store.update(r["username"], **fields)
        store.flush()
        handled += 1
        print(f"  reply from @{r['username']}: {cat} -> {status}")
    return handled


def send_replies(store, gmail) -> int:
    n = 0
    for r in store.all():
        if r["reply_approval"] != "approved" or r["opted_out"] == "yes":
            continue
        if sent_today() >= cfg.DAILY_SEND_LIMIT:
            print("  daily send limit reached")
            break
        if r["reply_draft_id"]:
            gmail.delete_draft(r["reply_draft_id"])
        res = gmail.send(r["email"], "Re: " + r["draft_subject"], r["reply_draft"],
                         thread_id=r["thread_id"], in_reply_to=r["last_message_id"])
        store.update(r["username"], reply_approval="sent", reply_draft_id="", last_message_id=res["message_id"],
                     last_sent_at=now_iso())
        _log_send(r["username"], "reply")
        store.flush()
        n += 1
        print(f"  reply sent -> @{r['username']}")
    return n


# ------------------------------------------------------------------ reporting
def summary(store) -> dict:
    out = {s: 0 for s in cfg.STATUSES}
    for r in store.all():
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out
