
"""
Share Sanket - Results Scanner

Features:
- Processes standalone and consolidated financial results separately.
- Calculates revenue, profit and EPS growth.
- Calculates net and operating margins.
- Generates separate ratings for each reporting scope.
- Uses consolidated results as the default headline scope.
- Maintains alerts, history and results calendar.
- Sends Telegram notifications when configured and when the
  category is switched on in preferences.json.
- Preserves existing records and notification status.
- Does not invent analyst estimates or financial figures.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from categories import category_enabled


BASE_DIR = Path(__file__).resolve().parent

SOURCE_FILE = BASE_DIR / "source_announcements.json"
ALERTS_FILE = BASE_DIR / "alerts.json"
HISTORY_FILE = BASE_DIR / "history.json"
CALENDAR_FILE = BASE_DIR / "calendar.json"

IST = ZoneInfo("Asia/Kolkata")

BOT_TOKEN = os.getenv("RESULTS_TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("RESULTS_TELEGRAM_CHAT_ID", "")

SCOPES = ("standalone", "consolidated")

RESULTS_CATEGORY = "results"


# Only accept genuine financial-results announcements.
RESULT_KEYWORDS = (
    "financial results",
    "unaudited financial results",
    "audited financial results",
    "quarterly results",
    "quarterly financial results",
    "half yearly results",
    "half-yearly results",
    "half yearly financial results",
    "half-yearly financial results",
    "annual results",
    "annual financial results",
    "standalone results",
    "consolidated results",
    "results for the period ended",
    "results for the quarter ended",
    "results for the year ended",
)


def is_financial_results_announcement(item):
    """
    Accept only Outcome of Board Meeting announcements
    whose details explicitly refer to financial results.
    """

    if not isinstance(item, dict):
        return False

    subject = str(item.get("subject", "")).lower().strip()
    details = str(item.get("details", "")).lower().strip()

    if "outcome of board meeting" not in subject:
        return False

    return any(
        keyword in details
        for keyword in RESULT_KEYWORDS
    )


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
        json.dump(
            data,
            temp,
            indent=2,
            ensure_ascii=False,
            allow_nan=False
        )
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

        allowed_domains = (
            "nseindia.com",
            "nseindia.in",
            "bseindia.com",
        )

        if not any(
            parsed.hostname == domain
            or parsed.hostname.endswith("." + domain)
            for domain in allowed_domains
        ):
            return False

    except Exception:
        return False

    return True


def number(value):
    if value is None or value == "":
        return None

    try:
        result = float(
            str(value).replace(",", "").replace("₹", "").strip()
        )

        if result != result or abs(result) == float("inf"):
            return None

        return result

    except (ValueError, TypeError):
        return None


def get_growth(current, previous):
    current = number(current)
    previous = number(previous)

    if current is None or previous is None or previous == 0:
        return None

    # Do not calculate misleading percentage growth from a loss.
    if previous < 0:
        return None

    return round(((current - previous) / abs(previous)) * 100, 2)


def calculate_margin(profit, revenue):
    profit = number(profit)
    revenue = number(revenue)

    if profit is None or revenue in (None, 0):
        return None

    return round((profit / revenue) * 100, 2)


def enrich_financial_record(financials):
    """Calculate derived financial metrics for one scope."""

    if not isinstance(financials, dict):
        financials = {}

    financials = dict(financials)

    revenue = number(financials.get("revenue"))

    previous_revenue = number(
        financials.get(
            "previous_year_revenue",
            financials.get("revenue_previous")
        )
    )

    net_profit = number(financials.get("net_profit"))

    previous_net_profit = number(
        financials.get(
            "previous_year_net_profit",
            financials.get("net_profit_previous")
        )
    )

    operating_profit = number(
        financials.get("operating_profit")
    )

    eps = number(financials.get("eps"))

    previous_eps = number(
        financials.get(
            "previous_year_eps",
            financials.get("eps_previous")
        )
    )

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

    return financials


def has_financial_data(financials):
    if not isinstance(financials, dict):
        return False

    return any(
        number(financials.get(field)) is not None
        for field in ("revenue", "net_profit", "eps")
    )


def enrich_financials(item):
    """
    Enrich standalone and consolidated figures independently.

    Consolidated is the default headline scope.
    Legacy financials are retained as a fallback.
    """

    scopes = item.get("financials_by_scope", {})

    if not isinstance(scopes, dict):
        scopes = {}

    enriched = {
        "standalone": enrich_financial_record(
            scopes.get("standalone", {})
        ),
        "consolidated": enrich_financial_record(
            scopes.get("consolidated", {})
        ),
    }

    # Support older announcements using the legacy field.
    legacy = item.get("financials", {})

    if (
        not has_financial_data(enriched["standalone"])
        and not has_financial_data(enriched["consolidated"])
    ):
        if has_financial_data(legacy):
            enriched["consolidated"] = enrich_financial_record(legacy)

    selected = item.get("primary_scope")

    if selected not in SCOPES:
        if has_financial_data(enriched["consolidated"]):
            selected = "consolidated"
        elif has_financial_data(enriched["standalone"]):
            selected = "standalone"
        else:
            selected = "consolidated"

    if not has_financial_data(enriched[selected]):
        alternate = (
            "standalone"
            if selected == "consolidated"
            else "consolidated"
        )

        if has_financial_data(enriched[alternate]):
            selected = alternate

    item["financials_by_scope"] = enriched
    item["primary_scope"] = selected

    # Keep compatibility with existing UI and scanner fields.
    item["financials"] = dict(enriched[selected])

    return item


def assess_scope(financials):
    """Rate one reporting scope using available financial evidence."""

    if not isinstance(financials, dict):
        return "Insufficient Data"

    metrics = []

    for field in ("revenue_yoy", "net_profit_yoy", "eps_yoy"):
        value = number(financials.get(field))

        if value is not None:
            metrics.append(value)

    turnaround = financials.get("profit_turnaround", False)

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
    operating_margin = number(
        financials.get("operating_margin")
    )

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


def assess_results(item):
    scopes = item.get("financials_by_scope", {})

    if not isinstance(scopes, dict):
        scopes = {}

    item["ratings_by_scope"] = {
        "standalone": assess_scope(scopes.get("standalone", {})),
        "consolidated": assess_scope(scopes.get("consolidated", {})),
    }

    selected = item.get("primary_scope", "consolidated")

    return item["ratings_by_scope"].get(
        selected,
        "Insufficient Data"
    )


def assess_expectations(item):
    expectations = item.get("expectations", {})

    if not isinstance(expectations, dict):
        return "Not Available"

    source = expectations.get("source")
    estimate = number(expectations.get("estimate"))
    actual = number(expectations.get("actual"))
    analyst_count = number(expectations.get("analyst_count"))
    estimate_date = expectations.get("estimate_date")

    if not source or not estimate_date:
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


def telegram_scope_text(item, scope):
    financials = item.get(
        "financials_by_scope", {}
    ).get(scope, {})

    if not isinstance(financials, dict):
        financials = {}

    rating = item.get(
        "ratings_by_scope", {}
    ).get(scope, "Insufficient Data")

    return (
        f"\n{scope.title()} Results\n"
        f"Rating: {rating}\n"
        f"Revenue: {financials.get('revenue', 'Not Available')}\n"
        f"Net Profit: {financials.get('net_profit', 'Not Available')}\n"
        f"EPS: {financials.get('eps', 'Not Available')}\n"
        f"Revenue YoY: {financials.get('revenue_yoy', 'Not Available')}\n"
    )


def send_telegram(item):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets not configured. Notification skipped.")
        return False

    message = (
        "SHARE SANKET - RESULTS\n\n"
        f"Company: {item.get('company', 'N/A')}\n"
        f"Symbol: {item.get('symbol', 'N/A')}\n"
        f"Period: {item.get('period', 'N/A')}\n"
        f"Expectations: {item.get('expectations_rating', 'Not Available')}\n"
        f"Units: {item.get('financial_units', 'Not specified')}\n"
        + telegram_scope_text(item, "standalone")
        + telegram_scope_text(item, "consolidated")
        + "\n"
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
    source_data = load_json(
        SOURCE_FILE,
        {"announcements": []}
    )

    alerts_data = load_json(
        ALERTS_FILE,
        {"updated": "", "alerts": []}
    )

    history_data = load_json(
        HISTORY_FILE,
        {"updated": "", "results": []}
    )

    calendar_data = load_json(
        CALENDAR_FILE,
        {"updated": "", "calendar": []}
    )

    announcements = source_data.get("announcements", [])

    if not isinstance(announcements, list):
        print("Invalid announcements format.")
        return

    # Filter the incoming source records.
    rejected_source = sum(
        1 for item in announcements
        if not is_financial_results_announcement(item)
    )

    announcements = [
        item for item in announcements
        if is_financial_results_announcement(item)
    ]

    alerts = alerts_data.get("alerts", [])
    history = history_data.get("results", [])
    calendar = calendar_data.get("calendar", [])

    if not isinstance(alerts, list):
        alerts = []

    if not isinstance(history, list):
        history = []

    if not isinstance(calendar, list):
        calendar = []

    old_alerts = {
        stable_id(item): item
        for item in alerts
        if isinstance(item, dict)
    }

    old_history = {
        stable_id(item): item
        for item in history
        if isinstance(item, dict)
    }

    old_calendar = {
        stable_id(item): item
        for item in calendar
        if isinstance(item, dict)
    }

    # Remove previously misclassified announcements.
    old_alerts = {
        key: item
        for key, item in old_alerts.items()
        if is_financial_results_announcement(item)
    }

    old_history = {
        key: item
        for key, item in old_history.items()
        if is_financial_results_announcement(item)
    }

    # Calendar entries do not contain subject/details.
    # Retain calendar entries only when their IDs exist
    # in the filtered Results alerts or history.
    valid_ids = set(old_alerts) | set(old_history)

    old_calendar = {
        key: item
        for key, item in old_calendar.items()
        if key in valid_ids
    }

    processed = 0

    for original in announcements:
        if not valid_announcement(original):
            print("Skipping invalid announcement.")
            continue

        item = dict(original)
        item["id"] = stable_id(item)
        item["category"] = RESULTS_CATEGORY

        previous_record = old_alerts.get(item["id"], {})
        was_known = bool(previous_record)

        # Preserve notification state on updates.
        item["telegram_notified"] = previous_record.get(
            "telegram_notified", False
        )

        if previous_record.get("telegram_notified_at"):
            item["telegram_notified_at"] = previous_record[
                "telegram_notified_at"
            ]

        item = enrich_financials(item)

        item["rating"] = assess_results(item)

        item["expectations_rating"] = assess_expectations(item)

        item["processed_at"] = now_ist()

        item["data_status"] = item.get(
            "data_status",
            "Announcement detected - figures not verified"
        )

        old_alerts[item["id"]] = item
        old_history[item["id"]] = item

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

        print(
            "Standalone rating:",
            item["ratings_by_scope"]["standalone"]
        )

        print(
            "Consolidated rating:",
            item["ratings_by_scope"]["consolidated"]
        )

        print("Primary scope:", item["primary_scope"])

    final_alerts = list(old_alerts.values())
    final_history = list(old_history.values())
    final_calendar = list(old_calendar.values())

    timestamp = now_ist()

    # Save all data before attempting Telegram delivery.
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

    # Retry notifications that have not been confirmed.
    # Alerts in a category that is switched off are saved but not sent.
    for item in final_alerts:
        if item.get("telegram_notified", False):
            continue

        if not category_enabled(item.get("category", RESULTS_CATEGORY)):
            continue

        if send_telegram(item):
            item["telegram_notified"] = True
            item["telegram_notified_at"] = now_ist()

    save_json(ALERTS_FILE, {
        "updated": now_ist(),
        "alerts": final_alerts,
    })

    print("--------------------------------")
    print("SHARE SANKET RESULTS SCANNER")
    print("--------------------------------")
    print(f"Announcements processed: {processed}")
    print(f"Rejected unrelated announcements: {rejected_source}")
    print(f"Total results in history: {len(final_history)}")
    print("Results scanner completed.")


if __name__ == "__main__":
    main()