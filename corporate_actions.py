import os
import json
import re
import hashlib
import warnings
import requests

from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from bs4 import MarkupResemblesLocatorWarning


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

SEEN_FILE = "seen.json"

IST = ZoneInfo("Asia/Kolkata")

# NSE normal equity market
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)

NSE_HOME = "https://www.nseindia.com/"

NSE_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/plain,*/*"
    ),
    "Accept-Language": (
        "en-US,en;q=0.9"
    ),
    "Referer": NSE_HOME,
    "Connection": "keep-alive",
}


# ============================================================
# REMOVE BEAUTIFULSOUP WARNING
# ============================================================

warnings.filterwarnings(
    "ignore",
    category=MarkupResemblesLocatorWarning
)


# ============================================================
# INDIA TIME
# ============================================================

def now_ist():

    return datetime.now(IST)


def is_nse_market_time():

    current = now_ist()

    # Monday = 0
    # Friday = 4
    # Saturday/Sunday = 5/6

    if current.weekday() >= 5:

        return False

    current_time = current.time()

    return (
        MARKET_OPEN
        <= current_time
        <= MARKET_CLOSE
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
        ) as file:

            data = json.load(file)

        # New format
        if isinstance(data, dict):

            keys = data.get(
                "keys",
                []
            )

            if not isinstance(
                keys,
                list
            ):

                keys = []

            return {
                "initialized": bool(
                    data.get(
                        "initialized",
                        False
                    )
                ),
                "keys": set(keys)
            }

        # Old format: simple list
        if isinstance(data, list):

            return {
                "initialized": True,
                "keys": set(data)
            }

    except Exception as error:

        print(
            "ERROR loading seen.json:",
            error
        )

    return {
        "initialized": False,
        "keys": set()
    }


def save_seen(state):

    keys = state.get(
        "keys",
        set()
    )

    data = {
        "initialized": bool(
            state.get(
                "initialized",
                False
            )
        ),
        "keys": sorted(
            list(keys)
        )
    }

    try:

        with open(
            SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                indent=2
            )

        print(
            "Saved state:",
            len(keys),
            "keys"
        )

    except Exception as error:

        print(
            "ERROR saving seen.json:",
            error
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "ERROR: TELEGRAM_CHAT_ID missing."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
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

        print(
            "Telegram status:",
            response.status_code
        )

        if response.ok:

            print(
                "Telegram alert sent successfully."
            )

            return True

        print(
            "Telegram response:",
            response.text[:500]
        )

    except Exception as error:

        print(
            "Telegram error:",
            error
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
            NSE_HOME,
            timeout=30
        )

        print(
            "NSE homepage status:",
            response.status_code
        )

    except Exception as error:

        print(
            "NSE homepage request failed:",
            error
        )

    return session


# ============================================================
# GET NSE ANNOUNCEMENTS
# ============================================================

def get_announcements():

    session = create_nse_session()

    # IMPORTANT:
    # NSE date is based on Indian calendar.
    current_date = now_ist().date()

    previous_date = (
        current_date
        - timedelta(days=1)
    )

    params = {
        "index": "equities",
        "from_date": previous_date.strftime(
            "%d-%m-%Y"
        ),
        "to_date": current_date.strftime(
            "%d-%m-%Y"
        )
    }

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

        response.raise_for_status()

        data = response.json()

        if isinstance(
            data,
            list
        ):

            return data

        if isinstance(
            data,
            dict
        ):

            for key in (
                "data",
                "announcements",
                "results"
            ):

                value = data.get(
                    key
                )

                if isinstance(
                    value,
                    list
                ):

                    return value

        return []

    except Exception as error:

        print(
            "NSE API error:",
            error
        )

        return []


# ============================================================
# CLEAN TEXT
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
    )

    return text.strip()


# ============================================================
# RECURSIVE DATA READER
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
# GET SYMBOL
# ============================================================

def get_symbol(item):

    keys = [
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

        if key in keys:

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

    keys = [
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

        if key in keys:

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

    keys = [
        "id",
        "announcementId",
        "announcement_id",
        "seqId",
        "seq_id",
        "attchmntText",
        "fileName",
        "file_name"
    ]

    for key, value in recursive_values(
        item
    ):

        if key in keys:

            value = clean_text(
                value
            )

            if value:

                return value

    return ""


# ============================================================
# GET ATTACHMENT LINK
# ============================================================

def get_link(item):

    keys = [
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

        if key in keys:

            value = clean_text(
                value
            )

            if value.startswith(
                "http"
            ):

                return value

    return ""


# ============================================================
# IDENTIFY BONUS / STOCK SPLIT
# ============================================================

def identify_action(item):

    values = []

    for key, value in recursive_values(
        item
    ):

        if isinstance(
            value,
            (str, int, float)
        ):

            values.append(
                clean_text(value)
            )

    text = " ".join(
        values
    )

    text_lower = text.lower()

    # Bonus
    if "bonus" in text_lower:

        return "BONUS"

    # Stock split
    if (
        "stock split" in text_lower
        or "sub-division" in text_lower
        or "sub division" in text_lower
        or "subdivision" in text_lower
    ):

        return "STOCK SPLIT"

    return ""


# ============================================================
# FIND ACTION DATE
# ============================================================

def find_action_date(item):

    preferred_keys = [
        "record date",
        "record_date",
        "recordDate",
        "ex date",
        "ex-date",
        "ex_date",
        "bonus date",
        "bonus_date",
        "effective date",
        "effective_date",
        "action date",
        "date of action"
    ]

    found = []

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

        for priority, wanted in enumerate(
            preferred_keys
        ):

            if wanted.lower() in key_text:

                match = re.search(
                    r"\b\d{1,2}[-/]"
                    r"\d{1,2}[-/]"
                    r"\d{2,4}\b",
                    value_text
                )

                if match:

                    found.append(
                        (
                            priority,
                            match.group(0)
                        )
                    )

    if found:

        found.sort(
            key=lambda item: item[0]
        )

        return found[0][1]

    return ""


# ============================================================
# NORMALIZE DATE
# ============================================================

def normalize_date(value):

    if not value:

        return ""

    value = value.strip()

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

            parsed = datetime.strptime(
                value,
                fmt
            )

            return parsed.strftime(
                "%d-%b-%Y"
            )

        except ValueError:

            continue

    return value


# ============================================================
# UNIQUE ALERT KEY
# ============================================================

def create_unique_key(
    item,
    announcement_id,
    link
):

    if link:

        raw = (
            "URL|"
            + link.strip().lower()
        )

    elif announcement_id:

        raw = (
            "ID|"
            + announcement_id.strip()
        )

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

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def create_message(
    company,
    symbol,
    action,
    action_date,
    link
):

    if action == "BONUS":

        title = "🎁 FRESH BONUS"

    else:

        title = "✂️ FRESH STOCK SPLIT"

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

    print()
    print("=" * 70)
    print(
        "NSE FRESH BONUS & STOCK SPLIT SCANNER"
    )
    print("=" * 70)

    # --------------------------------------------------------
    # INDIA TIME
    # --------------------------------------------------------

    current_ist = now_ist()

    print(
        "India Time:",
        current_ist.strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    )

    print(
        "NSE Market Window:",
        "09:15 - 15:30 IST"
    )

    market_open = is_nse_market_time()

    print(
        "Market Status:",
        "OPEN"
        if market_open
        else "CLOSED"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # MARKET HOURS
    # --------------------------------------------------------

    if not market_open:

        print(
            "Outside NSE market hours."
        )

        print(
            "No scan performed."
        )

        print("=" * 70)

        return

    # --------------------------------------------------------
    # LOAD STATE
    # --------------------------------------------------------

    state = load_seen()

    seen = state["keys"]

    initialized = state["initialized"]

    print(
        "Previously stored alerts:",
        len(seen)
    )

    print(
        "Scanner initialized:",
        initialized
    )

    # --------------------------------------------------------
    # GET DATA
    # --------------------------------------------------------

    announcements = get_announcements()

    print(
        "Announcements received:",
        len(announcements)
    )

    if not announcements:

        print(
            "No announcements received."
        )

        save_seen(
            state
        )

        return

    # --------------------------------------------------------
    # FIND BONUS / SPLIT
    # --------------------------------------------------------

    eligible = []

    for item in announcements:

        action = identify_action(
            item
        )

        if action not in (
            "BONUS",
            "STOCK SPLIT"
        ):

            continue

        symbol = get_symbol(
            item
        )

        company = get_company(
            item
        )

        announcement_id = (
            get_announcement_id(
                item
            )
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
            announcement_id=announcement_id,
            link=link
        )

        eligible.append(
            {
                "action": action,
                "symbol": symbol,
                "company": company,
                "announcement_id": announcement_id,
                "link": link,
                "action_date": action_date,
                "unique_key": unique_key
            }
        )

    print(
        "Bonus/Split announcements:",
        len(eligible)
    )

    # --------------------------------------------------------
    # FIRST RUN
    # --------------------------------------------------------

    if not initialized:

        print()
        print(
            "FIRST RUN / BASELINE MODE"
        )

        print(
            "Existing NSE announcements "
            "will be stored."
        )

        print(
            "They will NOT generate Telegram alerts."
        )

        for item in eligible:

            seen.add(
                item["unique_key"]
            )

        state["keys"] = seen

        state["initialized"] = True

        save_seen(
            state
        )

        print(
            "Baseline completed."
        )

        return

    # --------------------------------------------------------
    # FRESH ALERTS ONLY
    # --------------------------------------------------------

    fresh_count = 0

    for item in eligible:

        unique_key = item[
            "unique_key"
        ]

        action = item[
            "action"
        ]

        symbol = item[
            "symbol"
        ]

        if unique_key in seen:

            print(
                "Already alerted - SKIP:",
                f"{symbol} | {action}"
            )

            continue

        # ----------------------------------------------------
        # NEW EVENT
        # ----------------------------------------------------

        message = create_message(
            company=item["company"],
            symbol=symbol,
            action=action,
            action_date=item["action_date"],
            link=item["link"]
        )

        print()
        print("-" * 70)
        print("NEW FRESH ALERT")
        print(message)
        print("-" * 70)

        sent = send_telegram(
            message
        )

        if sent:

            # Only save after Telegram succeeds.
            seen.add(
                unique_key
            )

            state["keys"] = seen

            state["initialized"] = True

            save_seen(
                state
            )

            fresh_count += 1

            print(
                "Alert state updated."
            )

        else:

            print(
                "Telegram failed."
            )

            print(
                "This alert will be retried "
                "on the next hourly scan."
            )

    # --------------------------------------------------------
    # FINAL STATE
    # --------------------------------------------------------

    state["keys"] = seen

    state["initialized"] = True

    save_seen(
        state
    )

    print()
    print("=" * 70)
    print(
        "Fresh alerts sent:",
        fresh_count
    )
    print(
        "Total saved alerts:",
        len(seen)
    )
    print("=" * 70)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
