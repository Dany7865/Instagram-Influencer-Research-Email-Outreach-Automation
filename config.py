"""Central configuration. Everything is driven by environment variables / .env.

Each integration auto-falls back to a local MOCK implementation when its
credentials are missing, so the whole pipeline can be exercised offline and
then switched to live accounts just by adding credentials.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv is optional
    pass


def _e(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _i(key: str, default: int) -> int:
    try:
        return int(_e(key, str(default)))
    except ValueError:
        return default


STATE_DIR = ROOT / "state"
DATA_DIR = ROOT / "data"
STATE_DIR.mkdir(exist_ok=True)

# ---- CRM statuses (assignment list + "No Response" after the final follow-up)
STATUSES = [
    "New Lead",
    "Email Available",
    "Email Sent",
    "Follow-up Due",
    "Replied",
    "Interested",
    "Not Interested",
    "Collaboration Confirmed",
    "No Response",
]

# ---- Brand / campaign (edit in .env)
BRAND_NAME = _e("BRAND_NAME", "GlowLeaf Organics")
BRAND_DESCRIPTION = _e(
    "BRAND_DESCRIPTION",
    "GlowLeaf Organics is an Indian clean-beauty brand making vegan, cruelty-free skincare "
    "with plant-based ingredients.",
)
CAMPAIGN_BRIEF = _e(
    "CAMPAIGN_BRIEF",
    "Winter Glow campaign: we are looking for creators to try our new Vitamin C + Aloe "
    "serum range and share honest reels/stories. We offer a free product kit plus a paid "
    "collaboration (budget discussed per creator) and an affiliate code.",
)
TARGET_NICHE = _e("TARGET_NICHE", "skincare, beauty, wellness, self-care, clean beauty")
TARGET_KEYWORDS = [k.strip().lower() for k in _e(
    "TARGET_KEYWORDS",
    "skincare,beauty,wellness,self-care,clean beauty,vegan,organic,glow,makeup,selfcare",
).split(",") if k.strip()]
TARGET_LOCATION = _e("TARGET_LOCATION", "India")
MIN_FOLLOWERS = _i("MIN_FOLLOWERS", 100_000)
MAX_FOLLOWERS = _i("MAX_FOLLOWERS", 50_000_000)
MIN_SCORE = _i("MIN_SCORE", 60)  # suitability score (0-100) needed to be emailed

# ---- Sender identity (required for anti-spam compliance: CAN-SPAM / IT Act style)
SENDER_NAME = _e("SENDER_NAME", "Danyal Ali")
SENDER_TITLE = _e("SENDER_TITLE", "Influencer Partnerships")
SENDER_EMAIL = _e("SENDER_EMAIL", "partnerships@example.com")
COMPANY_ADDRESS = _e("COMPANY_ADDRESS", "GlowLeaf Organics, 12 Example Street, Noida, UP 201301, India")

# ---- Sending policy
DAILY_SEND_LIMIT = _i("DAILY_SEND_LIMIT", 20)
SEND_DELAY_SECONDS = _i("SEND_DELAY_SECONDS", 30)  # jitter is added on top; ignored in mock mode
FOLLOWUP1_DAYS = _i("FOLLOWUP1_DAYS", 4)   # 3-5 days after the first email
FOLLOWUP2_DAYS = _i("FOLLOWUP2_DAYS", 6)   # 5-7 days after follow-up 1
MAX_FOLLOWUPS = 2

# ---- Claude
ANTHROPIC_API_KEY = _e("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _e("CLAUDE_MODEL", "claude-sonnet-5-5")
LLM_MOCK = (not ANTHROPIC_API_KEY) or _e("MOCK_LLM") == "1"

# ---- Instagram (official Graph API only)
IG_ACCESS_TOKEN = _e("IG_ACCESS_TOKEN")
IG_USER_ID = _e("IG_USER_ID")  # your own Instagram Business/Creator account id
IG_MOCK = (not (IG_ACCESS_TOKEN and IG_USER_ID)) or _e("MOCK_INSTAGRAM") == "1"

# ---- Gmail
GMAIL_CREDENTIALS = ROOT / _e("GMAIL_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN = ROOT / "token.json"
GMAIL_MOCK = (not GMAIL_CREDENTIALS.exists()) or _e("MOCK_GMAIL") == "1"
MOCK_MAILBOX = STATE_DIR / "mock_mailbox"

# ---- Google Sheets (service account) or local CSV fallback
GOOGLE_SHEET_ID = _e("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_FILE = ROOT / _e("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
SHEETS_MOCK = (not (GOOGLE_SHEET_ID and SERVICE_ACCOUNT_FILE.exists())) or _e("MOCK_SHEETS") == "1"
LOCAL_SHEET = STATE_DIR / "influencers.csv"


def banner() -> str:
    def m(flag): return "MOCK (local)" if flag else "LIVE"
    return (f"[modes] Claude: {m(LLM_MOCK)} | Instagram: {m(IG_MOCK)} | "
            f"Gmail: {m(GMAIL_MOCK)} | Sheet: {'LOCAL CSV' if SHEETS_MOCK else 'GOOGLE SHEETS'}")
