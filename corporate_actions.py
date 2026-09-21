import os
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

import requests


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SEEN_FILE = "seen.json"

NSE_URL = "https://www.nseindia.com/api/corporate-announcements"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


# ============================================================
# LOAD / SAVE SEEN ALERTS
# ============================================================

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return set(data)

    except Exception:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing."
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    response = requests.post(
        url,
        json=payload,
        timeout=30
    )

    response.raise_for_status()

    print("Telegram alert sent.")


# ============================================================
# NSE SESSION
# ============================================================

def create_nse_session():
    session = requests.Session()

    session.headers.update(HEADERS)

    # First visit NSE homepage to obtain cookies
    response = session.get(
        "https://www.nseindia.com/",
        timeout=30
    )

    response.raise_for_status()

    return session


# ============================================================
# GET NSE CORPORATE ANNOUNCEMENTS
# ============================================================

def get_announcements():

    session = create_nse_session()

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    params = {
        "index": "equities",
        "from_date": yesterday.strftime("%d-%m-%Y"),
        "to_date": today.strftime("%d-%m-%Y"),
    }

    response = session.get(
        NSE_URL,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):
        announcements = data.get("data", [])
    elif isinstance(data, list):
        announcements = data
    else:
        announcements = []

    return announcements


# ============================================================
# IDENTIFY BONUS / SPLIT
# ============================================================

def identify_action(item):

    text_parts = []

    for key in [
        "desc",
        "description",
        "subject",
        "purpose",
        "details",
        "announcement",
    ]:
        value = item.get(key)

        if value:
            text_parts.append(str(value))

    text = " ".join(text_parts).upper()

    # Remove unnecessary whitespace
    text = re.sub(r"\s+", " ", text)

    if "BONUS" in text:
        return "BONUS ISSUE"

    if "STOCK SPLIT" in text:
        return "STOCK SPLIT"

    if "SUB-DIVISION" in text or "SUBDIVISION" in text:
        return "STOCK SPLIT"

    if "SPLIT" in text and "SHARE" in text:
        return "STOCK SPLIT"

    return None


# ============================================================
# CREATE UNIQUE ID
# ============================================================

def announcement_id(item):

    values = []

    for key in [
        "symbol",
        "sm_name",
        "subject",
        "desc",
        "description",
        "purpose",
        "dt",
        "date",
        "seq_id",
        "id",
    ]:
        value = item.get(key)

        if value is not None:
            values.append(str(value))

    raw = "|".join(values)

    # Stable simple ID
    import hashlib

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# FORMAT TELEGRAM MESSAGE
# ============================================================

def format_message(item, action):

    symbol = (
        item.get("symbol")
        or item.get("sm_name")
        or "Unknown"
    )

    company = (
        item.get("sm_name")
        or item.get("companyName")
        or symbol
    )

    subject = (
        item.get("subject")
        or item.get("desc")
        or item.get("description")
        or item.get("purpose")
        or "Corporate action announced"
    )

    date_value = (
        item.get("dt")
        or item.get("date")
        or ""
    )

    record_date = (
        item.get("recordDate")
        or item.get("record_date")
        or ""
    )

    ex_date = (
        item.get("exDate")
        or item.get("ex_date")
        or ""
    )

    message = (
        "🚨 FRESH CORPORATE ACTION\n\n"
        f"🏢 Company: {company}\n"
        f"📊 Symbol: {symbol}\n"
        f"📌 Action: {action}\n\n"
        f"📝 Details: {subject}\n"
    )

    if date_value:
        message += f"📅 Announcement: {date_value}\n"

    if record_date:
        message += f"📅 Record Date: {record_date}\n"

    if ex_date:
        message += f"📅 Ex Date: {ex_date}\n"

    message += (
        "\n🔗 Source: NSE India\n"
        "⏰ Detected by hourly scanner"
    )

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("NSE BONUS & STOCK SPLIT SCANNER")
    print("=" * 60)

    seen = load_seen()

    print(f"Previously alerted: {len(seen)}")

    try:
        announcements = get_announcements()

    except Exception as e:
        print(f"NSE request failed: {e}")
        raise

    print(f"NSE announcements received: {len(announcements)}")

    new_alerts = 0

    for item in announcements:

        action = identify_action(item)

        # Ignore everything except BONUS and SPLIT
        if not action:
            continue

        alert_id = announcement_id(item)

        # Already alerted
        if alert_id in seen:
            continue

        message = format_message(
            item,
            action
        )

        print("\nNEW ALERT:")
        print(message)

        try:
            send_telegram(message)

            # Mark as seen only AFTER successful Telegram delivery
            seen.add(alert_id)

            new_alerts += 1

        except Exception as e:
            print(
                f"Telegram failed for alert: {e}"
            )

    save_seen(seen)

    print("\n" + "=" * 60)
    print(f"New alerts sent: {new_alerts}")
    print(f"Total stored alerts: {len(seen)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
