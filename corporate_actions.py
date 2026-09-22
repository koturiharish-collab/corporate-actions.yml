import os
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
    "Connection": "keep-alive",
}


# ============================================================
# SEEN ALERT STORAGE
# ============================================================

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(data)

        return set()

    except Exception:
        return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(seen), f, indent=2)

    except Exception as e:
        print(f"Could not save seen.json: {e}")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
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

        if response.status_code == 200:
            print("Telegram alert sent.")
            return True

        print(
            f"Telegram failed: "
            f"{response.status_code} {response.text}"
        )

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
        response = session.get(
            "https://www.nseindia.com/",
            timeout=30
        )

        print(f"NSE homepage status: {response.status_code}")

    except Exception as e:
        print(f"NSE homepage request failed: {e}")

    return session


# ============================================================
# GET NSE ANNOUNCEMENTS
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

    try:

        response = session.get(
            NSE_URL,
            params=params,
            timeout=30
        )

        print(f"NSE API status: {response.status_code}")

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):
            return data

        if isinstance(data, dict):

            for key in [
                "data",
                "announcements",
                "results"
            ]:
                if isinstance(data.get(key), list):
                    return data[key]

        return []

    except Exception as e:

        print(f"NSE request failed: {e}")

        return []


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(value):

    if value is None:
        return ""

    text = str(value)

    text = BeautifulSoup(
        text,
        "html.parser"
    ).get_text(" ", strip=True)

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


# ============================================================
# FIND VALUE RECURSIVELY
# ============================================================

def recursive_values(obj):

    if isinstance(obj, dict):

        for key, value in obj.items():

            yield key, value

            yield from recursive_values(value)

    elif isinstance(obj, list):

        for item in obj:

            yield from recursive_values(item)


# ============================================================
# FIND DATE FROM ANNOUNCEMENT
# ============================================================

def find_action_date(item):

    """
    Tries to identify the actual corporate-action date.

    Priority:
    1. Record date
    2. Ex-date
    3. Bonus date
    4. Book closure date
    5. Any relevant date field
    """

    priority_keywords = [
        "record date",
        "record_date",
        "recordDate",
        "ex date",
        "ex-date",
        "ex_date",
        "bonus date",
        "bonus_date",
        "book closure",
        "book_closure",
        "effective date",
        "effective_date",
        "date of action",
        "action date",
    ]

    possible_dates = []

    for key, value in recursive_values(item):

        key_text = clean_text(key).lower()
        value_text = clean_text(value)

        if not value_text:
            continue

        for keyword in priority_keywords:

            if keyword.lower() in key_text:

                date_match = re.search(
                    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
                    value_text
                )

                if date_match:
                    possible_dates.append(
                        (
                            priority_keywords.index(keyword),
                            date_match.group(0)
                        )
                    )

                else:

                    date_match = re.search(
                        r"\b\d{1,2}\s+"
                        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                        r"[a-z]*\s+\d{4}\b",
                        value_text,
                        re.IGNORECASE
                    )

                    if date_match:
                        possible_dates.append(
                            (
                                priority_keywords.index(keyword),
                                date_match.group(0)
                            )
                        )

    if possible_dates:

        possible_dates.sort(
            key=lambda x: x[0]
        )

        return possible_dates[0][1]

    # --------------------------------------------------------
    # Search complete announcement text
    # --------------------------------------------------------

    complete_text = " ".join(
        clean_text(value)
        for _, value in recursive_values(item)
    )

    patterns = [

        r"(?:record\s*date)"
        r"\s*[:\-]?\s*"
        r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",

        r"(?:ex\s*date|ex-date)"
        r"\s*[:\-]?\s*"
        r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",

        r"(?:record\s*date)"
        r"\s*[:\-]?\s*"
        r"(\d{1,2}\s+"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"[a-z]*\s+\d{4})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            complete_text,
            re.IGNORECASE
        )

        if match:
            return match.group(1)

    return ""


# ============================================================
# IDENTIFY CORPORATE ACTION
# ============================================================

def identify_action(item):

    text_parts = []

    for key, value in recursive_values(item):

        if isinstance(value, (str, int, float)):

            text_parts.append(
                clean_text(value)
            )

    text = " ".join(text_parts)

    text_lower = text.lower()

    # --------------------------------------------------------
    # BONUS
    # --------------------------------------------------------

    if "bonus" in text_lower:

        return "BONUS"

    # --------------------------------------------------------
    # STOCK SPLIT
    # --------------------------------------------------------

    if (
        "stock split" in text_lower
        or "split" in text_lower
        or "sub-division" in text_lower
        or "sub division" in text_lower
    ):

        return "STOCK SPLIT"

    # --------------------------------------------------------
    # RIGHTS ISSUE
    # --------------------------------------------------------

    if "rights issue" in text_lower:

        return "RIGHTS ISSUE"

    # --------------------------------------------------------
    # DIVIDEND
    # --------------------------------------------------------

    if "dividend" in text_lower:

        return "DIVIDEND"

    # --------------------------------------------------------
    # BUYBACK
    # --------------------------------------------------------

    if "buyback" in text_lower or "buy back" in text_lower:

        return "BUYBACK"

    return ""


# ============================================================
# GET SYMBOL
# ============================================================

def get_symbol(item):

    possible_keys = [
        "symbol",
        "Symbol",
        "ticker",
        "Ticker",
        "securitySymbol",
        "security_symbol"
    ]

    for key, value in recursive_values(item):

        if key in possible_keys:

            value = clean_text(value)

            if value:
                return value

    return ""


# ============================================================
# GET COMPANY NAME
# ============================================================

def get_company(item):

    possible_keys = [
        "companyName",
        "company_name",
        "Company Name",
        "company",
        "Company",
        "name",
        "Name"
    ]

    for key, value in recursive_values(item):

        if key in possible_keys:

            value = clean_text(value)

            if value:
                return value

    return ""


# ============================================================
# GET ANNOUNCEMENT ID
# ============================================================

def get_announcement_id(item):

    possible_keys = [
        "id",
        "announcementId",
        "announcement_id",
        "seqId",
        "seq_id",
        "attchmntText",
        "attachment",
        "fileName",
        "file_name"
    ]

    for key, value in recursive_values(item):

        if key in possible_keys:

            value = clean_text(value)

            if value:
                return value

    # fallback: entire item hash-like string

    return json.dumps(
        item,
        sort_keys=True
    )


# ============================================================
# GET LINK
# ============================================================

def get_link(item):

    possible_keys = [
        "attchmntFile",
        "attachment",
        "attachmentUrl",
        "attachment_url",
        "url",
        "link",
        "fileUrl",
        "file_url"
    ]

    for key, value in recursive_values(item):

        if key in possible_keys:

            value = clean_text(value)

            if value.startswith("http"):

                return value

    return ""


# ============================================================
# NORMALIZE DATE
# ============================================================

def normalize_date(date_text):

    if not date_text:
        return ""

    date_text = date_text.strip()

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d-%m-%y",
        "%d/%m/%y",
        "%d %b %Y",
        "%d %B %Y",
    ]

    for fmt in formats:

        try:

            dt = datetime.strptime(
                date_text,
                fmt
            )

            return dt.strftime(
                "%d-%b-%Y"
            )

        except ValueError:
            pass

    return date_text


# ============================================================
# FORMAT TELEGRAM MESSAGE
# ============================================================

def format_message(
    company,
    symbol,
    action,
    action_date,
    link
):

    if action == "BONUS":

        title = "🎁 FRESH BONUS"

    elif action == "STOCK SPLIT":

        title = "✂️ FRESH STOCK SPLIT"

    else:

        title = f"📢 FRESH {action}"

    message = (
        f"{title}\n\n"
        f"🏢 Company: {company or 'N/A'}\n"
        f"📌 Symbol: {symbol or 'N/A'}\n"
        f"📅 Action Date: "
        f"{action_date or 'Not available'}\n"
    )

    if link:

        message += (
            f"\n🔗 NSE Announcement:\n"
            f"{link}\n"
        )

    message += (
        "\n🤖 NSE Fresh Corporate Action Scanner"
    )

    return message


# ============================================================
# CREATE UNIQUE ACTION KEY
# ============================================================

def create_action_key(
    symbol,
    action,
    action_date
):

    return (
        f"{symbol.upper().strip()}|"
        f"{action.upper().strip()}|"
        f"{action_date.strip()}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("NSE FRESH BONUS & STOCK SPLIT SCANNER")
    print("=" * 60)

    seen = load_seen()

    print(
        f"Previously alerted: {len(seen)}"
    )

    announcements = get_announcements()

    print(
        f"Announcements received: "
        f"{len(announcements)}"
    )

    if not announcements:

        print("No announcements received.")

        save_seen(seen)

        return

    sent_this_run = set()

    fresh_count = 0

    for item in announcements:

        action = identify_action(item)

        # We only want bonus and stock split.
        if action not in [
            "BONUS",
            "STOCK SPLIT"
        ]:
            continue

        symbol = get_symbol(item)

        company = get_company(item)

        announcement_id = get_announcement_id(item)

        link = get_link(item)

        action_date = find_action_date(item)

        action_date = normalize_date(
            action_date
        )

        # ----------------------------------------------------
        # UNIQUE KEY
        # ----------------------------------------------------

        action_key = create_action_key(
            symbol,
            action,
            action_date
        )

        # ----------------------------------------------------
        # OLD ANNOUNCEMENT
        # ----------------------------------------------------

        if announcement_id in seen:

            print(
                f"Already sent announcement: "
                f"{symbol}"
            )

            continue

        # ----------------------------------------------------
        # DUPLICATE SAME ACTION
        # ----------------------------------------------------

        if action_key in sent_this_run:

            print(
                f"Duplicate ignored: "
                f"{action_key}"
            )

            continue

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        message = format_message(
            company=company,
            symbol=symbol,
            action=action,
            action_date=action_date,
            link=link
        )

        print("\n" + "-" * 60)

        print(message)

        print("-" * 60)

        success = send_telegram(
            message
        )

        if success:

            seen.add(announcement_id)

            sent_this_run.add(
                action_key
            )

            fresh_count += 1

    save_seen(seen)

    print("\n" + "=" * 60)

    print(
        f"Fresh alerts sent: {fresh_count}"
    )

    print("=" * 60)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
