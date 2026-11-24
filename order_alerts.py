#!/usr/bin/env python3
"""NSE + BSE order announcements -> complete Share Sanket alert records.

Official exchange records and filing text are retained. Yahoo Finance fields are
snapshots and are left unavailable when Yahoo does not return a value. No AI
scores or buy/sell recommendations are generated.
"""
import hashlib
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import requests

try:
    from pypdf import PdfReader
except Exception:  # PDF text extraction is best-effort; the filing link is retained.
    PdfReader = None

try:
    import yfinance as yf
except Exception:
    yf = None

IST = timezone(timedelta(hours=5, minutes=30))
SEEN_FILE = Path("seen.json")
ALERT_FILE = Path("alerts.json")
MAX_SEEN = 5000
MAX_PDF_BYTES = 12 * 1024 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

INCLUDE = re.compile(
    r"\b(bagging\s*/?\s*receiving|"
    r"(receiving|receipt|award(ing)?|bagging)\s+of\s+(an?\s+)?(new\s+)?(order|contract)s?|"
    r"orders?\s+(win|won|received|inflow|book)|"
    r"(bagg(ed|s)|secur(ed|es)|win(s)?|won|receiv(ed|es)|awarded|grabs?|clinch(ed|es)?)"
    r"[^.]{0,80}\b(order|contract|project|tender|work)|"
    r"letter\s+of\s+(award|intent|acceptance)|\bLoA\b|\bLoI\b|"
    r"work\s+order|purchase\s+order|new\s+order|"
    r"(order|contract)\s+(worth|valued|of\s+rs|of\s+inr|for)|empanel(l)?ment)",
    re.I,
)
EXCLUDE = re.compile(
    r"(newspaper\s+publication|trading\s+window|loss\s+of\s+share\s+certificate|"
    r"duplicate\s+share|analyst\s*/?\s*investor\s+meet|credit\s+rating|"
    r"cancel(led|lation)\s+of\s+(the\s+)?order)",
    re.I,
)
MONEY = re.compile(
    r"(?P<currency>₹|\bINR\.?|\bRs\.?)\s*"
    r"(?P<number>[0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(?P<unit>crores?|crs?|lakhs?|lacs?|millions?|mn|billions?|bn)?",
    re.I,
)


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def fetch_bse(day, end_day=None):
    """Fetch BSE announcements for an inclusive date range."""
    end_day = end_day or day
    url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    headers = {
        **HEADERS,
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
        "Accept": "application/json, text/plain, */*",
    }
    start_key = day.strftime("%Y%m%d")
    end_key = end_day.strftime("%Y%m%d")
    out = []

    # BSE returns paginated records, normally up to 50 rows per page.
    for page in range(1, 51):
        params = {
            "pageno": page,
            "strCat": "-1",
            "subcategory": "-1",
            "strPrevDate": start_key,
            "strToDate": end_key,
            "strSearch": "P",
            "strscrip": "",
            "strType": "C",
        }
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("Table") or [] if isinstance(payload, dict) else []
        if not rows:
            break

        for row in rows:
            attachment = row.get("ATTACHMENTNAME") or ""
            link = (
                "https://www.bseindia.com/xml-data/corpfiling/AttachLive/" + attachment
                if attachment else ""
            )
            # Keep the exact ID format used by the previous scanner so
            # existing BSE app records are recognised instead of duplicated.
            out.append({
                "id": f"BSE:{row.get('NEWSID') or row.get('SCRIP_CD')}:{row.get('NEWS_DT')}",
                "exchange": "BSE",
                "company": row.get("SLONGNAME") or "",
                "symbol": str(row.get("SCRIP_CD") or ""),
                "title": row.get("HEADLINE") or "",
                "text": row.get("NEWSSUB") or "",
                "category": row.get("SUBCATNAME") or row.get("CATEGORYNAME") or "",
                "time": row.get("NEWS_DT") or "",
                "link": link,
                "source_data": row,
            })

        if len(rows) < 50:
            break
        time.sleep(0.4)

    return out


def fetch_nse(day, end_day=None):
    """Fetch NSE equity announcements for an inclusive date range."""
    end_day = end_day or day
    session = requests.Session()
    session.headers.update({
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    })
    session.get("https://www.nseindia.com", timeout=30)

    start_key = day.strftime("%d-%m-%Y")
    end_key = end_day.strftime("%d-%m-%Y")
    response = session.get(
        "https://www.nseindia.com/api/corporate-announcements",
        params={
            "index": "equities",
            "from_date": start_key,
            "to_date": end_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json()
    out = []

    for row in rows if isinstance(rows, list) else []:
        key = row.get("seq_id") or (
            f"{row.get('symbol')}|{row.get('an_dt')}|{row.get('desc')}"
        )
        out.append({
            "id": f"NSE:{key}",
            "exchange": "NSE",
            "company": row.get("sm_name") or "",
            "symbol": row.get("symbol") or "",
            "title": row.get("desc") or "",
            "text": row.get("attchmntText") or "",
            "category": row.get("desc") or "",
            "time": row.get("an_dt") or row.get("sort_date") or "",
            "link": row.get("attchmntFile") or "",
            "source_data": row,
        })

    return out


def parse_money(text):
    """Find explicit INR values; distinguish order value from per-share prices."""
    found = []
    for match in MONEY.finditer(text or ""):
        number = float(match.group("number").replace(",", ""))
        unit = (match.group("unit") or "").lower()
        left, right = max(0, match.start() - 100), min(len(text), match.end() + 100)
        context = text[left:right]
        unit_factor = 1.0
        unit_name = unit or "rupees"
        if unit.startswith("cr"):
            unit_factor, unit_name = 10_000_000.0, "crore"
        elif unit.startswith("lak") or unit.startswith("lac"):
            unit_factor, unit_name = 100_000.0, "lakh"
        elif unit.startswith("mn") or unit.startswith("million"):
            unit_factor, unit_name = 1_000_000.0, "million"
        elif unit.startswith("bn") or unit.startswith("billion"):
            unit_factor, unit_name = 1_000_000_000.0, "billion"
        per_share = re.search(r"per\s+(share|unit|piece)|\beach\b", context, re.I)
        relevant = re.search(r"order|contract|project|work|aggregate|combined|total|consideration|value|worth|award", context, re.I)
        value = {"text": match.group(0).strip(), "unit": unit_name,
                 "inr": number * unit_factor, "order_related": bool(relevant and not per_share)}
        found.append(value)
    order_values = [x for x in found if x["order_related"] and x["unit"] != "rupees"]
    if not order_values:
        order_values = [x for x in found if x["order_related"]]
    chosen = max(order_values, key=lambda x: x["inr"]) if order_values else None
    return found, chosen


def find_client(text):
    patterns = [
        r"\b(?:new\s+)?order\s+from\s+([A-Z][A-Za-z0-9&.,()' -]{1,70}?)(?=\s+(?:for|worth|valued|to\s+manufacture|including|comprising)\b|[,.;]|$)",
        r"\b(?:client|customer|end\s+user)\s*[:\-]\s*([^,.;\n]{2,80})",
        r"\b(?:awarded\s+by|awarded\s+to\s+the\s+company\s+by|received\s+from)\s+([A-Z][A-Za-z0-9&.,()' -]{1,70}?)(?=\s+(?:for|worth|valued|to\s+manufacture|including|comprising)\b|[,.;]|$)",
    ]
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", re.I):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;")
            if value and value.lower() not in {"the exchange", "the company", "its customer"}:
                matches.append(value)
    return max(matches, key=len) if matches else None


def find_execution(text):
    duration = r"\d+(?:\.\d+)?\s+(?:working\s+)?(?:days?|weeks?|months?|years?)"
    patterns = [
        rf"\b(?:execution|completion|delivery)\s+(?:period|timeline|schedule|duration)[^.;]{{0,50}}?({duration})\b",
        rf"\btime\s+period[^.;]{{0,140}}?(?:to\s+be\s+)?(?:executed|completed|delivered)[^.;]{{0,50}}?({duration})\b",
        rf"\b({duration})\s+for\s+completion\s+of\s+[^.;]{{0,90}}",
        rf"\b(?:within|over|in)\s+({duration})\b",
        r"\b(?:by|before|until)\s+(?:March|April|May|June|July|August|September|October|November|December|January|February)\s+\d{4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if match:
            value = match.group(1) if match.lastindex else match.group(0)
            return re.sub(r"\s+", " ", value).strip(" ,.;")
    return None


def undisclosed_order_value(text):
    match = re.search(r"(?:consideration|size|value)[\s\S]{0,180}?\bnot\s+disclosed\b[\s\S]{0,160}", text or "", re.I)
    return re.sub(r"\s+", " ", match.group(0)).strip(" ,.;") if match else None


def yahoo_search(company):
    if not company:
        return None
    try:
        response = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                                params={"q": company, "quotesCount": 12, "newsCount": 0},
                                headers=HEADERS, timeout=12)
        response.raise_for_status()
        quotes = response.json().get("quotes") or []
        candidates = [q for q in quotes if q.get("quoteType") == "EQUITY" and str(q.get("symbol", "")).endswith((".NS", ".BO"))]
        return candidates[0].get("symbol") if candidates else None
    except Exception as exc:
        log("Yahoo symbol search unavailable:", exc)
        return None


def yahoo_market_data(item, cache):
    if not yf:
        return {}, None
    raw_symbol = str(item.get("symbol") or "").strip()
    ticker_symbol = raw_symbol + ".NS" if raw_symbol and not raw_symbol.isdigit() else None
    if not ticker_symbol:
        ticker_symbol = yahoo_search(item.get("company"))
    if not ticker_symbol:
        return {}, None
    if ticker_symbol in cache:
        return cache[ticker_symbol], ticker_symbol
    result = {}
    try:
        info = yf.Ticker(ticker_symbol).info or {}
        for key in ("regularMarketPrice", "currentPrice", "marketCap", "trailingPE", "forwardPE", "priceToBook",
                    "enterpriseValue", "trailingEps", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                    "regularMarketChangePercent", "currency", "sector", "industry", "regularMarketTime"):
            if info.get(key) is not None:
                result[key] = info.get(key)
    except Exception as exc:
        log("Yahoo Finance snapshot unavailable for", ticker_symbol, repr(exc))
    cache[ticker_symbol] = result
    return result, ticker_symbol


def extract_pdf(item):
    link = item.get("link") or ""
    host = re.sub(r"^https?://", "", link).split("/", 1)[0].lower()
    if not link.startswith("https://") or not host.endswith(("nseindia.com", "nseindia.in", "bseindia.com", "bseindia.in")):
        return "", "No exchange PDF attachment was available."
    try:
        response = requests.get(link, headers=HEADERS, timeout=25, stream=True)
        response.raise_for_status()
        content = response.raw.read(MAX_PDF_BYTES + 1)
        if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF"):
            return "", "Filing link retained; attachment was not a readable PDF."
        if not PdfReader:
            return "", "Filing link retained; PDF text extraction library unavailable."
        reader = PdfReader(BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages).strip()
        if text:
            return text, f"Extracted text from {len(pages)} PDF page(s). Check the source filing for tables or scanned pages."
        return "", f"PDF has {len(pages)} page(s), but no selectable text; open the source filing."
    except Exception as exc:
        log("Filing PDF extraction failed:", repr(exc))
        return "", "Filing link retained; automatic PDF text extraction was unavailable."


def raw_details(source_data, exchange):
    details = []
    for key, value in (source_data or {}).items():
        label = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key))
        label = re.sub(r"[_\-]+", " ", label).strip().title()
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if value == "":
            value = None
        details.append({"label": f"Exchange field · {label}", "value": value, "source": exchange})
    return details


def clean_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def format_inr(value):
    if value is None:
        return None
    return "₹" + format(value, ",.2f")


def order_alert(item, market_cache):
    pdf_text, pdf_status = extract_pdf(item)
    full_text = "\n\n".join(part for part in (item.get("title"), item.get("text"), pdf_text) if part)
    amounts, order_amount = parse_money(full_text)
    market, yahoo_ticker = yahoo_market_data(item, market_cache)
    currency = str(market.get("currency") or "").upper()
    price = clean_number(market.get("regularMarketPrice") or market.get("currentPrice"))
    market_cap = clean_number(market.get("marketCap"))
    cap_cr = market_cap / 10_000_000 if market_cap is not None and currency == "INR" else None
    order_cr = order_amount["inr"] / 10_000_000 if order_amount else None
    ratio = (order_cr / cap_cr * 100) if order_cr is not None and cap_cr and cap_cr > 0 else None
    if order_cr is None:
        size_band, subfolder = None, None
    elif order_cr >= 100:
        size_band, subfolder = "Big (₹100 crore or more)", "big"
    elif order_cr >= 10:
        size_band, subfolder = "Medium (₹10 crore to under ₹100 crore)", "medium"
    else:
        size_band, subfolder = "Small (under ₹10 crore)", "small"

    details = [
        {"label": "Source exchange", "value": item.get("exchange"), "source": item.get("exchange")},
        {"label": "Company", "value": item.get("company"), "source": item.get("exchange")},
        {"label": "Exchange symbol / code", "value": item.get("symbol"), "source": item.get("exchange")},
        {"label": "Announcement category", "value": item.get("category"), "source": item.get("exchange")},
        {"label": "Filing title", "value": item.get("title"), "source": item.get("exchange")},
        {"label": "Order / project description", "value": item.get("text"), "source": item.get("exchange")},
        {"label": "Client / counterparty", "value": find_client(full_text), "source": item.get("exchange")},
        {"label": "Reported order value", "value": order_amount["text"] if order_amount else undisclosed_order_value(full_text), "source": item.get("exchange")},
        {"label": "Order size band", "value": size_band, "source": "Rule based only on disclosed order value"},
        {"label": "Execution / delivery period", "value": find_execution(full_text), "source": item.get("exchange")},
        {"label": "Filing date and time", "value": item.get("time"), "source": item.get("exchange")},
        {"label": "PDF text extraction", "value": pdf_status, "source": item.get("exchange")},
        {"label": "Yahoo Finance symbol", "value": yahoo_ticker, "source": "Yahoo Finance"},
        {"label": "Yahoo Finance company page", "value": f"https://finance.yahoo.com/quote/{yahoo_ticker}/" if yahoo_ticker else None, "source": "Yahoo Finance"},
        {"label": "Market data snapshot captured", "value": datetime.now(IST).isoformat(timespec="seconds"), "source": "Yahoo Finance"},
        {"label": "Price at snapshot", "value": format_inr(price) if price is not None else None, "source": "Yahoo Finance"},
        {"label": "Market capitalization", "value": format_inr(market_cap) if market_cap is not None else None, "source": "Yahoo Finance"},
        {"label": "Market capitalization (INR crore)", "value": f"₹{cap_cr:,.2f} crore" if cap_cr is not None else None, "source": "Yahoo Finance"},
        {"label": "Trailing P/E", "value": f"{clean_number(market.get('trailingPE')):.2f}" if clean_number(market.get("trailingPE")) is not None else None, "source": "Yahoo Finance"},
        {"label": "Forward P/E", "value": f"{clean_number(market.get('forwardPE')):.2f}" if clean_number(market.get("forwardPE")) is not None else None, "source": "Yahoo Finance"},
        {"label": "Price-to-book", "value": f"{clean_number(market.get('priceToBook')):.2f}" if clean_number(market.get("priceToBook")) is not None else None, "source": "Yahoo Finance"},
        {"label": "Trailing EPS", "value": format_inr(clean_number(market.get("trailingEps"))), "source": "Yahoo Finance"},
        {"label": "Enterprise value", "value": format_inr(clean_number(market.get("enterpriseValue"))), "source": "Yahoo Finance"},
        {"label": "52-week high", "value": format_inr(clean_number(market.get("fiftyTwoWeekHigh"))), "source": "Yahoo Finance"},
        {"label": "52-week low", "value": format_inr(clean_number(market.get("fiftyTwoWeekLow"))), "source": "Yahoo Finance"},
        {"label": "Sector", "value": market.get("sector"), "source": "Yahoo Finance"},
        {"label": "Industry", "value": market.get("industry"), "source": "Yahoo Finance"},
        {"label": "Order value / market cap", "value": f"{ratio:.4f}%" if ratio is not None else None,
         "source": "Calculated only when reported INR order value and INR market cap are both available"},
        {"label": "All monetary figures found in filing", "value": "; ".join(x["text"] for x in amounts) if amounts else None,
         "source": item.get("exchange")},
        {"label": "Complete extracted filing text", "value": pdf_text or None, "source": item.get("exchange")},
    ]
    details.extend(raw_details(item.get("source_data"), item.get("exchange")))
    now = datetime.now(IST).isoformat(timespec="seconds")
    raw_id = str(item.get("id") or "")
    alert_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]
    change = clean_number(market.get("regularMarketChangePercent"))
    title = item.get("title") or item.get("category") or "Order win announcement"
    return {
        "id": alert_id, "parser_version": 3, "time": now, "folder": "orders", "subfolder": subfolder,
        "symbol": str(item.get("symbol") or ""), "company": item.get("company") or "",
        "title": title, "price": price, "change_pct": change, "details": details,
        "link": item.get("link") or "", "source_data": item.get("source_data") or {},
        "filing_text": full_text, "source_id": raw_id,
    }


def load_seen():
    try:
        value = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else list(value.keys()) if isinstance(value, dict) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_seen(values):
    SEEN_FILE.write_text(json.dumps(values[-MAX_SEEN:], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def ensure_orders_folder(folders):
    folder = next((f for f in folders if f.get("id") == "orders"), None)
    if folder is None:
        folders.append({"id": "orders", "name": "Orders", "subfolders": []})
        folder = folders[-1]
    existing = {x.get("id") for x in folder.get("subfolders", [])}
    for ident, name in (("big", "Big orders"), ("medium", "Medium orders"), ("small", "Small orders")):
        if ident not in existing:
            folder.setdefault("subfolders", []).append({"id": ident, "name": name})


def save_app_alert(alert):
    """Insert or update exactly one app record per exchange announcement."""
    try:
        data = json.loads(ALERT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {"sample": False, "folders": [], "alerts": []}

    data["sample"] = False
    folders = data.setdefault("folders", [])
    ensure_orders_folder(folders)
    alerts = data.setdefault("alerts", [])

    source_id = alert.get("source_id")
    matches = [
        existing for existing in alerts
        if existing.get("id") == alert.get("id")
        or (
            source_id
            and existing.get("source_id") == source_id
        )
    ]

    if matches:
        # Keep the first-seen timestamp, even if details are refreshed later.
        alert["time"] = matches[0].get("time") or alert.get("time")

        # If more than one old copy exists, collapse them into one.
        if len(matches) == 1 and matches[0] == alert:
            return False
        for existing in matches:
            alerts.remove(existing)

    alerts.append(alert)
    data["updated"] = datetime.now(IST).isoformat(timespec="seconds")
    ALERT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def main():
    today = datetime.now(IST).date()

    # Re-read a rolling 7-calendar-day window. This backfills missed scans
    # while the exchange APIs still expose those announcements by date.
    start_day = today - timedelta(days=7)
    items = []
    successful_sources = 0

    for name, source in (("BSE", fetch_bse), ("NSE", fetch_nse)):
        try:
            rows = source(start_day, today)
            log(
                f"{name}: {len(rows)} announcements "
                f"from {start_day} through {today}"
            )
            items.extend(rows)
            successful_sources += 1
        except Exception as exc:
            log(f"{name} fetch failed: {exc!r}")

    if successful_sources == 0:
        log("Both exchange sources failed; leaving Share Sanket data unchanged.")
        return 1

    # Remove duplicates caused by overlapping exchange API pages.
    unique_items = {}
    for item in items:
        unique_items[item["id"]] = item
    items = list(unique_items.values())

    try:
        current_data = json.loads(ALERT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        current_data = {}

    existing_alerts = current_data.get("alerts", [])
    existing_by_source = {
        alert.get("source_id"): alert
        for alert in existing_alerts
        if alert.get("source_id")
    }
    # Also recognise earlier records by the deterministic alert ID, in case
    # a previous app record did not contain source_id for some reason.
    existing_alert_ids = {
        alert.get("id") for alert in existing_alerts if alert.get("id")
    }

    hits = [
        item for item in items
        if INCLUDE.search(f"{item.get('title', '')} {item.get('text', '')}")
        and not EXCLUDE.search(f"{item.get('title', '')} {item.get('text', '')}")
    ]

    def app_alert_id(source_id):
        return hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:24]

    # Recover every qualifying announcement not present in alerts.json.
    # seen.json is deliberately NOT used as the authority for app delivery:
    # older runs may have marked an ID seen before successfully saving it.
    new_hits = [
        item for item in hits
        if (
            item["id"] not in existing_by_source
            and app_alert_id(item["id"]) not in existing_alert_ids
        )
    ]

    # Reprocess older-format records once so their filing fields can be refreshed.
    refresh_pdf = []
    for item in hits:
        old_alert = existing_by_source.get(item["id"])
        if not old_alert or not item.get("link"):
            continue

        if old_alert.get("parser_version", 0) < 3:
            refresh_pdf.append(item)

    to_process = new_hits + refresh_pdf
    log(
        f"{len(hits)} qualifying order/contract announcements in the window; "
        f"{len(new_hits)} missing from the app; "
        f"{len(refresh_pdf)} older records to refresh"
    )

    market_cache = {}
    changed = 0
    failed_ids = set()

    for item in to_process:
        try:
            alert = order_alert(item, market_cache)
            if save_app_alert(alert):
                changed += 1
        except Exception as exc:
            failed_ids.add(item["id"])
            log("Could not add/update order alert:", item.get("id"), repr(exc))

    # Keep seen.json as a compact scanner checkpoint, but never let it prevent
    # a missing application record from being recovered on the next scan.
    seen = load_seen()
    fetched_ids = [item["id"] for item in items]
    seen = list(dict.fromkeys(seen + fetched_ids))
    save_seen(seen)

    log(f"Share Sanket order alerts added or updated: {changed}")
    if failed_ids:
        log(f"Order records that need retry: {len(failed_ids)}")

    # Telegram bots remain independent; this script updates the app feed only.
    return 0

if __name__ == "__main__":
    sys.exit(main())


