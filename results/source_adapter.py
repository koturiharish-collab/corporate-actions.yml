
"""
Share Sanket - Results Source Adapter

Processes official NSE financial-results announcements and extracts quarterly
financial data from official NSE PDF attachments.

Input:
    results/inbox/*.json

Output:
    results/source_announcements.json

Safety:
- NSE only.
- Accepts only Outcome of Board Meeting announcements mentioning results.
- Downloads PDFs only from approved NSE domains.
- Keeps standalone and consolidated figures separate.
- Does not convert missing/dash values to zero.
- Preserves previously verified non-null financial figures.
- PDF extraction failures do not stop the complete results workflow.
"""

import json
import math
import time
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

# This file must be in the same results/ folder as this adapter.
from results_pdf_extractor import extract_results_pdf


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

NSE_FILINGS_PAGE = (
    "https://www.nseindia.com/companies-listing/"
    "corporate-filings-announcements"
)
NSE_ANNOUNCEMENTS_API = "https://www.nseindia.com/api/corporate-announcements"
NSE_LOOKBACK_DAYS = 7
MAX_PDF_BYTES = 25 * 1024 * 1024

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_FILINGS_PAGE,
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
SCOPES = ("standalone", "consolidated")

FINANCIAL_FIELDS = [
    "revenue",
    "net_profit",
    "eps",
    "operating_profit",
    "gross_profit",
    "ebitda",
    "ebitda_margin",
    "total_debt",
    "cash_flow",
    "previous_year_revenue",
    "previous_year_net_profit",
    "previous_year_eps",
    "revenue_yoy",
    "net_profit_yoy",
    "eps_yoy",
    "revenue_qoq",
    "net_profit_qoq",
    "eps_qoq",
    "ebitda_yoy",
    "ebitda_qoq",
    "net_margin",
    "operating_margin",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path.name}: {exc}") from exc


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
        return parsed.scheme == "https" and hostname in ALLOWED_DOMAINS
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
        result = float(str(value).replace(",", "").replace("₹", "").strip())
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalize_financials(financials):
    if not isinstance(financials, dict):
        financials = {}
    normalized = {
        field: normalize_number(financials.get(field))
        for field in FINANCIAL_FIELDS
    }
    normalized["profit_turnaround"] = bool(
        financials.get("profit_turnaround", False)
    )
    normalized["revenue_growth_available"] = (
        normalized["revenue_yoy"] is not None
    )
    return normalized


def normalize_financial_scopes(item):
    supplied = item.get("financials_by_scope", {})
    if not isinstance(supplied, dict):
        supplied = {}
    output = {
        scope: normalize_financials(supplied.get(scope, {}))
        for scope in SCOPES
    }
    legacy = normalize_financials(item.get("financials", {}))
    return output, legacy


def is_results_announcement(item):
    subject = str(item.get("subject", "")).lower().strip()
    details = str(item.get("details", "")).lower().strip()
    if BOARD_MEETING_SUBJECT not in subject:
        return False
    return any(keyword in details for keyword in RESULT_KEYWORDS)


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
        "symbol", "company", "subject", "details",
        "published_at", "source", "source_url",
    ]
    for field in required:
        if not str(item.get(field, "")).strip():
            return False, f"Missing field: {field}"

    if str(item["source"]).strip().upper() != "NSE":
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
        str(item.get("subject", "")) + " " + str(item.get("details", ""))
    )
    financials_by_scope, legacy_financials = normalize_financial_scopes(item)

    expectations = item.get("expectations", {})
    if not isinstance(expectations, dict) or not expectations.get("source"):
        expectations = {}

    normalized = {
        "symbol": str(item["symbol"]).strip().upper(),
        "company": str(item["company"]).strip(),
        "period": str(item.get("period", "Not Specified")).strip(),
        "published_at": str(item["published_at"]).strip(),
        "source": "NSE",
        "subject": str(item["subject"]).strip(),
        "details": str(item["details"]).strip(),
        "source_url": str(item["source_url"]).strip(),
        "attachment_url": str(item.get("attachment_url", "")).strip(),
        "result_scope": classify_result_scope(combined_text),
        "financials_by_scope": financials_by_scope,
        "financials": legacy_financials,
        "expectations": expectations,
        "data_status": item.get(
            "data_status",
            "Announcement detected - figures not verified",
        ),
        "financial_units": item.get("financial_units", "Not specified"),
    }

    # Preserve extraction provenance when re-reading a verified inbox record.
    for key in ("pdf_extraction", "primary_scope"):
        if key in item:
            normalized[key] = item[key]

    return normalized


def announcement_key(item):
    return (
        item.get("symbol"),
        item.get("period"),
        item.get("published_at"),
        item.get("source_url"),
    )


def merge_preserving_verified_data(old, new):
    """Never replace an existing non-null financial value with None."""
    merged = dict(old)

    protected = {"financials", "financials_by_scope"}
    for key, value in new.items():
        if key not in protected and value not in (None, ""):
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


def _scope_to_adapter_fields(scope_data):
    """Map extractor output to the source-adapter's standard financial schema."""
    if not isinstance(scope_data, dict):
        return {}

    def period_value(field, period):
        values = scope_data.get(field, {})
        return values.get(period) if isinstance(values, dict) else None

    comparisons = scope_data.get("comparisons", {})
    if not isinstance(comparisons, dict):
        comparisons = {}

    def comparison(field, kind):
        entry = comparisons.get(field, {})
        return entry.get(kind) if isinstance(entry, dict) else None

    q2_revenue = period_value("revenue_cr", "q2")
    q2_pat = period_value("pat", "q2")
    q2_eps = period_value("eps", "q2")
    q2_ebitda = period_value("ebitda", "q2")
    q2_ebitda_margin = period_value("ebitda_margin_pct", "q2")

    mapped = {
        "revenue": q2_revenue,
        "net_profit": q2_pat,
        "eps": q2_eps,
        "ebitda": q2_ebitda,
        "ebitda_margin": q2_ebitda_margin,
        # Keep the legacy operating_margin field populated for compatibility.
        # The explicit ebitda_margin field is the canonical metric.
        "operating_margin": q2_ebitda_margin,
        "previous_year_revenue": period_value("revenue_cr", "q2_prev_year"),
        "previous_year_net_profit": period_value("pat", "q2_prev_year"),
        "previous_year_eps": period_value("eps", "q2_prev_year"),
        "revenue_yoy": comparison("revenue", "yoy_pct"),
        "net_profit_yoy": comparison("pat", "yoy_pct"),
        "eps_yoy": comparison("eps", "yoy_pct"),
        "revenue_qoq": comparison("revenue", "qoq_pct"),
        "net_profit_qoq": comparison("pat", "qoq_pct"),
        "eps_qoq": comparison("eps", "qoq_pct"),
        "ebitda_yoy": comparison("ebitda", "yoy_pct"),
        "ebitda_qoq": comparison("ebitda", "qoq_pct"),
    }

    # Keep only fields that were actually identified in the PDF.
    return {key: value for key, value in mapped.items() if value is not None}


def extract_pdf_financials(item):
    """
    Download and parse one official NSE PDF.

    Existing verified figures win over automatically extracted figures.
    A dash or an unrecognised table row remains None (Not Available).
    """
    pdf_url = item.get("attachment_url") or item.get("source_url")
    if not is_official_url(pdf_url):
        raise ValueError("No approved official NSE PDF URL")

    response = requests.get(
        pdf_url,
        headers=NSE_HEADERS,
        timeout=(20, 45),
        stream=True,
    )
    response.raise_for_status()

    pdf_bytes = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        pdf_bytes.extend(chunk)
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise ValueError("PDF exceeds the 25 MB safety limit")

    if not bytes(pdf_bytes).startswith(b"%PDF"):
        raise ValueError("NSE attachment did not return a valid PDF")

    with tempfile.NamedTemporaryFile(suffix=".pdf") as temp_file:
        temp_file.write(pdf_bytes)
        temp_file.flush()
        extracted = extract_results_pdf(temp_file.name)

    extracted_scopes = extracted.get("financials_by_scope", {})
    if not isinstance(extracted_scopes, dict):
        raise ValueError("Extractor returned an invalid financial-scope structure")

    old_scopes = item.get("financials_by_scope", {})
    if not isinstance(old_scopes, dict):
        old_scopes = {}

    parsed_scopes = {
        scope: _scope_to_adapter_fields(extracted_scopes.get(scope, {}))
        for scope in SCOPES
    }

    # Existing inbox records may use INR millions while this PDF parser
    # normalises statement values to INR crore. Never mix units in one record.
    existing_has_values = any(
        isinstance(old_scopes.get(scope), dict)
        and any(
            value is not None
            for value in old_scopes.get(scope, {}).values()
        )
        for scope in SCOPES
    )
    existing_units = str(item.get("financial_units", "")).lower()
    unit_conflict = (
        existing_has_values
        and "million" in existing_units
        and "crore" not in existing_units
    )

    updated_scopes = {}
    for scope in SCOPES:
        existing = old_scopes.get(scope, {})
        if not isinstance(existing, dict):
            existing = {}
        combined = dict(existing)

        # Only fill blanks when the existing record uses compatible units.
        if not unit_conflict:
            for field, value in parsed_scopes[scope].items():
                if combined.get(field) is None:
                    combined[field] = value

        updated_scopes[scope] = combined

    if not any(parsed_scopes.values()):
        raise ValueError("No reliable financial figures found in the PDF")

    if not unit_conflict:
        item["financials_by_scope"] = updated_scopes

        # Keep the existing primary scope convention: consolidated first.
        primary = "consolidated"
        if not any(
            updated_scopes[primary].get(field) is not None
            for field in ("revenue", "net_profit", "eps", "ebitda")
        ):
            primary = "standalone"

        legacy = item.get("financials", {})
        if not isinstance(legacy, dict):
            legacy = {}
        merged_legacy = dict(legacy)
        for field, value in parsed_scopes[primary].items():
            if merged_legacy.get(field) is None:
                merged_legacy[field] = value
        item["financials"] = merged_legacy
        item["financial_units"] = "INR crore (EPS in INR/share)"

    item["pdf_extraction"] = {
        "status": "extracted",
        "source_url": pdf_url,
        "pages": extracted.get("pages", {}),
        "source_file": extracted.get("source_file"),
        "method": "pypdf text extraction",
        "missing_values_policy": "Dashes and unrecognised rows remain unavailable",
        "unit_conflict": unit_conflict,
        "extracted_financials_by_scope": parsed_scopes,
    }
    item["data_status"] = (
        "Official NSE PDF downloaded; figures extracted where table rows "
        "were reliably recognised. Existing verified values preserved."
    )
    return item


def enrich_announcement_from_pdf(item):
    """Fail safely: one unreadable PDF must not stop other announcements."""
    existing_status = item.get("pdf_extraction", {})
    if isinstance(existing_status, dict) and existing_status.get("status") == "extracted":
        return item

    try:
        return extract_pdf_financials(item)
    except Exception as exc:
        print(f"PDF extraction skipped for {item.get('symbol', 'UNKNOWN')}: {exc}")
        item["pdf_extraction"] = {
            "status": "not_extracted",
            "source_url": item.get("attachment_url") or item.get("source_url"),
            "reason": str(exc)[:500],
        }
        # Keep manually supplied / previously verified values intact.
        return item


def fetch_nse_announcements():
    """Fetch recent NSE announcements and retain only qualifying results."""
    start = date.today() - timedelta(days=NSE_LOOKBACK_DAYS)
    end = date.today()
    last_error = None
    max_attempts = 4

    for attempt in range(1, max_attempts + 1):
        session = None
        try:
            session = requests.Session()
            session.headers.update(NSE_HEADERS)

            filings_page = session.get(NSE_FILINGS_PAGE, timeout=(15, 25))
            filings_page.raise_for_status()

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
                payload = payload.get("data", payload.get("rows", []))
            if not isinstance(payload, list):
                raise ValueError("NSE returned an unexpected announcements response")

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

                if not all((subject, details, symbol, company, published_at, pdf_url)):
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
                        "NSE announcement detected; financial figures not yet verified"
                    ),
                    "financial_units": "Not specified",
                }

                if not is_results_announcement(announcement):
                    rejected_count += 1
                    continue
                announcements.append(announcement)

            print(
                f"NSE announcements fetched: {len(payload)} "
                f"({start.isoformat()} to {end.isoformat()})"
            )
            print(f"Matching financial-results announcements: {len(announcements)}")
            print(f"Rejected unrelated announcements: {rejected_count}")
            return announcements

        except Exception as exc:
            last_error = exc
            print(f"NSE fetch attempt {attempt}/{max_attempts} failed: {exc}")
            if attempt < max_attempts:
                time.sleep(attempt * 5)
        finally:
            if session is not None:
                session.close()

    raise RuntimeError(
        f"Unable to fetch NSE announcements after {max_attempts} attempts: {last_error}"
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
            raise ValueError(f"Invalid JSON structure in {path.name}")

        if not isinstance(announcements, list):
            raise ValueError(f"'announcements' must be a list in {path.name}")

        for item in announcements:
            try:
                result = normalize_announcement(item)
                if result is not None:
                    records.append(result)
                else:
                    print("SKIPPED: Not a qualifying financial-results announcement")
            except ValueError as exc:
                print(f"SKIPPED {path.name}: {exc}")

    return records


def main():
    # Read manually verified inbox records and append filtered live NSE records.
    announcements = read_inbox()

    try:
        announcements.extend(fetch_nse_announcements())
    except Exception as exc:
        print(
            "WARNING: Live NSE refresh failed; processing inbox records only: "
            f"{exc}"
        )

    existing = {"announcements": []}
    if OUTPUT_FILE.exists():
        existing = load_json(OUTPUT_FILE)

    previous = existing.get("announcements", [])
    if not isinstance(previous, list):
        raise ValueError("Existing source_announcements.json is invalid")

    unique = {}
    for item in previous:
        if isinstance(item, dict):
            unique[announcement_key(item)] = item

    new_count = 0
    update_count = 0
    extracted_count = 0
    failed_count = 0

    for item in announcements:
        key = announcement_key(item)

        # Avoid repeating downloads for records already successfully extracted.
        old_item = unique.get(key)
        if isinstance(old_item, dict):
            old_status = old_item.get("pdf_extraction", {})
            if isinstance(old_status, dict) and old_status.get("status") == "extracted":
                item = merge_preserving_verified_data(old_item, item)
                item["pdf_extraction"] = old_status
            else:
                item = enrich_announcement_from_pdf(item)
        else:
            item = enrich_announcement_from_pdf(item)

        status = item.get("pdf_extraction", {})
        if isinstance(status, dict):
            if status.get("status") == "extracted":
                extracted_count += 1
            elif status.get("status") == "not_extracted":
                failed_count += 1

        if key in unique:
            unique[key] = merge_preserving_verified_data(unique[key], item)
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
    print(f"PDFs extracted this run: {extracted_count}")
    print(f"PDF extraction failures: {failed_count}")
    print(f"Total unique records: {len(unique)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
