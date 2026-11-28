import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from categories import category_enabled

try:
    import yfinance as yf
except Exception:
    yf = None


BASE_DIR = Path(__file__).resolve().parent
ALERTS_FILE = BASE_DIR / "alerts.json"
STATE_FILE = BASE_DIR / "order_state.json"

IST = ZoneInfo("Asia/Kolkata")
CATEGORY = "order_wins"
LOOKBACK_DAYS = 2
MAX_STATE = 5000

BOT_TOKEN = os.getenv("RESULTS_TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("RESULTS_TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/plain, */*",
}

# Match the NSE order announcement wording even when it appears in the
# announcement details rather than only in the short description.
EXACT_ORDER_ANNOUNCEMENT = re.compile(
    r"\bbagging\s*/\s*receiving\s+of\s+orders?\s*/\s*contracts?\b",
    re.I,
)

MONEY = re.compile(
    r"(?:₹|\bRs\.?|\bINR)\s*([0-9][0-9,]*(?:\.\d+)?)\s*"
    r"(crores?|crs?|lakhs?|lacs?|millions?|mn)?",
    re.I,
)

ORDER_FOLDER_ID = "orders"
SUBFOLDERS = [
    {"id": "very-big", "name": "Very big orders"},
    {"id": "big", "name": "Big orders"},
    {"id": "medium", "name": "Medium orders"},
    {"id": "small", "name": "Small orders"},
]

_fundamentals_cache = {}


def now_ist():
    return datetime.now(IST).isoformat(timespec="seconds")


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp:
            json.dump(data, temp, indent=1, ensure_ascii=False, allow_nan=False)
            temp.write("\n")
            temp_path = temp.name

        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def load_alerts():
    """Load alerts.json without overwriting an unreadable file."""
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (json.JSONDecodeError, OSError) as error:
        raise SystemExit(f"alerts.json unreadable, not touching it: {error}")

    if isinstance(data, list):
        data = {"alerts": data}
    if not isinstance(data, dict):
        raise SystemExit("alerts.json has an unexpected format, not touching it.")
    if not isinstance(data.get("alerts"), list):
        data["alerts"] = []

    return data


def ensure_order_folders(data):
    """Ensure the app's Orders folder has all four size subfolders."""
    folders = data.get("folders")
    if not isinstance(folders, list):
        folders = []
        data["folders"] = folders

    for folder in folders:
        if not isinstance(folder, dict) or folder.get("id") != ORDER_FOLDER_ID:
            continue

        existing = folder.setdefault("subfolders", [])
        if not isinstance(existing, list):
            existing = []
            folder["subfolders"] = existing

        known = {
            sub.get("id") for sub in existing if isinstance(sub, dict)
        }
        for sub in SUBFOLDERS:
            if sub["id"] not in known:
                existing.append(dict(sub))
        return

    folders.append({
        "id": ORDER_FOLDER_ID,
        "name": "Orders",
        "subfolders": [dict(sub) for sub in SUBFOLDERS],
    })


def load_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else None
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as error:
        print(f"Order state could not be read; treating as empty state: {error}")
        return []


def order_value_crore(text):
    """Return the largest disclosed amount in crore, or None."""
    best = None
    for match in MONEY.finditer(text or ""):
        number = float(match.group(1).replace(",", ""))
        unit = (match.group(2) or "").lower()

        if unit.startswith("cr"):
            crore = number
        elif unit.startswith(("lak", "lac")):
            crore = number / 100
        elif unit.startswith(("mn", "million")):
            crore = number / 10
        elif unit == "":
            crore = number / 10_000_000
        else:
            continue

        best = crore if best is None else max(best, crore)

    return best


def company_fundamentals(symbol):
    """Return Yahoo Finance NSE metrics; unavailable values remain None."""
    if yf is None or not symbol:
        return {}

    symbol = str(symbol).strip()
    if symbol in _fundamentals_cache:
        return dict(_fundamentals_cache[symbol])

    ticker = f"{symbol}.NS"
    result = {}
    try:
        info = yf.Ticker(ticker).info or {}

        def number(key, divisor=1):
            raw = info.get(key)
            try:
                value = float(raw)
                if value != value or value in (float("inf"), float("-inf")):
                    return None
                return round(value / divisor, 2)
            except (TypeError, ValueError, OverflowError):
                return None

        result = {
            "market_cap_crore": number("marketCap", 10_000_000),
            "trailing_pe": number("trailingPE"),
            "forward_pe": number("forwardPE"),
            "price_to_book": number("priceToBook"),
            "trailing_eps": number("trailingEps"),
            "enterprise_value": number("enterpriseValue", 10_000_000),
            "fifty_two_week_high": number("fiftyTwoWeekHigh"),
            "fifty_two_week_low": number("fiftyTwoWeekLow"),
            "sector": info.get("sector") or None,
            "industry": info.get("industry") or None,
        }
    except Exception as error:
        print(f"Company fundamentals lookup failed for {ticker}: {error}")

    _fundamentals_cache[symbol] = result
    return dict(result)


def market_cap_crore(symbol):
    """Return market capitalisation in crore, or None."""
    return company_fundamentals(symbol).get("market_cap_crore")


def size_folder(order_crore, mcap_crore):
    """Return (size folder, order value as percentage of market cap)."""
    if order_crore is None or not mcap_crore or mcap_crore <= 0:
        return "undisclosed", None

    percent = order_crore / mcap_crore * 100
    if percent > 50:
        folder = "very-big"
    elif percent >= 25:
        folder = "big"
    elif percent >= 15:
        folder = "medium"
    else:
        folder = "small"

    return folder, round(percent, 2)


def fetch_nse(start, end):
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    })
    session.get("https://www.nseindia.com", timeout=20)

    response = session.get(
        "https://www.nseindia.com/api/corporate-announcements",
        params={
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date": end.strftime("%d-%m-%Y"),
        },
        timeout=20,
    )
    response.raise_for_status()
    rows = response.json()

    if not isinstance(rows, list):
        raise ValueError("NSE returned an unexpected announcements response")

    out = []
    for row in rows:
        key = row.get("seq_id") or f"{row.get('symbol')}|{row.get('an_dt')}"
        out.append({
            "id": f"NSE:{key}",
            "exchange": "NSE",
            "company": row.get("sm_name") or "",
            "symbol": row.get("symbol") or "",
            "title": row.get("desc") or "",
            "category": row.get("category") or row.get("smIndustry") or "",
            "details": row.get("attchmntText") or "",
            "time": row.get("an_dt") or "",
            "link": row.get("attchmntFile") or "",
        })
    return out


def is_bagging(item):
    """Check title, category and NSE attachment text for the order wording."""
    text = " ".join(str(item.get(key) or "") for key in (
        "title", "category", "details"
    ))
    return bool(EXACT_ORDER_ANNOUNCEMENT.search(text))


def build_alert(item):
    text = f"{item.get('title', '')}\n{item.get('details', '')}"
    order_crore = order_value_crore(text)
    fundamentals = company_fundamentals(item.get("symbol"))
    mcap_crore = fundamentals.get("market_cap_crore")
    folder, percent = size_folder(order_crore, mcap_crore)

    size_names = {
        "very-big": "VERY BIG ORDER",
        "big": "BIG ORDER",
        "medium": "MEDIUM ORDER",
        "small": "SMALL ORDER",
        "undisclosed": "UNDISCLOSED",
    }

    return {
        "id": "order-" + item["id"],
        "source_id": item["id"],
        "category": CATEGORY,
        "folder": ORDER_FOLDER_ID,
        "subfolder": folder,
        "order_size": size_names[folder],
        "announcement_category": item.get("category") or "",
        "exchange": item["exchange"],
        "company": item["company"],
        "symbol": item["symbol"],
        "title": item["title"],
        "details": item["details"],
        "order_value_crore": round(order_crore, 2) if order_crore is not None else None,
        "market_cap_crore": round(mcap_crore, 2) if mcap_crore is not None else None,
        "order_percent_of_mcap": percent,
        "trailing_pe": fundamentals.get("trailing_pe"),
        "forward_pe": fundamentals.get("forward_pe"),
        "price_to_book": fundamentals.get("price_to_book"),
        "trailing_eps": fundamentals.get("trailing_eps"),
        "enterprise_value": fundamentals.get("enterprise_value"),
        "fifty_two_week_high": fundamentals.get("fifty_two_week_high"),
        "fifty_two_week_low": fundamentals.get("fifty_two_week_low"),
        "sector": fundamentals.get("sector"),
        "industry": fundamentals.get("industry"),
        "time": item["time"],
        "link": item["link"],
        "detected_at": now_ist(),
        "telegram_notified": False,
    }


def send_telegram(alert):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets not configured. Notification skipped.")
        return False

    value = alert.get("order_value_crore")
    value_text = f"₹{value:,.2f} crore" if value is not None else "Not disclosed"
    percent = alert.get("order_percent_of_mcap")
    percent_text = (
        f"{percent:.2f}% of market cap"
        if percent is not None else "Not available"
    )

    message = (
        "SHARE SANKET - ORDER WIN\n\n"
        f"Company: {alert['company']} ({alert['symbol']})\n"
        f"Exchange: {alert['exchange']}\n"
        f"Announcement: {alert['title']}\n"
        f"Order value: {value_text}\n"
        f"Size: {alert.get('order_size') or 'UNDISCLOSED'} ({percent_text})\n"
        f"Filed: {alert['time'] or 'Not Available'}\n\n"
        f"{alert['link']}\n\n"
        "For information only. Not investment advice."
    )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))
    except requests.RequestException as error:
        print(f"Telegram notification failed: {error}")
        return False


def is_order_alert(alert):
    return (
        isinstance(alert, dict)
        and (
            alert.get("category") == CATEGORY
            or alert.get("folder") == ORDER_FOLDER_ID
        )
    )


def main():
    today = datetime.now(IST).date()
    start = today - timedelta(days=LOOKBACK_DAYS)

    data = load_alerts()
    ensure_order_folders(data)

    old_alerts = data["alerts"]
    state = load_state()
    first_run = state is None

    # IDs already represented in the app are authoritative, regardless of state.
    seen = set(state or [])
    old_orders_by_source = {}
    for alert in old_alerts:
        if is_order_alert(alert) and alert.get("source_id"):
            old_orders_by_source[str(alert["source_id"])] = alert
            seen.add(str(alert["source_id"]))

    try:
        fetched = fetch_nse(start, today)
        if not isinstance(fetched, list):
            raise ValueError("NSE returned an invalid response")
        print(f"NSE: {len(fetched)} announcements")
    except Exception as error:
        print(f"NSE scan failed; previous NSE orders will be retained: {error}")
        fetched = []

    # Preserve every existing alert, including historical Orders.
    retained_alerts = list(old_alerts)
    new_order_alerts = []
    newly_seen_ids = set()
    recovered_orders = 0
    matched_orders = 0

    for item in fetched:
        source_id = item.get("id")
        if not source_id or not is_bagging(item):
            continue

        source_id = str(source_id)
        if source_id in newly_seen_ids:
            continue
        newly_seen_ids.add(source_id)
        matched_orders += 1

        existing = old_orders_by_source.get(source_id)
        if existing is not None:
            refreshed = build_alert(item)
            for key, value in refreshed.items():
                if key in (
                    "telegram_notified",
                    "telegram_notified_at",
                    "detected_at",
                ):
                    continue
                if value not in (None, "", []):
                    existing[key] = value
            continue

        # If state says this was seen but alerts.json lacks it, restore it
        # without sending another Telegram notification.
        if source_id in seen:
            recovered = build_alert(item)
            recovered["telegram_notified"] = True
            recovered["recovered_from_state"] = True
            new_order_alerts.append(recovered)
            recovered_orders += 1
            continue

        # On first run, establish a baseline without re-alerting old announcements.
        if first_run:
            continue

        new_order_alerts.append(build_alert(item))

    # Notify only genuinely new alerts, never recovered records.
    sent = 0
    if category_enabled(CATEGORY):
        for alert in new_order_alerts:
            if alert.get("recovered_from_state"):
                continue
            if send_telegram(alert):
                alert["telegram_notified"] = True
                alert["telegram_notified_at"] = now_ist()
                sent += 1
    else:
        print("order_wins is off in preferences.json: saved, not sent.")

    # New/recovered orders appear first; all existing alerts remain untouched.
    data["alerts"] = new_order_alerts + retained_alerts
    data["updated"] = now_ist()

    # Save the app feed before advancing state, so a failed feed write cannot
    # silently mark an order as successfully published.
    save_json(ALERTS_FILE, data)

    seen.update(newly_seen_ids)
    save_json(STATE_FILE, sorted(seen)[-MAX_STATE:])

    print("--------------------------------")
    print("SHARE SANKET ORDER SCANNER")
    print("--------------------------------")
    print(f"Matching order announcements: {matched_orders}")
    print(f"New order alerts: {len(new_order_alerts) - recovered_orders}")
    print(f"Recovered missing orders: {recovered_orders}")
    print(f"Telegram messages sent: {sent}")

    displayed_orders = sum(1 for alert in data["alerts"] if is_order_alert(alert))
    print(f"Orders currently displayed: {displayed_orders}")
    if first_run:
        print("Initial baseline completed; existing announcements were not notified.")
    print("Order scanner completed.")


if __name__ == "__main__":
    main()
