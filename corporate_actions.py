"""NSE corporate-action alerts: STOCK SPLIT, BONUS ISSUE, BUYBACK - all NSE stocks.
Sends only NEW announcements to Telegram (deduped via corp_state.json).
Usage: python corporate_actions.py [--test]
"""
import datetime as dt
import html
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOOKAHEAD_DAYS = int(os.getenv("LOOKAHEAD_DAYS", 180))    # ex-dates up to N days ahead
SEND_EXISTING = os.getenv("SEND_EXISTING", "false").lower() == "true"  # first run: alert already-known items?
WANTED = {k.strip() for k in os.getenv("KINDS", "split,bonus,buyback").split(",")}

STATE_FILE = Path("corp_state.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = "https://www.nseindia.com"
API = BASE + "/api/corporates-corporateActions"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/companies-listing/corporate-filings-actions",
}
ICONS = {"split": "✂️ STOCK SPLIT", "bonus": "🎁 BONUS ISSUE", "buyback": "💰 BUYBACK"}


# ---------- fetch ----------
def fetch_actions(frm, to):
    last_err = None
    for attempt in range(4):
        try:
            s = requests.Session()
            s.headers.update(HEADERS)
            s.get(BASE, timeout=30)                                             # prime cookies
            s.get(BASE + "/companies-listing/corporate-filings-actions", timeout=30)
            r = s.get(API, params={"index": "equities",
                                   "from_date": frm.strftime("%d-%m-%Y"),
                                   "to_date": to.strftime("%d-%m-%Y")}, timeout=40)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = data.get("data", [])
            if isinstance(data, list):
                return data
            last_err = f"unexpected response: {str(data)[:200]}"
        except Exception as e:
            last_err = e
        print(f"[retry {attempt + 1}] NSE fetch failed: {last_err}")
        time.sleep(5 * (attempt + 1))
    sys.exit(f"NSE fetch failed after retries: {last_err}")


def classify(subject):
    s = " ".join(str(subject).lower().replace("-", " ").split())
    kinds = []
    if "bonus" in s and "debenture" not in s and "ncd" not in s:
        kinds.append("bonus")
    if "split" in s or "sub division" in
