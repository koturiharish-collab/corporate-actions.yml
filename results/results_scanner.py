
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "source_announcements.json"
ALERTS_FILE = BASE_DIR / "alerts.json"
HISTORY_FILE = BASE_DIR / "history.json"
CALENDAR_FILE = BASE_DIR / "calendar.json"

IST = ZoneInfo("Asia/Kolkata")

BOT_TOKEN = os.getenv("RESULTS_TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("RESULTS_TELEGRAM_CHAT_ID", "")

RATINGS = [
    "Stellar",
    "Excellent",
    "Strong",
    "Average",
    "Weak",
    "Disappointing",
    "Insufficient Data",
]


def now_ist():
    return datetime.now(IST).isoformat()


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False
    ) as temp:
        json.dump(data, temp, indent=2, ensure_ascii=False)
        temp.write("\n")
        temp_path = temp.name

    os.replace(temp_path, path)


def valid_announcement(item):
    if not isinstance(item, dict):
        return False

    required = ["symbol", "company", "subject", "source_url"]

    if not all(item.get(key) for key in required):
        return False

    try:
        parsed = urlparse(item["source_url"])
        if parsed.scheme != "https" or not parsed.hostname:
            return False
    except Exception:
        return False

    return True


def number(value):
    if value is None or value == "":
        return None

    try:
        result = float(str(value).replace(",", "").replace("₹", "").strip())
        return result if result == result and abs(result) != float("inf") else None
    except (ValueError, TypeError):
        return None


def get_growth(current, previous):
    current = number(current)
    previous = number(previous)

    if current is None or previous is None or previous == 0:
        return None

    # A percentage growth figure is misleading when the previous
    # period was a loss. Handle this as a turnaround instead.
    if previous < 0 <= current:
        return None

    if previous < 0 and current < 0:
        return None

    return round(((current - previous) / abs(previous)) * 100, 2)


def calculate_margin(profit, revenue):
    profit = number(profit)
    revenue = number(revenue)

    if profit is None or revenue in (None, 0):
        return None

    return round((profit / revenue) * 100, 2)


def enrich_financials(item):
    financials = item.get("financials")
    if not isinstance(financials, dict):
        financials = {}

    item["financials"] = financials

    revenue = number(financials.get("revenue"))
    previous_revenue = number(financials.get("revenue_previous"))

    net_profit = number(financials.get("net_profit"))
    previous_net_profit = number(financials.get("net_profit_previous"))

    operating_profit = number(financials.get("operating_profit"))
    eps = number(financials.get("eps"))
    previous_eps = number(financials.get("eps_previous"))

    financials["revenue_yoy"] = get_growth(
        revenue, previous_revenue
    )

    financials["net_profit_yoy"] = get_growth(
        net_profit, previous_net_profit
    )

    financials["eps_yoy"] = get_growth(
        eps, previous_eps
    )

    financials["net_margin"] = calculate_margin(
        net_profit, revenue
    )

    financials["operating_margin"] = calculate_margin(
        operating_profit, revenue
    )

    financials["profit_turnaround"] = (
        previous_net_profit is not None
        and previous_net_profit < 0
        and net_profit is not None
        and net_profit > 0
    )

    financials["revenue_growth_available"] = (
        financials["revenue_yoy"] is not None
    )

    return item


def assess_results(item):
    financials = item.get("financials", {})

    if not isinstance(financials, dict):
        return "Insufficient Data"

    metrics = []

    revenue_growth = financials.get("revenue_yoy")
    profit_growth = financials.get("net_profit_yoy")
    eps_growth = financials.get("eps_yoy")

    for value in [revenue_growth, profit_growth, eps_growth]:
        if isinstance(value, (int, float)):
            metrics.append(value)

    turnaround = financials.get("profit_turnaround", False)

    # Do not rate an announcement using missing or unverified figures.
    if len(metrics) < 2 and not turnaround:
        return "Insufficient Data"

    score = 0

    for value in metrics:
        if value >= 25:
            score += 2
        elif value >= 5:
            score += 1
        elif value <= -25:
            score -= 2
        elif value < 0:
            score -= 1

    if turnaround:
        score += 2

    net_margin = number(financials.get("net_margin"))
    operating_margin = number(financials.get("operating_margin"))

    if net_margin is not None and net_margin < 0:
        score -= 1

    if operating_margin is not None and operating_margin < 0:
        score -= 1

    if len(metrics) >= 3 and score >= 6:
        return "Stellar"

    if score >= 4:
        return "Excellent"

    if score >= 2:
        return "Strong"

    if score >= 0:
        return "Average"

    if score <= -4:
        return "Disappointing"

    return "Weak"


def assess_expectations(item):
    expectations = item.get("expectations", {})

    if not isinstance(expectations, dict):
        return "Not Available"

    source = expectations.get("source")
    estimate = number(expectations.get("estimate"))
    actual = number(expectations.get("actual"))
    analyst_count = number(expectations.get("analyst_count"))
    estimate_date = expectations.get("estimate_date")

    if not all([source, estimate_date]):
        return "Not Available"

    if estimate is None or actual is None:
        return "Not Available"

    if analyst_count is None or analyst_count <= 0:
        return "Not Available"

    if actual > estimate:
        return "Beat Estimates"

    if actual < estimate:
        return "Missed Estimates"

    return "In-line"


def stable_id(item):
    if item.get("id"):
        return str(item["id"])

    return "|".join([
        str(item.get("symbol", "")),
        str(item.get("period", "")),
        str(item.get("published_at", "")),
        str(item.get("source_url", "")),
    ])


def send_telegram(item):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets not configured. Notification skipped.")
        return False

    financials = item.get("financials", {})

    message = (
        "📊 SHARE SANKET - RESULTS\n\n"
        f"Company: {item.get('company', 'N/A')}\n"
        f"Symbol: {item.get('symbol', 'N/A')}\n"
        f"Period: {item.get('period', 'N/A')}\n"
        f"Rating: {item.get('rating', 'Insufficient Data')}\n"
        f"Expectations: {item.get('expectations_rating', 'Not Available')}\n"
        f"Revenue: {financials.get('revenue', 'Not Available')}\n"
        f"Net Profit: {financials.get('net_profit', 'Not Available')}\n"
        f"EPS: {financials.get('eps', 'Not Available')}\n\n"
        f"Source: {item.get('source', 'N/A')}\n"
        f"{item.get('source_url', '')}\n\n"
        "For information only. Not investment advice."
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        response.raise_for_status()

        result = response.json()
        if not result.get("ok"):
            print("Telegram API did not confirm message delivery.")
            return False

        return True

    except requests.RequestException as error:
        print(f"Telegram notification failed: {error}")
        return False


def main():
    source_data = load_json(SOURCE_FILE, {"announcements": []})
    alerts_data = load_json(ALERTS_FILE, {"updated": "", "alerts": []})
    history_data = load_json(HISTORY_FILE, {"updated": "", "results": []})
    calendar_data = load_json(CALENDAR_FILE, {"updated": "", "calendar": []})

    announcements = source_data.get("announcements", [])

    if not isinstance(announcements, list):
        print("Invalid announcements format.")
        return

    alerts = alerts_data.get("alerts", [])
    history = history_data.get("results", [])
    calendar = calendar_data.get("calendar", [])

    if not isinstance(alerts, list):
        alerts = []
    if not isinstance(history, list):
        history = []
    if not isinstance(calendar, list):
        calendar = []

    old_alerts = {stable_id(item): item for item in alerts}
    old_history = {stable_id(item): item for item in history}
    old_calendar = {stable_id(item): item for item in calendar}

    processed = 0

    for original in announcements:
        if not valid_announcement(original):
            print("Skipping invalid announcement.")
            continue

        item = dict(original)
        item["id"] = stable_id(item)

        previous_record = old_alerts.get(item["id"], {})
        was_known = bool(previous_record)

        item = enrich_financials(item)
        item["rating"] = assess_results(item)
        item["expectations_rating"] = assess_expectations(item)

        item["data_status"] = item.get(
            "data_status",
            "Announcement detected - figures not verified"
        )

        item["processed_at"] = now_ist()

        # Preserve notification status when refreshing an existing record.
        item["telegram_notified"] = previous_record.get(
            "telegram_notified", False
        )

        old_alerts[item["id"]] = item
        old_history[item["id"]] = item

        # Keep the calendar record for the announcement.
        old_calendar[item["id"]] = {
            "id": item["id"],
            "symbol": item.get("symbol"),
            "company": item.get("company"),
            "period": item.get("period"),
            "published_at": item.get("published_at"),
            "source": item.get("source"),
            "source_url": item.get("source_url"),
            "status": "Announced",
        }

        processed += 1

        if not was_known:
            print(f"New result announcement: {item['symbol']}")
        else:
            print(f"Updated result record: {item['symbol']}")

    final_alerts = list(old_alerts.values())
    final_history = list(old_history.values())
    final_calendar = list(old_calendar.values())

    # Save records before sending notifications.
    # Failed notifications remain unmarked and can be retried next run.
    timestamp = now_ist()

    save_json(ALERTS_FILE, {
        "updated": timestamp,
        "alerts": final_alerts,
    })

    save_json(HISTORY_FILE, {
        "updated": timestamp,
        "results": final_history,
    })

    save_json(CALENDAR_FILE, {
        "updated": timestamp,
        "calendar": final_calendar,
    })

    for item in final_alerts:
        if not item.get("telegram_notified", False):
            if send_telegram(item):
                item["telegram_notified"] = True
                item["telegram_notified_at"] = now_ist()

    save_json(ALERTS_FILE, {
        "updated": now_ist(),
        "alerts": final_alerts,
    })

    print(f"Announcements processed: {processed}")
    print(f"Total results in history: {len(final_history)}")
    print("Results scanner completed.")


if __name__ == "__main__":
    main()
