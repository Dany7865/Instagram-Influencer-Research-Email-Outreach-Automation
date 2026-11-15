# AI-Powered Instagram Influencer Research & Email Outreach Automation

**Sankar Group – AI Automation Internship Assignment**
Submitted by: Danyal Ali | Roll No: 2401331690015 | Branch: CSE R

A Python system that finds Instagram influencers, filters them with Claude, stores them in
Google Sheets, sends human-approved personalised Gmail outreach, runs automatic follow-ups, and
classifies replies with AI-drafted responses that also need human approval.

```
Instagram research -> contact collection -> Claude filtering/scoring -> Google Sheet CRM
   -> Claude cold email -> HUMAN APPROVAL -> Gmail send (daily limit)
   -> follow-up 1 (day 4) -> follow-up 2 (+6 days) -> stop on reply/decline/opt-out
   -> reply detection -> Claude classification + reply draft -> HUMAN APPROVAL -> send -> CRM status update
```

## Tools used
| Need | Tool |
|---|---|
| Orchestration | Python 3.10+ (CLI, cron-friendly `tick` command) |
| AI | Claude via the Anthropic API (`outreach/llm.py`) |
| Instagram | Official Instagram Graph API: Business Discovery + Hashtag Search (no scraping) |
| CRM | Google Sheets via `gspread` (service account); local CSV fallback |
| Email | Gmail API with OAuth (send, read threads, drafts) |

## Project layout
```
main.py                 entry point  (python main.py <command>)
outreach/config.py      all settings from .env; auto-selects live vs mock per integration
outreach/instagram.py   Graph API research + mock
outreach/llm.py         Claude prompts: score, cold email, follow-up, classify, reply draft
outreach/store.py       CRM (Google Sheets / CSV), dedupe by username AND email
outreach/gmail_client.py  Gmail API + offline mock mailbox
outreach/pipeline.py    workflow steps (idempotent)
outreach/cli.py         commands + human-approval gate
data/test_influencers.csv  the 12 influencers used for the project (fill the email column)
samples/                sample CRM sheet, sample emails, demo run log
```

## Quick start (offline demo, no accounts needed)
```bash
pip install -r requirements.txt
cp .env.example .env
python main.py research --usernames rajshamani,beerbiceps,physicswallah,tanyakhanijow,ca_rachana_ranade,taneja.gaurav,sanjyotkeer,sourav_joshi_vlogs,the.rebel.kid,curly.tales,kushakapila,awez_darbar
python main.py filter
```
The 12 creators in `data/test_influencers.csv` are loaded with an empty `email` column: add each creator's public business email there (or your own test addresses for the demo) before running `generate`.
Every integration falls back to a local mock when its credentials are missing, so the whole
workflow runs end to end. The mode banner printed on every command shows what is live.
**For the graded demo, switch on the real accounts below and record that run.**

## Going live
1. **Claude**: set `ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`).
2. **Gmail**: Google Cloud Console -> new project -> enable *Gmail API* -> OAuth consent screen
   (External, add your Gmail as a test user) -> Credentials -> *OAuth client ID (Desktop)* ->
   download as `credentials.json` next to `main.py`. First run opens a browser to authorise.
   Use a dedicated test Gmail account. Set `SENDER_EMAIL` to it.
3. **Google Sheets**: enable *Google Sheets API*, create a *service account*, download its key as
   `service_account.json`, create a blank Sheet, **share it with the service-account e-mail (Editor)**,
   put the Sheet ID (from the URL) in `GOOGLE_SHEET_ID`. Headers are created automatically.
4. **Instagram**: needs your own Instagram Business/Creator account linked to a Facebook Page and a
   Meta developer app with the Instagram Graph API. Put the long-lived token in `IG_ACCESS_TOKEN` and the
   IG account id in `IG_USER_ID`. For a quick demo you can also use `import-csv` with creators you
   found manually on Instagram.
5. Edit brand/campaign/sender fields in `.env`.

## Commands
| Command | Step | What it does |
|---|---|---|
| `research --hashtags skincare,wellness --usernames a,b` | 1 | Finds creators, filters by followers, extracts public bio email, dedupes |
| `import-csv file.csv` | 1 | Import creators you collected manually (same dedupe rules) |
| `filter` | 2 | Claude: category, summary, suitability score 0-100, fit reason, qualified if >= `MIN_SCORE` |
| `generate` | 4 | Claude drafts a personalised cold email per qualified lead -> `pending` |
| `approve` | 4 | **Human gate**: approve / edit / reject each email |
| `send` | 4 | Sends approved mails through Gmail up to `DAILY_SEND_LIMIT`, with delay + jitter |
| `tick` | 5, 6 | Checks replies, then sends due follow-ups. Run from cron every 30 min |
| `approve-replies` / `send-replies` | 6 | **Human gate** for AI reply drafts, then send in-thread |
| `set-status user "Collaboration Confirmed"` | 3 | Manual CRM update |
| `status` | 3 | Pipeline summary |

Cron example: `*/30 * * * * cd /path/to/influencer-outreach && python3 main.py tick`

## How each requirement is met
- **Step 1 Instagram research**: official Graph API only (no scraping, no login automation, no private data).
  Collects name, username, profile link, follower count, bio-listed public email, location (when stated).
- **Step 2 AI filtering**: `llm.score_influencer` returns niche category, 40-word summary, score, and fit reason.
- **Step 3 CRM**: statuses *New Lead, Email Available, Email Sent, Follow-up Due, Replied, Interested,
  Not Interested, Collaboration Confirmed* (+ *No Response* after the final follow-up). Duplicates are blocked
  by username and by email.
- **Step 4 Cold email**: Claude personalises using the fit reason; the code appends a fixed footer with sender
  identity, postal address, reason for contact, and an opt-out line, plus a `List-Unsubscribe` header.
  Nothing is sent until a human approves it; sending is capped per day.
- **Step 5 Follow-ups**: day 4 and +6 days, in the same Gmail thread. Replies are checked first, so anyone who
  replied, declined or opted out is never followed up. Opted-out contacts are suppressed by email everywhere.
- **Step 6 Reply management**: classes `interested, not_interested, pricing_enquiry, collaboration_request,
  opt_out, other`; Claude writes a summary, suggested next action and a draft (also saved as a Gmail draft).
  Unknown rates/dates become `[PLACEHOLDER: ...]` for the human. The CRM status updates automatically.

## Safety and compliance notes
- Influencer bios and replies are treated as untrusted data in prompts (prompt-injection hardening).
- Only publicly listed business emails are used; opt-outs are honoured immediately and permanently.
- Keep volumes low (default 20/day) and use a dedicated sender account; set a real postal address.
- Instagram's API does not expose the profile "Email" button, so emails come from the bio text/website field.
  Hashtag search yields @mentions rather than post owners, so combine it with `--usernames` or `import-csv`.
- Mock-mode samples in `samples/` come from fake test data; they are not a substitute for the live demo.

## Submission checklist
1. Live demo with your accounts: run the commands above in order (replace `simulate-reply`/`age` by replying
   from a second Gmail account and temporarily setting `FOLLOWUP1_DAYS=0`).
2. Screen recording of that run explaining each step.
3. This repository / zip.
4. Share your Google Sheet (see `samples/sample_crm_sheet.csv` for the layout).
5. Sample emails: `samples/sample_emails.md` (replace with your live ones).
6. This README is the tools/system explanation.
