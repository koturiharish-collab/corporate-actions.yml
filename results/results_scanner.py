
"""
Share Sanket - Results Scanner
Processes verified company financial-result announcements.

Input:
    results/source_announcements.json

Outputs:
    results/alerts.json
    results/history.json
    results/calendar.json

Telegram:
    RESULTS_TELEGRAM_BOT_TOKEN
    RESULTS_TELEGRAM_CHAT_ID

Important:
    This version processes a normalized source file.
    It does not scrape NSE/BSE directly. A verified and permitted
    official-data source adapter will be connected separately.
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent

SOURCE_FILE = BASE_DIR / "source_announcements.json"
ALERTS_FILE = BASE_DIR / "alerts.json"
HISTORY_FILE = BASE_DIR / "history.json"
CALENDAR_FILE = BASE_DIR / "calendar.json"

BOT_TOKEN = os.getenv("RESULTS_TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("RESULTS_TELEGRAM_CHAT_ID", "")

IST = timezone.utc


def now_iso():
    return datetime.now(IST).isoformat()


def load_json(path, default):
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Cannot safely read {path.name}: {exc}")


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    temp_path.replace(path)


def make_id(item):
    identity = "|".join([
        str(item.get("symbol", "")).upper().strip(),
        str(item.get("period", "")).strip(),
        str(item.get("published_at", "")).strip(),
        str(item.get("source_url", "")).strip(),
    ])

    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def valid_announcement(item):
    required = [
        "symbol",
        "company",
        "period",
        "published_at",
        "source",
        "source_url",
    ]

    if not isinstance(item, dict):
        return False

    if any(not str(item.get(key, "")).strip() for key in required):
        return False

    url = str(item["source_url"]).strip()

    return url.startswith("https://")


def number_or_none(value):
    if value is None or value == "":
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_growth(current, previous):
    current = number_or_none(current)
    previous = number_or_none(previous)

    if current is None or previous is None or previous == 0:
        return None

    return round(((current - previous) / abs(previous)) * 100, 2)


def calculate_margin(profit, revenue):
    profit = number_or_none(profit)
    revenue = number_or_none(revenue)

    if profit is None or revenue in (None, 0):
        return None

    return round((profit / revenue) * 100, 2)


def enrich_financials(item):
    financials = item.get("financials", {})

    revenue = number_or_none(financials.get("revenue"))
    net_profit = number_or_none(financials.get("net_profit"))
    operating_profit = number_or_none(
        financials.get("operating_profit")
    )

    financials["net_profit_margin"] = calculate_margin(
        net_profit, revenue
    )

    financials["operating_profit_margin"] = calculate_margin(
        operating_profit, revenue
    )

    financials["revenue_yoy_growth"] = calculate_growth(
        financials.get("revenue"),
        financials.get("previous_year_revenue"),
    )

    financials["net_profit_yoy_growth"] = calculate_growth(
        financials.get("net_profit"),
        financials.get("previous_year_net_profit"),
    )

    financials["eps_yoy_growth"] = calculate_growth(
        financials.get("eps"),
        financials.get("previous_year_eps"),
    )

    item["financials"] = financials
    return item


def assess_results(item):
    """
    Conservative descriptive assessment.
    This is not investment advice or a buy/sell signal.
    """

    financials = item.get("financials", {})

    revenue_growth = number_or_none(
        financials.get("revenue_yoy_growth")
    )
    profit_growth = number_or_none(
        financials.get("net_profit_yoy_growth")
    )
    eps_growth = number_or_none(
        financials.get("eps_yoy_growth")
    )

    values = [
        value for value in
        [revenue_growth, profit_growth, eps_growth]
        if value is not None
    ]

    if len(values) < 2:
        return "Insufficient data"

    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)

    if positive == len(values):
        if all(value >= 20 for value in values):
            return "Stellar"
        if all(value >= 10 for value in values):
            return "Excellent"
        return "Strong"

    if negative == len(values):
        return "Weak"

    if positive > negative:
        return "Mixed - improving"

    if negative > positive:
        return "Mixed - weakening"

    return "Mixed"


def assess_expectations(item):
    """
    Only compare when a genuine analyst-consensus estimate
    and its source are supplied.
    """

    expectations = item.get("expectations", {})

    source = expectations.get("source")
    estimate = number_or_none(expectations.get("net_profit"))
    actual = number_or_none(
        item.get("financials", {}).get("net_profit")
    )

    if not source or estimate is None or actual is None:
        return "Not Available"

    if estimate == 0:
        return "Not Available"

    difference = ((actual - estimate) / abs(estimate)) * 100

    if difference > 2:
        return "Beat Estimates"

    if difference < -2:
        return "Missed Estimates"

    return "In-line"


def send_telegram(item):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets not configured; skipping message.")
        return False

    financials = item.get("financials", {})

    message = (
        "📊 SHARE SANKET — RESULTS\n\n"
        f"🏢 {item['company']} ({item['symbol']})\n"
        f"📅 Period: {item['period']}\n"
        f"⭐ Assessment: {item.get('assessment', 'Not Available')}\n"
        f"📈 Expectations: {item.get('expectation_status', 'Not Available')}\n\n"
        f"Revenue: {financials.get('revenue', 'N/A')}\n"
        f"Net Profit: {financials.get('net_profit', 'N/A')}\n"
        f"EPS: {financials.get('eps', 'N/A')}\n"
        f"Operating Margin: "
        f"{financials.get('operating_profit_margin', 'N/A')}%\n"
        f"Net Profit Margin: "
        f"{financials.get('net_profit_margin', 'N/A')}%\n\n"
        f"Source: {item['source']}\n"
        f"{item['source_url']}\n\n"
        "Information only. Not investment advice."
    )

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

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message.")

    return True


def main():
    source = load_json(SOURCE_FILE, {"announcements": []})

    if not isinstance(source.get("announcements"), list):
        raise RuntimeError("Invalid source_announcements.json format.")

    alerts = load_json(ALERTS_FILE, {
        "updated": "",
        "alerts": [],
    })

    history = load_json(HISTORY_FILE, {
        "updated": "",
        "results": [],
    })

    calendar = load_json(CALENDAR_FILE, {
        "updated": "",
        "calendar": [],
    })

    existing_ids = {
        item.get("id")
        for item in alerts.get("alerts", [])
        if item.get("id")
    }

    new_count = 0

    for original in source["announcements"]:
        if not valid_announcement(original):
            print("Skipping invalid announcement.")
            continue

        item = dict(original)
        item["id"] = make_id(item)

        if item["id"] in existing_ids:
            continue

        item = enrich_financials(item)
        item["assessment"] = assess_results(item)
        item["expectation_status"] = assess_expectations(item)
        item["processed_at"] = now_iso()

        alerts["alerts"].append(item)
        history["results"].append(item)

        calendar["calendar"].append({
            "id": item["id"],
            "symbol": item["symbol"],
            "company": item["company"],
            "period": item["period"],
            "published_at": item["published_at"],
            "source_url": item["source_url"],
            "status": "Announced",
        })

        existing_ids.add(item["id"])
        new_count += 1

        try:
            send_telegram(item)
        except requests.RequestException as exc:
            print(f"Telegram delivery failed: {exc}")

    timestamp = now_iso()

    if new_count:
        alerts["updated"] = timestamp
        history["updated"] = timestamp
        calendar["updated"] = timestamp

        save_json(ALERTS_FILE, alerts)
        save_json(HISTORY_FILE, history)
        save_json(CALENDAR_FILE, calendar)

    print(f"New results processed: {new_count}")
    print("Results scanner completed.")


if __name__ == "__main__":
    main()
