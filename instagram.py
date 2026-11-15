"""Instagram research via the OFFICIAL Instagram Graph API only (no scraping, no logins).

Requirements for live mode (see README):
  * your own Instagram Business/Creator account linked to a Facebook Page
  * a Meta app with `instagram_basic` + `instagram_manage_insights` / Business Discovery access
  * IG_ACCESS_TOKEN + IG_USER_ID in .env

What the API can and cannot give us:
  * Business Discovery returns PUBLIC data of other Business/Creator accounts:
    username, name, biography, website, followers_count, media_count.
  * It does NOT return the "Email" contact button, so public emails are collected only
    from the public biography text (or the website field). Location is not returned either;
    Claude infers it from the bio during filtering.
  * Hashtag search returns posts (captions/permalinks), not owners, so candidate
    usernames are taken from @mentions in top posts for the hashtag. You can also pass
    usernames directly or import a CSV of creators you found manually.
"""
import csv
import re
from collections import Counter

from . import config as cfg

GRAPH = "https://graph.facebook.com/v21.0"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MENTION_RE = re.compile(r"@([A-Za-z0-9._]{3,30})")


def extract_email(text: str) -> str:
    m = EMAIL_RE.search(text or "")
    return m.group(0).lower().rstrip(".") if m else ""


class InstagramAPI:
    def __init__(self):
        import requests

        self.http = requests
        self.token = cfg.IG_ACCESS_TOKEN
        self.uid = cfg.IG_USER_ID

    def _get(self, path: str, **params):
        params["access_token"] = self.token
        r = self.http.get(f"{GRAPH}/{path}", params=params, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()

    def profile(self, username: str):
        fields = (f"business_discovery.username({username})"
                  "{username,name,biography,website,followers_count,media_count}")
        data = self._get(self.uid, fields=fields)
        if not data or "business_discovery" not in data:
            return None  # private, personal account, or not found
        bd = data["business_discovery"]
        bio = bd.get("biography", "") or ""
        return {
            "username": bd["username"],
            "name": bd.get("name") or bd["username"],
            "followers": bd.get("followers_count", 0),
            "bio": bio,
            "website": bd.get("website", ""),
            "email": extract_email(bio) or extract_email(bd.get("website", "")),
            "location": "",
            "profile_url": f"https://www.instagram.com/{bd['username']}/",
        }

    def candidates(self, hashtag: str, limit: int = 30):
        tag = hashtag.lstrip("#")
        found = self._get("ig_hashtag_search", user_id=self.uid, q=tag)
        if not found or not found.get("data"):
            return []
        hid = found["data"][0]["id"]
        media = self._get(f"{hid}/top_media", user_id=self.uid, fields="caption,permalink", limit=50)
        counts = Counter()
        for item in (media or {}).get("data", []):
            counts.update(m.lower() for m in MENTION_RE.findall(item.get("caption", "")))
        return [u for u, _ in counts.most_common(limit)]


class MockInstagram:
    """Offline stand-in that reads data/test_influencers.csv (clearly fake test data)."""

    def __init__(self, path=None):
        self.path = path or (cfg.DATA_DIR / "test_influencers.csv")
        with open(self.path, newline="", encoding="utf-8") as f:
            self.rows = list(csv.DictReader(f))

    def profile(self, username: str):
        u = username.lower().lstrip("@")
        for r in self.rows:
            if r["username"].lower() == u:
                bio = r["bio"]
                return {
                    "username": r["username"], "name": r["name"],
                    "followers": int(r["followers"]), "bio": bio, "website": "",
                    "email": extract_email(bio) or r.get("email", ""),
                    "location": r.get("location", ""),
                    "profile_url": f"https://www.instagram.com/{r['username']}/",
                }
        return None

    def candidates(self, hashtag: str, limit: int = 30):
        tag = hashtag.lstrip("#").lower()
        out = [r["username"] for r in self.rows
               if tag in r["bio"].lower() or tag in r.get("hashtags", "").lower()]
        return out[:limit]


def get_instagram():
    return MockInstagram() if cfg.IG_MOCK else InstagramAPI()
