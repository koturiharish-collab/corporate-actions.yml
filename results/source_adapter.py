"""
Share Sanket - Results Source Adapter

Processes official NSE financial-results announcements.

Supports:
- Standalone financial results
- Consolidated financial results
- Separate financial data for each reporting scope
- Existing announcement compatibility
- Duplicate prevention
- Preservation of previously verified figures

Input:
    results/inbox/*.json

Output:
    results/source_announcements.json

Only Outcome of Board Meeting announcements relating to
financial results are accepted.
"""

import json
import math
import time

import requests
from datetime import date, datetime, timedelta, timezone
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
    "nsearchives.nseindia.in",
}

NSE_HOME = "https://www.nseindia.com"
NSE_FILINGS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
NSE_ANNOUNCEMENTS_API = "https://www.nseindia.com/api/corporate-announcements"
NSE_LOOKBACK_DAYS = 7

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}

RESULT_KEYWORDS = [
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
]

BOARD_MEETING_SUBJECT = "outcome of board meeting"

FINANCIAL_FIELDS = [
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
    "revenue_yoy",
    "net_profit_yoy",
    "eps_yoy",
    "net_margin",
    "operating_margin",
]

SCOPES = ("standalone", "consolidated")


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
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        file.write("\n")

    temp_path.replace(path)


def is_official_url(url):
    try:
        parsed = urlparse(str(url).strip())
        hostname = (parsed.hostname or "").lower()

        return (
            parsed.scheme == "https"
            and hostname in ALLOWED_DOMAINS
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


def normalize_number(value):
    if value is None or value == "":
        return None

    try:
        result = float(
            str(value).replace(",", "").replace("₹", "").strip()
        )

        if not math.isfinite(result):
            return None

        return result

    except (TypeError, ValueError):
        return None


def normalize_financials(financials):
    if not isinstance(financials, dict):
        financials = {}

    normalized = {}

    for field in FINANCIAL_FIELDS:
        normalized[field] = normalize_number(
            financials.get(field)
        )

    normalized["profit_turnaround"] = bool(
        financials.get("profit_turnaround", False)
    )

    normalized["revenue_growth_available"] = (
        normalized["revenue_yoy"] is not None
    )

    return normalized


def normalize_financial_scopes(item):
    """
    Store standalone and consolidated figures separately.

    Legacy figures are not automatically assigned to either scope.
    """

    supplied = item.get("financials_by_scope", {})

    if not isinstance(supplied, dict):
        supplied = {}

    output = {}

    for scope in SCOPES:
        output[scope] = normalize_financials(
            supplied.get(scope, {})
        )

    legacy = normalize_financials(
        item.get("financials", {})
    )

    return output, legacy


def is_results_announcement(item):
    """
    Accept only Outcome of Board Meeting announcements
    whose details explicitly refer to financial results.
    """

    subject = str(item.get("subject", "")).lower().strip()
    details = str(item.get("details", "")).lower().strip()

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

    if source != "NSE":
        return False, "Only NSE announcements are accepted"

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

    financials_by_scope, legacy_financials = (
        normalize_financial_scopes(item)
    )

    expectations = item.get("expectations", {})

    if not isinstance(expectations, dict):
        expectations = {}

    if not expectations.get("source"):
        expectations = {}

    normalized = {
        "symbol": str(item["symbol"]).strip().upper(),
        "company": str(item["company"]).strip(),
        "period": str(
            item.get("period", "Not Specified")
        ).strip(),
        "published_at": str(item["published_at"]).strip(),
        "source": str(item["source"]).strip().upper(),
        "subject": str(item["subject"]).strip(),
        "details": str(item["details"]).strip(),
        "source_url": str(item["source_url"]).strip(),
        "attachment_url": str(
            item.get("attachment_url", "")
        ).strip(),
        "result_scope": classify_result_scope(combined_text),
        "financials_by_scope": financials_by_scope,
        "financials": legacy_financials,
        "expectations": expectations,
        "data_status": item.get(
            "data_status",
            "Announcement detected - figures not verified"
        ),
        "financial_units": item.get(
            "financial_units",
            "Not specified"
        ),
    }

    return normalized


def announcement_key(item):
    return (
        item.get("symbol"),
        item.get("period"),
        item.get("published_at"),
        item.get("source_url"),
    )


def merge_preserving_verified_data(old, new):
    """
    Update announcement metadata without replacing existing
    non-null financial values with empty values.
    """

    merged = dict(old)

    for key, value in new.items():
        if key not in {
            "financials",
            "financials_by_scope",
        }:
            if value not in (None, ""):
                merged[key] = value

    old_scopes = old.get("financials_by_scope", {})
    new_scopes = new.get("financials_by_scope", {})

    if not isinstance(old_scopes, dict):
        old_scopes = {}

    if not isinstance(new_scopes, dict):
        new_scopes = {}

    merged_scopes = {}

    for scope in SCOPES:
        previous = old_scopes.get(scope, {})
        incoming = new_scopes.get(scope, {})

        if not isinstance(previous, dict):
            previous = {}

        if not isinstance(incoming, dict):
            incoming = {}

        combined = dict(previous)

        for field, value in incoming.items():
            if value is not None:
                combined[field] = value

        merged_scopes[scope] = combined

    merged["financials_by_scope"] = merged_scopes

    old_legacy = old.get("financials", {})
    new_legacy = new.get("financials", {})

    if not isinstance(old_legacy, dict):
        old_legacy = {}

    if not isinstance(new_legacy, dict):
        new_legacy = {}

    merged_legacy = dict(old_legacy)

    for field, value in new_legacy.items():
        if value is not None:
            merged_legacy[field] = value

    merged["financials"] = merged_legacy

    return merged


def fetch_nse_announcements():
    """
    Fetch recent announcements from the official NSE API.

    Visit the filings page directly to obtain NSE cookies.
    The homepage is intentionally skipped because it can return 403.

    Only qualifying Outcome of Board Meeting announcements
    are returned.
    """

    start = date.today() - timedelta(days=NSE_LOOKBACK_DAYS)
    end = date.today()

    last_error = None
    max_attempts = 4

    for attempt in range(1, max_attempts + 1):
        session = None

        try:
            session = requests.Session()
            session.headers.update(NSE_HEADERS)

            # Visit the corporate filings page directly.
            filings_page = session.get(
                NSE_FILINGS_PAGE,
                timeout=(15, 25),
            )
            filings_page.raise_for_status()

            # Fetch official NSE announcements.
            response = session.get(
                NSE_ANNOUNCEMENTS_API,
                params={
                    "index": "equities",
                    "from_date": start.strftime("%d-%m-%Y"),
                    "to_date": end.strftime("%d-%m-%Y"),
                },
                timeout=(20, 30),
            )
            response.raise_for_status()

            payload = response.json()

            if isinstance(payload, dict):
                payload = payload.get(
                    "data",
                    payload.get("rows", [])
                )

            if not isinstance(payload, list):
                raise ValueError(
                    "NSE returned an unexpected announcements response"
                )

            announcements = []
            rejected_count = 0

            for row in payload:
                if not isinstance(row, dict):
                    continue

                subject = str(row.get("desc") or "").strip()
                details = str(row.get("attchmntText") or "").strip()
                symbol = str(row.get("symbol") or "").strip().upper()
                company = str(row.get("sm_name") or "").strip()
                published_at = str(row.get("an_dt") or "").strip()
                pdf_url = str(row.get("attchmntFile") or "").strip()

                if not (
                    subject
                    and details
                    and symbol
                    and company
                    and published_at
                    and pdf_url
                ):
                    rejected_count += 1
                    continue

                if pdf_url.startswith("/"):
                    pdf_url = "https://www.nseindia.com" + pdf_url
                elif pdf_url.startswith("http://"):
                    pdf_url = "https://" + pdf_url[len("http://"):]

                announcement = {
                    "symbol": symbol,
                    "company": company,
                    "subject": subject,
                    "details": details,
                    "published_at": published_at,
                    "source": "NSE",
                    "source_url": pdf_url,
                    "attachment_url": pdf_url,
                    "period": "Not Specified",
                    "financials": {},
                    "financials_by_scope": {},
                    "data_status": (
                        "NSE announcement detected; "
                        "financial figures not yet verified"
                    ),
                    "financial_units": "Not specified",
                }

                # IMPORTANT:
                # Only accept Outcome of Board Meeting announcements
                # that explicitly mention financial results in details.
                if not is_results_announcement(announcement):
                    rejected_count += 1
                    continue

                announcements.append(announcement)

            print(
                f"NSE announcements fetched: {len(payload)} "
                f"({start.isoformat()} to {end.isoformat()})"
            )
            print(
                f"Matching financial-results announcements: "
                f"{len(announcements)}"
            )
            print(
                f"Rejected unrelated announcements: {rejected_count}"
            )

            return announcements

        except Exception as exc:
            last_error = exc

            print(
                f"NSE fetch attempt {attempt}/{max_attempts} failed: {exc}"
            )

            if attempt < max_attempts:
                time.sleep(attempt * 5)

        finally:
            if session is not None:
                session.close()

    raise RuntimeError(
        f"Unable to fetch NSE announcements after "
        f"{max_attempts} attempts: {last_error}"
    )


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
                        "SKIPPED: Not a qualifying financial-results announcement"
                    )

            except ValueError as exc:
                print(f"SKIPPED {path.name}: {exc}")

    return records


def main():
    # Keep manually verified inbox entries and add filtered live NSE data.
    announcements = read_inbox()

    try:
        live_announcements = fetch_nse_announcements()
        announcements.extend(live_announcements)

    except Exception as exc:
        # Do not lose or overwrite existing/inbox data if NSE is temporarily down.
        print(
            "WARNING: Live NSE refresh failed; "
            f"processing inbox records only: {exc}"
        )

    existing = {"announcements": []}

    if OUTPUT_FILE.exists():
        existing = load_json(OUTPUT_FILE)

    previous = existing.get("announcements", [])

    if not isinstance(previous, list):
        raise ValueError(
            "Existing source_announcements.json is invalid"
        )

    unique = {}

    for item in previous:
        if isinstance(item, dict):
            unique[announcement_key(item)] = item

    new_count = 0
    update_count = 0

    for item in announcements:
        key = announcement_key(item)

        if key in unique:
            unique[key] = merge_preserving_verified_data(
                unique[key],
                item
            )
            update_count += 1

        else:
            unique[key] = item
            new_count += 1

    output = {
        "updated": now_iso(),
        "announcements": list(unique.values()),
    }

    save_json(OUTPUT_FILE, output)

    print("SHARE SANKET RESULTS SOURCE ADAPTER")
    print(f"New matching records: {new_count}")
    print(f"Existing records updated: {update_count}")
    print(f"Total unique records: {len(unique)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
