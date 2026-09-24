import os
import json
import re
import hashlib
import requests

from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    ""
)

# IMPORTANT:
# This MUST match the GitHub Actions state file.
SEEN_FILE = "seen.json"

# India timezone
IST = ZoneInfo("Asia/Kolkata")

# NSE regular market timing
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)

NSE_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
)

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
# INDIA TIME
# ============================================================

def now_ist():
    return datetime.now(IST)


def is_nse_market_time():

    current = now_ist()

    # Saturday / Sunday
    if current.weekday() >= 5:
        return False

    current_time = current.time()

    return (
        MARKET_OPEN
        <= current_time
        < MARKET_CLOSE
    )


# ============================================================
# SEEN STATE
# ============================================================

def load_seen():

    if not os.path.exists(SEEN_FILE):

        return {
            "initialized": False,
            "keys": set()
        }

    try:

        with open(
            SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        # New format
        if isinstance(data, dict):

            keys = data.get(
                "keys",
                []
            )

            if isinstance(keys, list):

                return {
                    "initialized": bool(
                        data.get(
                            "initialized",
                            False
                        )
                    ),
                    "keys": set(keys)
                }

        # Old format compatibility
        if isinstance(data, list):

            return {
                "initialized": True,
                "keys": set(data)
            }

    except Exception as e:

        print(
            f"Could not load seen.json: {e}"
        )

    return {
        "initialized": False,
        "keys": set()
    }


def save_seen(state):

    try:

        data = {
            "initialized": bool(
                state.get(
                    "initialized",
                    False
                )
            ),
            "keys": sorted(
                list(
                    state.get(
                        "keys",
                        set()
                    )
                )
            )
        }

        with open(
            SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2
            )

        print(
            f"Saved state: "
            f"{len(data['keys'])} keys"
        )

    except Exception as e:

        print(
            f"Could not save seen.json: {e}"
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials are missing."
        )

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        if response.status_code == 200:

            print(
                "Telegram alert sent."
            )

            return True

        print(
            f"Telegram failed: "
            f"{response.status_code} "
            f"{response.text}"
        )

    except Exception as e:

        print(
            f"Telegram error: {e}"
        )

    return False


# ============================================================
# NSE SESSION
# ============================================================

def create_nse_session():

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    try:

        response = session.get(
            "https://www.nseindia.com/",
            timeout=30
        )

        print(
            f"NSE homepage status: "
            f"{response.status_code}"
        )

    except Exception as e:

        print(
            f"NSE homepage request failed: {e}"
        )

    return session


# ============================================================
# GET NSE ANNOUNCEMENTS
# ============================================================

def get_announcements():

    session = create_nse_session()

    # IMPORTANT:
    # NSE dates are based on Indian calendar date.
    today = now_ist().date()

    yesterday = (
        today - timedelta(days=1)
    )

    params = {
        "index": "equities",
        "from_date": yesterday.strftime(
            "%d-%m-%Y"
        ),
        "to_date": today.strftime(
            "%d-%m-%Y"
        ),
    }

    try:

        response = session.get(
            NSE_URL,
            params=params,
            timeout=30
        )

        print(
            f"NSE API status: "
            f"{response.status_code}"
        )

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

                if isinstance(
                    data.get(key),
                    list
                ):

                    return data[key]

        return []

    except Exception as e:

        print(
            f"NSE request failed: {e}"
        )

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
    ).get_text(
        " ",
        strip=True
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


# ============================================================
# RECURSIVE VALUES
# ============================================================

def recursive_values(obj):

    if isinstance(
        obj,
        dict
    ):

        for key, value in obj.items():

            yield key, value

            yield from recursive_values(
                value
            )

    elif isinstance(
        obj,
        list
    ):

        for item in obj:

            yield from recursive_values(
                item
            )


# ============================================================
# FIND ACTION DATE
# ============================================================

def find_action_date(item):

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
        "action date"
    ]

    possible_dates = []

    for key, value in recursive_values(
        item
    ):

        key_text = clean_text(
            key
        ).lower()

        value_text = clean_text(
            value
        )

        if not value_text:

            continue

        for priority, keyword in enumerate(
            priority_keywords
        ):

            if keyword.lower() in key_text:

                match = re.search(
                    r"\b\d{1,2}[-/]"
                    r"\d{1,2}[-/]"
                    r"\d{2,4}\b",
                    value_text
                )

                if match:

                    possible_dates.append(
                        (
                            priority,
                            match.group(0)
                        )
                    )

                else:

                    match = re.search(
                        r"\b\d{1,2}\s+"
                        r"(?:Jan|Feb|Mar|Apr|May|Jun|"
                        r"Jul|Aug|Sep|Oct|Nov|Dec)"
                        r"[a-z]*\s+\d{4}\b",
                        value_text,
                        re.IGNORECASE
                    )

                    if match:

                        possible_dates.append(
                            (
                                priority,
                                match.group(0)
                            )
                        )

    if possible_dates:

        possible_dates.sort(
            key=lambda x: x[0]
        )

        return possible_dates[0][1]

    # Search complete announcement text
    complete_text = " ".join(
        clean_text(value)
        for _, value in recursive_values(
            item
        )
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
        r"[a-z]*\s+\d{4})"
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
# IDENTIFY ACTION
# ============================================================

def identify_action(item):

    text_parts = []

    for key, value in recursive_values(
        item
    ):

        if isinstance(
            value,
            (str, int, float)
        ):

            text_parts.append(
                clean_text(value)
            )

    text = " ".join(
        text_parts
    )

    text_lower = text.lower()

    # BONUS
    if "bonus" in text_lower:

        return "BONUS"

    # STOCK SPLIT
    if (
        "stock split" in text_lower
        or "sub-division" in text_lower
        or "sub division" in text_lower
    ):

        return "STOCK SPLIT"

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

    for key, value in recursive_values(
        item
    ):

        if key in possible_keys:

            value = clean_text(
                value
            )

            if value:

                return value

    return ""


# ============================================================
# GET COMPANY
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

    for key, value in recursive_values(
        item
    ):

        if key in possible_keys:

            value = clean_text(
                value
            )

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

    for key, value in recursive_values(
        item
    ):

        if key in possible_keys:

            value = clean_text(
                value
            )

            if value:

                return value

    return ""


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

    for key, value in recursive_values(
        item
    ):

        if key in possible_keys:

            value = clean_text(
                value
            )

            if value.startswith(
                "http"
            ):

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
        "%d %B %Y"
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
# CREATE STABLE UNIQUE KEY
# ============================================================

def create_unique_key(
    item,
    symbol,
    action,
    action_date,
    announcement_id,
    link
):

    # BEST KEY = NSE announcement URL

    if link:

        raw = (
            "URL|"
            + link.strip().lower()
        )

    # SECOND BEST = NSE announcement ID

    elif announcement_id:

        raw = (
            "ID|"
            + announcement_id.strip()
        )

    # FALLBACK = complete announcement

    else:

        raw = json.dumps(
            item,
            sort_keys=True,
            default=str
        )

        raw = (
            "HASH|"
            + raw
        )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()

    return digest


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

        f"🏢 Company: "
        f"{company or 'N/A'}\n"

        f"📌 Symbol: "
        f"{symbol or 'N/A'}\n"

        f"📅 Action Date: "
        f"{action_date or 'Not available'}\n"
    )

    if link:

        message += (
            "\n🔗 NSE Announcement:\n"
            f"{link}\n"
        )

    message += (
        "\n🇮🇳 Timezone: Asia/Kolkata"
        "\n🤖 NSE Fresh Corporate Action Scanner"
    )

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)

    print(
        "NSE FRESH BONUS & STOCK SPLIT SCANNER"
    )

    print("=" * 65)

    current_ist = now_ist()

    print(
        "India Time:",
        current_ist.strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    )

    print(
        "Market Status:",
        "OPEN"
        if is_nse_market_time()
        else "CLOSED"
    )

    print(
        "NSE Market Window:",
        "09:15 - 15:30 IST"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # IMPORTANT:
    # Only scan during NSE regular market hours.
    # --------------------------------------------------------

    if not is_nse_market_time():

        print(
            "Outside NSE market hours."
        )

        print(
            "No corporate-action scan performed."
        )

        return

    state = load_seen()

    seen = state["keys"]

    initialized = state["initialized"]

    print(
        f"Previously stored alerts: "
        f"{len(seen)}"
    )

    print(
        f"Scanner initialized: "
        f"{initialized}"
    )

    announcements = get_announcements()

    print(
        f"Announcements received: "
        f"{len(announcements)}"
    )

    if not announcements:

        print(
            "No announcements received."
        )

        save_seen(state)

        return

    eligible = []

    # ========================================================
    # PROCESS ANNOUNCEMENTS
    # ========================================================

    for item in announcements:

        action = identify_action(
            item
        )

        if action not in [
            "BONUS",
            "STOCK SPLIT"
        ]:

            continue

        symbol = get_symbol(
            item
        )

        company = get_company(
            item
        )

        announcement_id = (
            get_announcement_id(item)
        )

        link = get_link(
            item
        )

        action_date = find_action_date(
            item
        )

        action_date = normalize_date(
            action_date
        )

        unique_key = create_unique_key(
            item=item,
            symbol=symbol,
            action=action,
            action_date=action_date,
            announcement_id=announcement_id,
            link=link
        )

        eligible.append(
            (
                item,
                action,
                symbol,
                company,
                announcement_id,
                link,
                action_date,
                unique_key
            )
        )

    # ========================================================
    # FIRST RUN = BUILD BASELINE
    # ========================================================

    if not initialized:

        print(
            "First run detected."
        )

        print(
            "Creating baseline."
        )

        for (
            item,
            action,
            symbol,
            company,
            announcement_id,
            link,
            action_date,
            unique_key
        ) in eligible:

            seen.add(
                unique_key
            )

        state["keys"] = seen

        state["initialized"] = True

        save_seen(
            state
        )

        print(
            f"Baseline created with "
            f"{len(eligible)} existing announcements."
        )

        print(
            "No old announcements sent."
        )

        return

    # ========================================================
    # NORMAL HOURLY SCAN
    # ========================================================

    fresh_count = 0

    for (
        item,
        action,
        symbol,
        company,
        announcement_id,
        link,
        action_date,
        unique_key
    ) in eligible:

        # ----------------------------------------------------
        # ALREADY SENT
        # ----------------------------------------------------

        if unique_key in seen:

            print(
                f"Already alerted - SKIP: "
                f"{symbol} | {action}"
            )

            continue

        # ----------------------------------------------------
        # NEW ALERT
        # ----------------------------------------------------

        message = format_message(
            company=company,
            symbol=symbol,
            action=action,
            action_date=action_date,
            link=link
        )

        print(
            "\n" + "-" * 65
        )

        print(
            "NEW FRESH ALERT:"
        )

        print(
            message
        )

        print(
            "-" * 65
        )

        success = send_telegram(
            message
        )

        if success:

            # Save immediately after successful Telegram
            # delivery.

            seen.add(
                unique_key
            )

            state["keys"] = seen

            state["initialized"] = True

            save_seen(
                state
            )

            fresh_count += 1

        else:

            print(
                "Telegram failed."
            )

            print(
                "Alert NOT marked as sent."
            )

    # ========================================================
    # FINAL SAVE
    # ========================================================

    state["keys"] = seen

    state["initialized"] = True

    save_seen(
        state
    )

    print(
        "\n" + "=" * 65
    )

    print(
        f"Fresh alerts sent: "
        f"{fresh_count}"
    )

    print("=" * 65)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
