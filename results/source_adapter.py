
"""
Share Sanket - Results Source Adapter

Filters official NSE/BSE board meeting announcements
that contain financial results.

Input:
    results/inbox/*.json

Output:
    results/source_announcements.json

No live scraping or PDF extraction in this version.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
INBOX_DIR = BASE_DIR / "inbox"
OUTPUT_FILE = BASE_DIR / "source_announcements.json"

ALLOWED_DOMAINS = {
    "nseindia.com",
    "www.nseindia.com",
    "nseindia.in",
    "www.nseindia.in",
    "bseindia.com",
    "www.bseindia.com",
}

RESULT_KEYWORDS = [
    "financial results",
    "unaudited financial results",
    "audited financial results",
    "quarterly results",
    "annual results",
    "standalone results",
    "consolidated results",
    "results for the period ended",
    "results for the quarter ended",
    "results for the year ended",
]

BOARD_MEETING_SUBJECT = "outcome of board meeting"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}")


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    temp_path.replace(path)


def is_official_url(url):
    try:
        parsed = urlparse(str(url).strip())

        return (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_DOMAINS
        )
    except (TypeError, ValueError):
        return False


def valid_date(value):
    value = str(value).strip()

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y",
        "%d/%m/%Y",
    ]

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        pass

    for date_format in formats:
        try:
            datetime.strptime(value, date_format)
            return True
        except ValueError:
            continue

    return False


def normalize_financials(financials):
    if not isinstance(financials, dict):
        return {}

    allowed_fields = [
        "revenue",
        "net_profit",
        "eps",
        "operating_profit",
        "gross_profit",
        "ebitda",
        "total_debt",
        "cash_flow",
        "previous_year_revenue",
        "previous_year_net_profit",
        "previous_year_eps",
    ]

    normalized = {}

    for field in allowed_fields:
        value = financials.get(field)

        if value is None or value == "":
            normalized[field] = None
            continue

        try:
            normalized[field] = float(value)
        except (TypeError, ValueError):
            normalized[field] = None

    return normalized


def is_results_announcement(item):
    subject = str(item.get("subject", "")).lower()
    details = str(item.get("details", "")).lower()

    if BOARD_MEETING_SUBJECT not in subject:
        return False

    return any(
        keyword in details
        for keyword in RESULT_KEYWORDS
    )


def classify_result_scope(text):
    text = str(text).lower()

    has_standalone = "standalone" in text
    has_consolidated = "consolidated" in text

    if has_standalone and has_consolidated:
        return "Standalone and Consolidated"

    if has_consolidated:
        return "Consolidated"

    if has_standalone:
        return "Standalone"

    return "Not Specified"


def validate_announcement(item):
    if not isinstance(item, dict):
        return False, "Record must be a JSON object"

    required = [
        "symbol",
        "company",
        "subject",
        "details",
        "published_at",
        "source",
        "source_url",
    ]

    for field in required:
        if not str(item.get(field, "")).strip():
            return False, f"Missing field: {field}"

    source = str(item["source"]).strip().upper()

    if source not in {"NSE", "BSE"}:
        return False, "Source must be NSE or BSE"

    if not is_official_url(item["source_url"]):
        return False, "Invalid official announcement URL"

    if not valid_date(item["published_at"]):
        return False, "Invalid announcement date"

    attachment_url = item.get("attachment_url")

    if attachment_url and not is_official_url(attachment_url):
        return False, "Invalid PDF attachment URL"

    return True, "Valid"


def normalize_announcement(item):
    valid, reason = validate_announcement(item)

    if not valid:
        raise ValueError(reason)

    if not is_results_announcement(item):
        return None

    combined_text = (
        str(item.get("subject", ""))
        + " "
        + str(item.get("details", ""))
    )

    normalized = {
        "symbol": str(item["symbol"]).strip().upper(),
        "company": str(item["company"]).strip(),
        "period": str(item.get("period", "Not Specified")).strip(),
        "published_at": str(item["published_at"]).strip(),
        "source": str(item["source"]).strip().upper(),
        "subject": str(item["subject"]).strip(),
        "details": str(item["details"]).strip(),
        "source_url": str(item["source_url"]).strip(),
        "attachment_url": str(
            item.get("attachment_url", "")
        ).strip(),
        "result_scope": classify_result_scope(combined_text),
        "financials": normalize_financials(
            item.get("financials", {})
        ),
        "expectations": item.get("expectations", {}),
        "data_status": "Announcement detected - figures not verified",
    }

    expectations = normalized["expectations"]

    if not isinstance(expectations, dict):
        expectations = {}

    if not expectations.get("source"):
        expectations = {}

    normalized["expectations"] = expectations

    return normalized


def read_inbox():
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

    records = []

    for path in sorted(INBOX_DIR.glob("*.json")):
        data = load_json(path)

        if isinstance(data, list):
            announcements = data
        elif isinstance(data, dict):
            announcements = data.get("announcements", [])
        else:
            raise ValueError(
                f"Invalid JSON structure in {path.name}"
            )

        if not isinstance(announcements, list):
            raise ValueError(
                f"'announcements' must be a list in {path.name}"
            )

        for item in announcements:
            try:
                result = normalize_announcement(item)

                if result is not None:
                    records.append(result)
                else:
                    print(
                        f"SKIPPED: Not a financial results announcement"
                    )

            except ValueError as exc:
                print(f"SKIPPED {path.name}: {exc}")

    return records


def main():
    announcements = read_inbox()

    existing = {"announcements": []}

    if OUTPUT_FILE.exists():
        existing = load_json(OUTPUT_FILE)

    previous = existing.get("announcements", [])

    if not isinstance(previous, list):
        raise ValueError(
            "Existing source_announcements.json is invalid"
        )

    unique = {}

    for item in previous + announcements:
        key = (
            item.get("symbol"),
            item.get("period"),
            item.get("published_at"),
            item.get("source_url"),
        )

        unique[key] = item

    output = {
        "updated": now_iso(),
        "announcements": list(unique.values()),
    }

    save_json(OUTPUT_FILE, output)

    print("SHARE SANKET RESULTS SOURCE ADAPTER")
    print(f"New matching records: {len(announcements)}")
    print(f"Total unique records: {len(unique)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
