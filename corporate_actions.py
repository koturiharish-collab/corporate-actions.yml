#!/usr/bin/env python3
"""NSE corporate actions alert: stock splits, bonus issues and buybacks -> Telegram.

Env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
State file: seen.json (keeps alerts from being sent twice)
"""
import json
import os
import sys
import time
from datetime import date, timedelta

import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE = "seen.json"

LOOKBACK_DAYS = 2      # also look at announcements from the last couple of days
LOOKAHEAD_DAYS = 90    # upcoming ex-dates window

NSE_HOME = "https://www.nseindia.com"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
NSE_API = "https://www.nseindia.com/api/corporates-corporateActions"

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


def classify(subject):
    """Return SPLIT / BONUS / BUYBACK, or None if the action is not one we track."""
    s = (subject or "").lower()
    if "split" in s or "sub division" in s or "sub-division" in s:
        return "SPLIT"
    if "bonus" in s:
        return "BONUS"
    if "buy back" in s or "buyback" in s or "buy-back" in s:
        return "BUYBACK"
    return None


def fetch_actions():
    """Download corporate actions from NSE (retries, since NSE is flaky)."""
    frm = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%m-%Y")
    to = (date.today() + timedelta(days=LOOKAHEAD_DAYS)).strftime("%d-%m-%Y")
    last_err = None
    for attempt in range(1, 4):
        try:
            session = requests.Session()
            session.headers.update(HEADERS)
            # NSE needs cookies from a normal page visit before the API works.
            session.get(NSE_HOME, timeout=20)
            session.get(NSE_PAGE, timeout=20)
            resp = session.get(
                NSE_API,
                params={"index": "equities", "from_date": frm, "to_date": to},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                data = data.get("data", [])
            if not isinstance(data, list):
                raise ValueError("Unexpected NSE response format")
            print(f"Fetched {len(data)} corporate actions ({frm} to {to})")
            return data
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"Attempt {attempt} failed: {exc}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"Could not fetch NSE corporate actions: {last_err}")


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
                "text": text,
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


def build_message(kind, item):
    symbol = item.get("symbol", "?")
    company = item.get("comp", "")
    subject = (item.get("subject") or "").strip()
    ex_date = item.get("exDate") or "-"
    rec_date = item.get("recDate") or "-"
    face_val = item.get("faceVal")
    lines = [
        TITLES[kind],
        "",
        f"📊 Stock: {symbol}",
    ]
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


def main():
    actions = fetch_actions()

    seen = load_seen()
    first_run = seen is None
    if first_run:
        seen = set()
        print("No seen.json yet: marking current actions as seen without sending.")

    sent = 0
    failed = 0
    for item in actions:
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
        if key in seen:
            continue

        if first_run:
            seen.add(key)
            continue

        if send_telegram(build_message(kind, item)):
            seen.add(key)
            sent += 1
            time.sleep(1.2)  # stay under Telegram's rate limit
        else:
            failed += 1

    save_seen(seen)
    print(f"Done. New alerts sent: {sent}, failed: {failed}, tracked total: {len(seen)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
