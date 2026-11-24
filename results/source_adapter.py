
"""
Share Sanket - Results Source Adapter

Purpose:
1. Read verified NSE/BSE announcement records.
2. Validate source URLs and required fields.
3. Normalize financial data.
4. Create results/source_announcements.json.

Input:
    results/inbox/*.json

Output:
    results/source_announcements.json

This version does not scrape NSE/BSE.
Only verified source records are accepted.
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
    "bseindia.com",
    "www.bseindia.com",
}

REQUIRED_FIELDS = [
    "symbol",
    "company",
    "period",
    "published_at",
    "source",
    "source_url",
]


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
    try:
        datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        return True
    except (TypeError, ValueError):
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


def validate_announcement(item):
    if not isinstance(item, dict):
        return False, "Record must be a JSON object"

    for field in REQUIRED_FIELDS:
        if not str(item.get(field, "")).strip():
            return False, f"Missing field: {field}"

    if not is_official_url(item["source_url"]):
        return False, "Source URL is not an approved NSE/BSE HTTPS URL"

    if not valid_date(item["published_at"]):
        return False, "Invalid published_at date"

    return True, "Valid"


def normalize_announcement(item):
    valid, reason = validate_announcement(item)

    if not valid:
        raise ValueError(reason)

    source = str(item["source"]).strip().upper()

    if source not in {"NSE", "BSE"}:
        raise ValueError("Source must be NSE or BSE")

    normalized = {
        "symbol": str(item["symbol"]).strip().upper(),
        "company": str(item["company"]).strip(),
        "period": str(item["period"]).strip(),
        "published_at": str(item["published_at"]).strip(),
        "source": source,
        "source_url": str(item["source_url"]).strip(),
        "financials": normalize_financials(
            item.get("financials", {})
        ),
        "expectations": item.get("expectations", {}),
    }

    # Analyst estimates are accepted only with a source.
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
                records.append(normalize_announcement(item))
            except ValueError as exc:
                print(
                    f"SKIPPED {path.name}: {exc}"
                )

    return records


def main():
    announcements = read_inbox()

    # Preserve previously imported records.
    existing = {"announcements": []}

    if OUTPUT_FILE.exists():
        existing = load_json(OUTPUT_FILE)

    previous = existing.get("announcements", [])

    if not isinstance(previous, list):
        raise ValueError(
            "Existing source_announcements.json is invalid"
        )

    # Avoid duplicate records.
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

    print("Share Sanket Results Source Adapter")
    print(f"Records imported: {len(announcements)}")
    print(f"Total unique records: {len(unique)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
