"""Writes samples/ (sheet CSV + sample initial email, follow-up, AI reply) from the current state."""
import csv, json, shutil
from outreach import config as cfg

out = cfg.ROOT / "samples"
out.mkdir(exist_ok=True)
shutil.copy(cfg.LOCAL_SHEET, out / "sample_crm_sheet.csv")
sent = [json.loads(l) for l in (cfg.MOCK_MAILBOX / "sent.jsonl").read_text().splitlines()]
rows = {r["email"]: r for r in csv.DictReader(open(cfg.LOCAL_SHEET, encoding="utf-8"))}
by_thread = {}
for m in sent:
    by_thread.setdefault(m["thread_id"], []).append(m)

def show(title, m):
    return f"## {title}\n\n**To:** {m['to']}  \n**Subject:** {m['subject']}\n\n```\n{m['body']}\n```\n\n"

doc = "# Sample emails (generated in mock mode with test data)\n\n"
for th in by_thread.values():
    if len(th) >= 3:  # initial + 2 follow-ups
        doc += show("Initial cold email", th[0]) + show("Follow-up 1 (day 4)", th[1]) + show("Follow-up 2 (day ~10)", th[2])
        break
for th in by_thread.values():
    r = rows.get(th[0]["to"])
    if r and r["reply_class"] and r["reply_class"] != "opt_out" and len(th) >= 2:
        doc += f"## Influencer reply -> AI classification\n\n> {r['reply_text']}\n\n"
        doc += f"- **Class:** {r['reply_class']}\n- **Summary:** {r['reply_summary']}\n- **Suggested next action:** {r['next_action']}\n- **CRM status:** {r['status']}\n\n"
        doc += show("AI-generated reply (approved by human, then sent)", th[-1])
        break
(out / "sample_emails.md").write_text(doc, encoding="utf-8")
print("wrote samples/sample_crm_sheet.csv and samples/sample_emails.md")
