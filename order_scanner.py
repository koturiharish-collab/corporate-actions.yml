import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import re

import requests

from categories import category_enabled


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

# Only "Bagging/Receiving of orders/contracts" announcements.
BAGGING = re.compile(r"bagging\s*/?\s*receiv", re.I)

MONEY = re.compile(
    r"(?:₹|\bRs\.?|\bINR)\s*([0-9][0-9,]*(?:\.\d+)?)\s*"
    r"(crores?|crs?|lakhs?|lacs?|millions?|mn)?",
    re.I,
)


def now_ist():
    return datetime.now(IST).isoformat(timespec="seconds")


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temp:
        json.dump(data, temp, indent=1, ensure_ascii=False, allow_nan=False)
        temp.write("\n")
        temp_path = temp.name

    os.replace(temp_path, path)


def load_alerts():
    """Load alerts.json without ever overwriting an unreadable file."""
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


def load_state():
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else None
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return []


def order_value_crore(text):
    """Largest disclosed amount in the text, in crore. None if not disclosed."""
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


def size_folder(crore):
    if crore is None:
        return "undisclosed"
    if crore >= 100:
        return "big"
    if crore >= 10:
        return "medium"
    return "small"


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

    out = []
    for row in rows if isinstance(rows, list) else []:
        key = row.get("seq_id") or f"{row.get('symbol')}|{row.get('an_dt')}"
        out.append({
            "id": f"NSE:{key}",
            "exchange": "NSE",
            "company": row.get("sm_name") or "",
            "symbol": row.get("symbol") or "",
            "title": row.get("desc") or "",
            "details": row.get("attchmntText") or "",
            "time": row.get("an_dt") or "",
            "link": row.get("attchmntFile") or "",
        })
    return out


def fetch_bse(start, end):
    out = []
    headers = {
        **HEADERS,
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    }

    for page in range(1, 11):
        response = requests.get(
            "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
            params={
                "pageno": page,
                "strCat": "-1",
                "subcategory": "-1",
                "strPrevDate": start.strftime("%Y%m%d"),
                "strToDate": end.strftime("%Y%m%d"),
                "strSearch": "P",
                "strscrip": "",
                "strType": "C",
            },
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        rows = (response.json() or {}).get("Table") or []

        for row in rows:
            attachment = row.get("ATTACHMENTNAME") or ""
            out.append({
                "id": f"BSE:{row.get('NEWSID') or row.get('SCRIP_CD')}:{row.get('NEWS_DT')}",
                "exchange": "BSE",
                "company": row.get("SLONGNAME") or "",
                "symbol": str(row.get("SCRIP_CD") or ""),
                "title": row.get("HEADLINE") or "",
                "details": row.get("NEWSSUB") or "",
                "category": row.get("SUBCATNAME") or row.get("CATEGORYNAME") or "",
                "time": row.get("NEWS_DT") or "",
                "link": (
                    "https://www.bseindia.com/xml-data/corpfiling/AttachLive/" + attachment
                    if attachment else ""
                ),
            })

        if len(rows) < 50:
            break
        time.sleep(0.5)

    return out


def is_bagging(item):
    text = f"{item.get('title', '')} {item.get('category', '')}"
    return bool(BAGGING.search(text))


def build_alert(item):
    text = f"{item.get('title', '')}\n{item.get('details', '')}"
    crore = order_value_crore(text)
    folder = size_folder(crore)

    return {
        "id": "order-" + item["id"],
        "source_id": item["id"],
        "category": CATEGORY,
        "folder": "orders",
        "subfolder": folder,
        "exchange": item["exchange"],
        "company": item["company"],
        "symbol": item["symbol"],
        "title": item["title"],
        "details": item["details"],
        "order_value_crore": round(crore, 2) if crore is not None else None,
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

    message = (
        "SHARE SANKET - ORDER WIN\n\n"
        f"Company: {alert['company']} ({alert['symbol']})\n"
        f"Exchange: {alert['exchange']}\n"
        f"Announcement: {alert['title']}\n"
        f"Order value: {value_text}\n"
        f"Size: {alert['subfolder'].title()}\n"
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


def main():
    today = datetime.now(IST).date()
    start = today - timedelta(days=LOOKBACK_DAYS)

    data = load_alerts()
    alerts = data["alerts"]
    known = {a.get("source_id") for a in alerts if isinstance(a, dict)}

    state = load_state()
    first_run = state is None
    seen = set(state or [])

    fetched = []
    for name, fetcher in (("NSE", fetch_nse), ("BSE", fetch_bse)):
        try:
            rows = fetcher(start, today)
            print(f"{name}: {len(rows)} announcements")
            fetched.extend(rows)
        except Exception as error:
            print(f"{name} fetch failed, will retry next run: {error}")

    added = 0
    for item in fetched:
        if not item.get("id") or item["id"] in known:
            continue

        if not is_bagging(item):
            continue

        alert = build_alert(item)

        # First run: store everything but don't flood Telegram with old orders.
        if first_run:
            alert["telegram_notified"] = True
            alert["note"] = "Stored on first run, not sent"

        alerts.insert(0, alert)
        known.add(item["id"])
        seen.add(item["id"])
        added += 1

    # Retry any order alerts not yet sent, if the category is switched on.
    sent = 0
    if category_enabled(CATEGORY):
        for alert in alerts:
            if (
                isinstance(alert, dict)
                and alert.get("category") == CATEGORY
                and not alert.get("telegram_notified")
            ):
                if send_telegram(alert):
                    alert["telegram_notified"] = True
                    alert["telegram_notified_at"] = now_ist()
                    sent += 1
    else:
        print("order_wins is off in preferences.json: saved, not sent.")

    data["alerts"] = alerts[:500]
    data["updated"] = now_ist()
    save_json(ALERTS_FILE, data)

    save_json(STATE_FILE, list(seen)[-MAX_STATE:])

    print(f"New order alerts: {added}")
    print(f"Telegram messages sent: {sent}")
    print("Order scanner completed.")


if __name__ == "__main__":
    main()