import os
import json
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SEEN_FILE = "seen.json"

NSE_URL = "https://www.nseindia.com/api/corporate-announcements"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


# ============================================================
# INDIA DATE
# ============================================================

def india_today():
    return datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).date()


# ============================================================
# LOAD SEEN
# ============================================================

def load_seen():

    if not os.path.exists(SEEN_FILE):
        return set()

    try:

        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, list):
            return set(data)

        return set()

    except Exception as e:

        print(f"Could not load seen.json: {e}")

        return set()


# ============================================================
# SAVE SEEN
# ============================================================

def save_seen(seen):

    try:

        with open(
            SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                sorted(seen),
                f,
                indent=2
            )

    except Exception as e:

        print(f"Could not save seen.json: {e}")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID is missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        print(
            f"Telegram HTTP status: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        result = response.json()

        if result.get("ok"):

            print("Telegram message sent.")
            return True

        print(f"Telegram rejected message: {result}")

        return False

    except Exception as e:

        print(f"Telegram error: {e}")

        return False


# ============================================================
# NSE SESSION
# ============================================================

def create_nse_session():

    session = requests.Session()

    session.headers.update(HEADERS)

    try:

        print("Opening NSE homepage...")

        response = session.get(
            "https://www.nseindia.com/",
            timeout=30
        )

        print(
            "NSE homepage status:",
            response.status_code
        )

        print(
            "NSE homepage content type:",
            response.headers.get("Content-Type")
        )

        # Do not fail here.
        # NSE may return different content depending
        # on GitHub's IP.

    except requests.RequestException as e:

        print(
            f"NSE homepage request failed: {e}"
        )

    return session


# ============================================================
# GET NSE ANNOUNCEMENTS
# ============================================================

def get_announcements():

    session = create_nse_session()

    today = india_today()

    date_string = today.strftime("%d-%m-%Y")

    params = {
        "index": "equities",
        "from_date": date_string,
        "to_date": date_string,
    }

    print(
        f"Checking NSE announcements for "
        f"{date_string}"
    )

    try:

        response = session.get(
            NSE_URL,
            params=params,
            timeout=30
        )

        print(
            "NSE API status:",
            response.status_code
        )

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        print(
            "NSE API content type:",
            content_type
        )

        # ----------------------------------------------------
        # HTTP ERROR
        # ----------------------------------------------------

        if response.status_code != 200:

            print(
                "NSE API did not return HTTP 200."
            )

            print(
                "Response preview:",
                response.text[:500]
            )

            return None

        # ----------------------------------------------------
        # EMPTY RESPONSE
        # ----------------------------------------------------

        if not response.text.strip():

            print(
                "NSE returned an empty response."
            )

            return None

        # ----------------------------------------------------
        # JSON CHECK
        # ----------------------------------------------------

        try:

            data = response.json()

        except ValueError:

            print(
                "NSE returned NON-JSON data."
            )

            print(
                "Response preview:"
            )

            print(
                response.text[:1000]
            )

            return None

        # ----------------------------------------------------
        # RESPONSE FORMAT
        # ----------------------------------------------------

        if isinstance(data, list):

            return data

        if isinstance(data, dict):

            announcements = data.get(
                "data",
                []
            )

            if isinstance(
                announcements,
                list
            ):

                return announcements

        print(
            "Unexpected NSE response format."
        )

        return None

    except requests.RequestException as e:

        print(
            f"NSE request failed: {e}"
        )

        return None

    except Exception as e:

        print(
            f"Unexpected NSE error: {e}"
        )

        return None


# ============================================================
# IDENTIFY BONUS / SPLIT
# ============================================================

def identify_action(announcement):

    text_parts = []

    fields = [
        "desc",
        "description",
        "subject",
        "details",
        "headline",
        "attchmntText",
        "announcement",
        "purpose",
    ]

    for field in fields:

        value = announcement.get(field)

        if value:

            text_parts.append(
                str(value)
            )

    text = " ".join(text_parts)

    text = re.sub(
        r"\s+",
        " ",
        text
    ).lower()

    # BONUS

    bonus_patterns = [
        r"\bbonus\b",
        r"\bbonus issue\b",
        r"\bbonus shares\b",
        r"\bissue of bonus\b",
    ]

    for pattern in bonus_patterns:

        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):

            return "BONUS"

    # STOCK SPLIT

    split_patterns = [
        r"\bstock split\b",
        r"\bshare split\b",
        r"\bsub[\s-]?division\b",
        r"\bsub[\s-]?division of shares\b",
        r"\bsplit\b.*\bface value\b",
        r"\bface value\b.*\bsplit\b",
    ]

    for pattern in split_patterns:

        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):

            return "STOCK SPLIT"

    return None


# ============================================================
# UNIQUE ID
# ============================================================

def announcement_id(announcement):

    # First use NSE ID if available.

    for field in [
        "seq_id",
        "seqId",
        "id",
    ]:

        value = announcement.get(field)

        if value:

            return (
                f"{field}:{value}"
            )

    symbol = str(
        announcement.get(
            "symbol",
            ""
        )
    )

    company = str(
        announcement.get(
            "companyName",
            ""
        )
    )

    description = str(
        announcement.get(
            "desc",
            ""
        )
        or announcement.get(
            "description",
            ""
        )
        or announcement.get(
            "subject",
            ""
        )
    )

    date_value = str(
        announcement.get(
            "broadcastDate",
            ""
        )
        or announcement.get(
            "date",
            ""
        )
    )

    return "|".join(
        [
            symbol.strip(),
            company.strip(),
            date_value.strip(),
            description.strip().lower(),
        ]
    )


# ============================================================
# FORMAT MESSAGE
# ============================================================

def format_message(
    action,
    announcement
):

    symbol = (
        announcement.get("symbol")
        or announcement.get("symbolName")
        or "Unknown"
    )

    company = (
        announcement.get("companyName")
        or symbol
        or "Unknown company"
    )

    description = (
        announcement.get("desc")
        or announcement.get("description")
        or announcement.get("subject")
        or announcement.get("details")
        or announcement.get("headline")
        or "Corporate action announcement"
    )

    date_value = (
        announcement.get(
            "broadcastDate"
        )
        or announcement.get(
            "date"
        )
        or ""
    )

    attachment = (
        announcement.get(
            "attchmntFile"
        )
        or announcement.get(
            "attachment"
        )
        or ""
    )

    message = (
        f"🚨 FRESH {action}\n\n"
        f"🏢 Company: {company}\n"
        f"📌 Symbol: {symbol}\n"
        f"📅 Date: {date_value}\n\n"
        f"📝 Details:\n{description}"
    )

    if attachment:

        attachment = str(
            attachment
        )

        if attachment.startswith(
            "http"
        ):

            message += (
                f"\n\n🔗 {attachment}"
            )

        else:

            message += (
                "\n\n🔗 "
                "https://www.nseindia.com/"
                + attachment.lstrip("/")
            )

    message += (
        "\n\n🤖 NSE Fresh Corporate "
        "Action Scanner"
    )

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "NSE FRESH BONUS & STOCK SPLIT SCANNER"
    )

    print("=" * 60)

    print(
        f"India date: {india_today()}"
    )

    seen = load_seen()

    print(
        f"Previously alerted: {len(seen)}"
    )

    announcements = get_announcements()

    # ========================================================
    # VERY IMPORTANT:
    # None means NSE FAILED.
    #
    # [] means NSE successfully returned zero announcements.
    # ========================================================

    if announcements is None:

        print()
        print(
            "NSE SCAN FAILED."
        )

        print(
            "No alerts will be sent."
        )

        print(
            "Seen list will NOT be changed."
        )

        print(
            "Scanner finished with NSE error."
        )

        return

    print(
        f"Announcements received: "
        f"{len(announcements)}"
    )

    if not announcements:

        print(
            "No announcements for today."
        )

        print(
            "Scanner finished."
        )

        return

    new_alerts = 0

    for announcement in announcements:

        action = identify_action(
            announcement
        )

        if not action:

            continue

        ann_id = announcement_id(
            announcement
        )

        symbol = (
            announcement.get(
                "symbol"
            )
            or announcement.get(
                "symbolName"
            )
            or "Unknown"
        )

        print()
        print(
            f"Detected {action}: {symbol}"
        )

        # ====================================================
        # DUPLICATE PROTECTION
        # ====================================================

        if ann_id in seen:

            print(
                "Already alerted - SKIPPING."
            )

            continue

        # ====================================================
        # NEW ALERT
        # ====================================================

        message = format_message(
            action,
            announcement
        )

        print(
            "NEW FRESH ALERT"
        )

        print(message)

        sent = send_telegram(
            message
        )

        if sent:

            seen.add(
                ann_id
            )

            new_alerts += 1

            print(
                "Saved as alerted."
            )

        else:

            print(
                "Telegram failed."
            )

            print(
                "NOT marking as alerted."
            )

    # ========================================================
    # SAVE ONLY AFTER SUCCESSFUL PROCESSING
    # ========================================================

    save_seen(seen)

    print()
    print("=" * 60)

    print(
        f"NEW ALERTS SENT: {new_alerts}"
    )

    print(
        f"TOTAL SEEN ALERTS: {len(seen)}"
    )

    print("=" * 60)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
