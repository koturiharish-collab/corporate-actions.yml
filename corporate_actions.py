#!/usr/bin/env python3
"""NSE corporate actions alert: stock splits, bonus issues and buybacks -> Telegram.

Two sources are checked on every run:
  1. NSE corporate ACTIONS (has ex-date / record date)  -> "✂️ STOCK SPLIT" etc.
  2. NSE corporate ANNOUNCEMENTS (board approvals, press releases, usually days or
     weeks BEFORE the ex-date is fixed)                  -> "📢 ANNOUNCEMENT" alerts.

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
State file: seen.json (keeps alerts from being sent twice)
"""
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE = "seen.json"

LOOKBACK_DAYS = 2        # actions with ex-date from the last couple of days
LOOKAHEAD_DAYS = 90      # upcoming ex-dates window
ANN_LOOKBACK_DAYS = 2    # announcements from the last couple of days

IST = timezone(timedelta(hours=5, minutes=30))

NSE_HOME = "https://www.nseindia.com"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
NSE_ACTIONS_API = "https://www.nseindia.com/api/corporates-corporateActions"
NSE_ANN_API = "https://www.nseindia.com/api/corporate-announcements"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_PAGE,
}

TITLES = {
    "SPLIT": "✂️ STOCK SPLIT",
    "BONUS": "🎁 BONUS ISSUE",
    "BUYBACK": "💰 BUYBACK",
}
ANN_TITLES = {
    "SPLIT": "📢 ANNOUNCEMENT: STOCK SPLIT",
    "BONUS": "📢 ANNOUNCEMENT: BONUS ISSUE",
    "BUYBACK": "📢 ANNOUNCEMENT: BUYBACK",
}


def classify(text):
    """Return SPLIT / BONUS / BUYBACK, or None if the text is not one we track."""
    s = (text or "").lower()
    if "split" in s or "sub division" in s or "sub-division" in s or "subdivision" in s:
        return "SPLIT"
    if "bonus" in s:
        return "BONUS"
    if "buy back" in s or "buyback" in s or "buy-back" in s:
        return "BUYBACK"
    return None


def is_weekend_ist():
    return datetime.now(IST).weekday() >= 5


def nse_get(url, params):
    """GET a JSON list from NSE with cookies + retries (NSE is flaky)."""
    last_err = None
    for attempt in range(1, 5):
        try:
            session = requests.Session()
            session.headers.update(HEADERS)
            # NSE needs cookies from a normal page visit before the API works.
            session.get(NSE_HOME, timeout=20)
            session.get(NSE_PAGE, timeout=20)
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                data = data.get("data", [])
            if not isinstance(data, list):
                raise ValueError("Unexpected NSE response format")
            return data
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"Attempt {attempt} failed for {url}: {exc}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_err}")


def fetch_actions():
    frm = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%m-%Y")
    to = (date.today() + timedelta(days=LOOKAHEAD_DAYS)).strftime("%d-%m-%Y")
    data = nse_get(NSE_ACTIONS_API, {"index": "equities", "from_date": frm, "to_date": to})
    print(f"Fetched {len(data)} corporate actions ({frm} to {to})")
    return data


def fetch_announcements():
    frm = (date.today() - timedelta(days=ANN_LOOKBACK_DAYS)).strftime("%d-%m-%Y")
    to = date.today().strftime("%d-%m-%Y")
    data = nse_get(NSE_ANN_API, {"index": "equities", "from_date": frm, "to_date": to})
    print(f"Fetched {len(data)} corporate announcements ({frm} to {to})")
    return data


def load_seen():
    """Return a set of seen keys, or None if the state file doesn't exist yet."""
    if not os.path.exists(SEEN_FILE):
        return None
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read {SEEN_FILE}: {exc}")
        return set()
    if isinstance(data, dict):
        return set(map(str, data.keys()))
    if isinstance(data, list):
        return set(map(str, data))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=1)


def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("Telegram secrets missing, would have sent:\n" + text)
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": text[:4000],
                "disable_web_page_preview": "true",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Telegram request failed: {exc}")
        return False


def build_action_message(kind, item):
    symbol = item.get("symbol", "?")
    company = item.get("comp", "")
    subject = (item.get("subject") or "").strip()
    ex_date = item.get("exDate") or "-"
    rec_date = item.get("recDate") or "-"
    face_val = item.get("faceVal")
    lines = [TITLES[kind], "", f"📊 Stock: {symbol}"]
    if company:
        lines.append(f"🏢 Company: {company}")
    lines.append(f"📝 Action: {subject}")
    if face_val not in (None, "", "-"):
        lines.append(f"💵 Face Value: ₹{face_val}")
    lines += [
        f"📅 Ex-Date: {ex_date}",
        f"📅 Record Date: {rec_date}",
        "",
        f"🔗 https://www.nseindia.com/get-quotes/equity?symbol={symbol}",
    ]
    return "\n".join(lines)


def build_announcement_message(kind, item):
    symbol = item.get("symbol", "?")
    company = item.get("sm_name") or ""
    category = (item.get("desc") or "").strip()
    text = " ".join((item.get("attchmntText") or "").split())
    when = item.get("an_dt") or item.get("sort_date") or "-"
    link = item.get("attchmntFile") or ""
    lines = [ANN_TITLES[kind], "", f"📊 Stock: {symbol}"]
    if company:
        lines.append(f"🏢 Company: {company}")
    if category:
        lines.append(f"🗂 Category: {category}")
    lines.append(f"🕒 Announced: {when}")
    if text:
        lines += ["", f"📝 {text[:600]}"]
    lines.append("")
    if link:
        lines.append(f"📎 {link}")
    lines.append(f"🔗 https://www.nseindia.com/get-quotes/equity?symbol={symbol}")
    return "\n".join(lines)


def collect_candidates(actions, announcements):
    """Return a list of (key, message) for every tracked item."""
    out = []
    for item in actions or []:
        kind = classify(item.get("subject"))
        if not kind:
            continue
        key = "|".join(
            [
                kind,
                str(item.get("symbol", "")),
                str(item.get("exDate", "")),
                str(item.get("subject", "")).strip(),
            ]
        )
        out.append((key, build_action_message(kind, item)))

    for item in announcements or []:
        text = (item.get("attchmntText") or "") + " " + (item.get("desc") or "")
        kind = classify(text)
        if not kind:
            continue
        digest = hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:12]
        key = "|".join(
            ["ANN", kind, str(item.get("symbol", "")), str(item.get("an_dt", "")), digest]
        )
        out.append((key, build_announcement_message(kind, item)))
    return out


def main():
    fetch_failed = False
    actions = None
    announcements = None
    try:
        actions = fetch_actions()
    except Exception as exc:  # noqa: BLE001
        print(f"ACTIONS FETCH FAILED: {exc}")
        fetch_failed = True
    try:
        announcements = fetch_announcements()
    except Exception as exc:  # noqa: BLE001
        print(f"ANNOUNCEMENTS FETCH FAILED: {exc}")
        fetch_failed = True

    if actions is None and announcements is None:
        if is_weekend_ist():
            print("NSE unreachable on a weekend: not treating as an error.")
            return 0
        return 1

    seen = load_seen()
    first_run = seen is None
    if first_run:
        seen = set()
        print("No seen.json yet: marking current items as seen without sending.")

    candidates = collect_candidates(actions, announcements)
    print(f"Tracked items found: {len(candidates)}")

    sent = 0
    failed = 0
    for key, message in candidates:
        if key in seen:
            continue
        if first_run:
            seen.add(key)
            continue
        if send_telegram(message):
            seen.add(key)
            sent += 1
            time.sleep(1.2)  # stay under Telegram's rate limit
        else:
            failed += 1

    save_seen(seen)
    print(f"Done. New alerts sent: {sent}, failed: {failed}, tracked total: {len(seen)}")

    if failed:
        return 1
    if fetch_failed and not is_weekend_ist():
        # One of the two sources was down: run is marked failed so you get the ❌ alert.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
