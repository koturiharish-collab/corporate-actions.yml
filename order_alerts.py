
#!/usr/bin/env python3
"""NSE + BSE order announcements -> Share Sanket alerts."""

import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import requests

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    import yfinance as yf
except Exception:
    yf = None


IST = timezone(timedelta(hours=5, minutes=30))

SEEN_FILE = Path("seen.json")
ALERT_FILE = Path("alerts.json")

MAX_SEEN = 5000
MAX_ALERTS = 500
MAX_PDF_BYTES = 12 * 1024 * 1024
LOOKBACK_DAYS = 2

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------
# ORDER FILTER
# ---------------------------------------------------------

INCLUDE = re.compile(
    r"\b("
    # Exchange category: Action(s) initiated or orders passed
    r"actions?\s*(?:\(s\))?\s+(?:initiated|taken)"
    r"\s+(?:or\s+)?orders?\s+passed|"
    r"orders?\s*(?:\(s\))?\s+passed|"

    # Order and contract awards
    r"bagging\s*/?\s*receiving|"
    r"(?:receiving|receipt|award(?:ing)?|bagging)\s+of\s+"
    r"(?:an?\s+)?(?:new\s+)?(?:orders?|contracts?)"
    r"(?:\s*\(s\))?|"

    r"orders?\s+(?:win|won|received|inflow|booked)|"

    # Orders secured or received
    r"(?:bagged|bags|secured|secures|wins?|won|"
    r"received|receives|awarded|grabs?|clinched|clinch)"
    r"[^.]{0,100}\b(?:orders?|contracts?|projects?|tenders?|work)|"

    # Letters and purchase orders
    r"letter\s+of\s+(?:award|intent|acceptance)|"
    r"\bLoA\b|\bLoI\b|"
    r"work\s+order|purchase\s+order|new\s+order|"

    # Order value and empanelment
    r"(?:order|contract)\s+(?:worth|valued|of\s+rs|of\s+inr|for)|"
    r"empanel(?:ment|led|s)?"
    r")",
    re.I,
)

EXCLUDE = re.compile(
    r"(newspaper\s+publication|trading\s+window|"
    r"loss\s+of\s+share\s+certificate|"
    r"duplicate\s+share|analyst\s*/?\s*investor\s+meet|"
    r"credit\s+rating|cancel(?:led|lation)\s+of\s+"
    r"(?:the\s+)?order)",
    re.I,
)

MONEY = re.compile(
    r"(?P<currency>₹|\bINR\.?|\bRs\.?)\s*"
    r"(?P<number>[0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(?P<unit>crores?|crs?|lakhs?|lacs?|millions?|mn|billions?|bn)?",
    re.I,
)


def log(*args):
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    print(stamp, *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------
# ORDER CLASSIFICATION AND DIAGNOSTICS
# ---------------------------------------------------------

def filing_text(item):
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "text", "category")
    )


def order_filter_reason(item):
    text = filing_text(item)

    if EXCLUDE.search(text):
        return "excluded"

    if INCLUDE.search(text):
        return "order"

    return "not_matched"


def is_order_filing(item):
    return order_filter_reason(item) == "order"


# ---------------------------------------------------------
# FETCH BSE
# ---------------------------------------------------------

def fetch_bse(start, end):
    url = (
        "https://api.bseindia.com/"
        "BseIndiaAPI/api/AnnSubCategoryGetData/w"
    )

    headers = {
        **HEADERS,
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
        "Accept": "application/json, text/plain, */*",
    }

    out = []

    for page in range(1, 51):
        params = {
            "pageno": page,
            "strCat": "-1",
            "subcategory": "-1",
            "strPrevDate": start.strftime("%Y%m%d"),
            "strToDate": end.strftime("%Y%m%d"),
            "strSearch": "P",
            "strscrip": "",
            "strType": "C",
        }

        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=30
            )
            response.raise_for_status()
            payload = response.json()

        except requests.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None else "unknown"
            )
            log(f"BSE HTTP error {status}: {exc}")
            raise

        rows = payload.get("Table") if isinstance(payload, dict) else None
        rows = rows or []

        if not rows:
            break

        for row in rows:
            attachment = row.get("ATTACHMENTNAME") or ""

            link = (
                "https://www.bseindia.com/xml-data/corpfiling/"
                "AttachLive/" + attachment
                if attachment else ""
            )

            out.append({
                "id": (
                    f"BSE:{row.get('NEWSID') or row.get('SCRIP_CD')}:"
                    f"{row.get('NEWS_DT')}"
                ),
                "exchange": "BSE",
                "company": row.get("SLONGNAME") or "",
                "symbol": str(row.get("SCRIP_CD") or ""),
                "title": row.get("HEADLINE") or "",
                "text": row.get("NEWSSUB") or "",
                "category": (
                    row.get("SUBCATNAME")
                    or row.get("CATEGORYNAME") or ""
                ),
                "time": row.get("NEWS_DT") or "",
                "link": link,
                "source_data": row,
            })

        if len(rows) < 50:
            break

        time.sleep(0.4)

    return out


# ---------------------------------------------------------
# FETCH NSE
# ---------------------------------------------------------

def fetch_nse(start, end):
    session = requests.Session()

    session.headers.update({
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": (
            "https://www.nseindia.com/"
            "companies-listing/corporate-filings-announcements"
        ),
    })

    session.get("https://www.nseindia.com", timeout=30)

    response = session.get(
        "https://www.nseindia.com/api/corporate-announcements",
        params={
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date": end.strftime("%d-%m-%Y"),
        },
        timeout=30,
    )

    response.raise_for_status()
    rows = response.json()

    out = []

    for row in rows if isinstance(rows, list) else []:
        key = (
            row.get("seq_id")
            or f"{row.get('symbol')}|{row.get('an_dt')}|{row.get('desc')}"
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


# ---------------------------------------------------------
# PARSING
# ---------------------------------------------------------

def parse_money(text):
    found = []

    for match in MONEY.finditer(text or ""):
        number = float(match.group("number").replace(",", ""))
        unit = (match.group("unit") or "").lower()

        left = max(0, match.start() - 100)
        right = min(len(text), match.end() + 100)
        context = text[left:right]

        unit_factor = 1.0
        unit_name = unit or "rupees"

        if unit.startswith("cr"):
            unit_factor, unit_name = 10_000_000.0, "crore"
        elif unit.startswith(("lak", "lac")):
            unit_factor, unit_name = 100_000.0, "lakh"
        elif unit.startswith(("mn", "million")):
            unit_factor, unit_name = 1_000_000.0, "million"
        elif unit.startswith(("bn", "billion")):
            unit_factor, unit_name = 1_000_000_000.0, "billion"

        per_share = re.search(
            r"per\s+(share|unit|piece)|\beach\b", context, re.I
        )

        relevant = re.search(
            r"order|contract|project|work|aggregate|combined|"
            r"total|consideration|value|worth|award",
            context,
            re.I,
        )

        found.append({
            "text": match.group(0).strip(),
            "unit": unit_name,
            "inr": number * unit_factor,
            "order_related": bool(relevant and not per_share),
        })

    order_values = [
        x for x in found
        if x["order_related"] and x["unit"] != "rupees"
    ]

    if not order_values:
        order_values = [x for x in found if x["order_related"]]

    chosen = max(order_values, key=lambda x: x["inr"]) if order_values else None

    return found, chosen


def find_client(text):
    patterns = [
        r"\b(?:new\s+)?order\s+from\s+"
        r"([A-Z][A-Za-z0-9&.,()' -]{1,70}?)"
        r"(?=\s+(?:for|worth|valued|to\s+manufacture|including|"
        r"comprising)\b|[,.;]|$)",

        r"\b(?:client|customer|end\s+user)\s*[:\-]\s*([^,.;\n]{2,80})",

        r"\b(?:awarded\s+by|awarded\s+to\s+the\s+company\s+by|"
        r"received\s+from)\s+"
        r"([A-Z][A-Za-z0-9&.,()' -]{1,70}?)"
        r"(?=\s+(?:for|worth|valued|to\s+manufacture|including|"
        r"comprising)\b|[,.;]|$)",
    ]

    matches = []

    for pattern in patterns:
        for match in re.finditer(pattern, text or "", re.I):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" ,.;")

            if value and value.lower() not in {
                "the exchange", "the company", "its customer"
            }:
                matches.append(value)

    return max(matches, key=len) if matches else None


def find_execution(text):
    duration = (
        r"\d+(?:\.\d+)?\s+"
        r"(?:working\s+)?(?:days?|weeks?|months?|years?)"
    )

    patterns = [
        rf"\b(?:execution|completion|delivery)\s+"
        rf"(?:period|timeline|schedule|duration)[^.;]{{0,50}}?"
        rf"({duration})\b",

        rf"\btime\s+period[^.;]{{0,140}}?"
        rf"(?:to\s+be\s+)?(?:executed|completed|delivered)"
        rf"[^.;]{{0,50}}?({duration})\b",

        rf"\b({duration})\s+for\s+completion\s+of\s+[^.;]{{0,90}}",

        rf"\b(?:within|over|in)\s+({duration})\b",

        r"\b(?:by|before|until)\s+"
        r"(?:March|April|May|June|July|August|September|October|"
        r"November|December|January|February)\s+\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)

        if match:
            value = match.group(1) if match.lastindex else match.group(0)
            return re.sub(r"\s+", " ", value).strip(" ,.;")

    return None


def undisclosed_order_value(text):
    match = re.search(
        r"(?:consideration|size|value)[\s\S]{0,180}?"
        r"\bnot\s+disclosed\b[\s\S]{0,160}",
        text or "",
        re.I,
    )

    return (
        re.sub(r"\s+", " ", match.group(0)).strip(" ,.;")
        if match else None
    )


# ---------------------------------------------------------
# YAHOO FINANCE
# ---------------------------------------------------------

def yahoo_search(company):
    if not company:
        return None

    try:
        response = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={
                "q": company,
                "quotesCount": 12,
                "newsCount": 0,
            },
            headers=HEADERS,
            timeout=12,
        )

        response.raise_for_status()
        quotes = response.json().get("quotes") or []

        candidates = [
            q for q in quotes
            if q.get("quoteType") == "EQUITY"
            and str(q.get("symbol", "")).endswith((".NS", ".BO"))
        ]

        return candidates[0].get("symbol") if candidates else None

    except Exception as exc:
        log("Yahoo symbol search unavailable:", repr(exc))
        return None


def yahoo_market_data(item, cache):
    if not yf:
        return {}, None

    raw_symbol = str(item.get("symbol") or "").strip()

    ticker_symbol = (
        raw_symbol + ".NS"
        if raw_symbol and not raw_symbol.isdigit()
        else None
    )

    if not ticker_symbol:
        ticker_symbol = yahoo_search(item.get("company"))

    if not ticker_symbol:
        return {}, None

    if ticker_symbol in cache:
        return cache[ticker_symbol], ticker_symbol

    result = {}

    try:
        info = yf.Ticker(ticker_symbol).info or {}

        for key in (
            "regularMarketPrice", "currentPrice", "marketCap",
            "trailingPE", "forwardPE", "priceToBook",
            "enterpriseValue", "trailingEps",
            "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
            "regularMarketChangePercent", "currency",
            "sector", "industry", "regularMarketTime",
        ):
            if info.get(key) is not None:
                result[key] = info.get(key)

    except Exception as exc:
        log(
            "Yahoo Finance snapshot unavailable for",
            ticker_symbol,
            repr(exc),
        )

    cache[ticker_symbol] = result
    return result, ticker_symbol


# ---------------------------------------------------------
# PDF EXTRACTION
# ---------------------------------------------------------

def extract_pdf(item):
    link = item.get("link") or ""

    host = re.sub(r"^https?://", "", link).split("/", 1)[0].lower()

    if not link.startswith("https://") or not host.endswith(
        ("nseindia.com", "nseindia.in", "bseindia.com", "bseindia.in")
    ):
        return "", "No exchange PDF attachment was available."

    try:
        response = requests.get(
            link, headers=HEADERS, timeout=25, stream=True
        )

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
            return (
                text,
                f"Extracted text from {len(pages)} PDF page(s). "
                "Check the source filing for tables or scanned pages.",
            )

        return (
            "",
            f"PDF has {len(pages)} page(s), but no selectable text; "
            "open the source filing.",
        )

    except Exception as exc:
        log("PDF extraction failed:", repr(exc))
        return "", "Filing link retained; automatic PDF text extraction was unavailable."


# ---------------------------------------------------------
# FORMATTING
# ---------------------------------------------------------

def clean_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def format_inr(value):
    return None if value is None else "₹" + format(value, ",.2f")


def fmt_ratio(value):
    return None if value is None else f"{value:.2f}"


def raw_details(source_data, exchange):
    details = []

    for key, value in (source_data or {}).items():
        label = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key))
        label = re.sub(r"[_\-]+", " ", label).strip().title()

        if isinstance(value, (dict, list)):
            value = json.dumps(
                value, ensure_ascii=False, sort_keys=True
            )

        if value == "":
            value = None

        details.append({
            "label": f"Exchange field · {label}",
            "value": value,
            "source": exchange,
        })

    return details


def pe_value(market, price):
    pe = clean_number(market.get("trailingPE"))

    if pe is None:
        eps = clean_number(market.get("trailingEps"))

        if price and eps and eps > 0:
            pe = price / eps

    return pe


# ---------------------------------------------------------
# ALERT BUILDING
# ---------------------------------------------------------

def order_alert(item, market_cache):
    pdf_text, pdf_status = extract_pdf(item)

    full_text = "\n\n".join(
        part for part in (
            item.get("title"), item.get("text"), pdf_text
        ) if part
    )

    amounts, order_amount = parse_money(full_text)
    market, yahoo_ticker = yahoo_market_data(item, market_cache)

    currency = str(market.get("currency") or "").upper()

    price = clean_number(
        market.get("regularMarketPrice") or market.get("currentPrice")
    )

    market_cap = clean_number(market.get("marketCap"))

    cap_cr = (
        market_cap / 10_000_000
        if market_cap is not None and currency == "INR"
        else None
    )

    order_cr = (
        order_amount["inr"] / 10_000_000
        if order_amount else None
    )

    ratio = (
        order_cr / cap_cr * 100
        if order_cr is not None and cap_cr and cap_cr > 0
        else None
    )

    pe = pe_value(market, price)
    week52_high = clean_number(market.get("fiftyTwoWeekHigh"))
    week52_low = clean_number(market.get("fiftyTwoWeekLow"))

    if order_cr is None:
        size_band, subfolder = None, None
    elif order_cr >= 100:
        size_band, subfolder = "Big (₹100 crore or more)", "big"
    elif order_cr >= 10:
        size_band, subfolder = (
            "Medium (₹10 crore to under ₹100 crore)", "medium"
        )
    else:
        size_band, subfolder = "Small (under ₹10 crore)", "small"

    exch = item.get("exchange")

    details = [
        {"label": "Source exchange", "value": exch, "source": exch},
        {"label": "Company", "value": item.get("company"), "source": exch},
        {
            "label": "Exchange symbol / code",
            "value": item.get("symbol"), "source": exch,
        },
        {
            "label": "Announcement category",
            "value": item.get("category"), "source": exch,
        },
        {
            "label": "Filing title",
            "value": item.get("title"), "source": exch,
        },
        {
            "label": "Order / project description",
            "value": item.get("text"), "source": exch,
        },
        {
            "label": "Client / counterparty",
            "value": find_client(full_text), "source": exch,
        },
        {
            "label": "Reported order value",
            "value": (
                order_amount["text"] if order_amount
                else undisclosed_order_value(full_text)
            ),
            "source": exch,
        },
        {
            "label": "Order size band",
            "value": size_band,
            "source": "Rule based only on disclosed order value",
        },
        {
            "label": "Execution / delivery period",
            "value": find_execution(full_text), "source": exch,
        },
        {
            "label": "Filing date and time",
            "value": item.get("time"), "source": exch,
        },
        {
            "label": "PDF text extraction",
            "value": pdf_status, "source": exch,
        },
        {
            "label": "Yahoo Finance symbol",
            "value": yahoo_ticker, "source": "Yahoo Finance",
        },
        {
            "label": "Yahoo Finance company page",
            "value": (
                f"https://finance.yahoo.com/quote/{yahoo_ticker}/"
                if yahoo_ticker else None
            ),
            "source": "Yahoo Finance",
        },
        {
            "label": "Market data snapshot captured",
            "value": datetime.now(IST).isoformat(timespec="seconds"),
            "source": "Yahoo Finance",
        },
        {
            "label": "Price at snapshot",
            "value": format_inr(price), "source": "Yahoo Finance",
        },
        {
            "label": "Current P/E (trailing)",
            "value": fmt_ratio(pe), "source": "Yahoo Finance",
        },
        {
            "label": "52-week high",
            "value": format_inr(week52_high), "source": "Yahoo Finance",
        },
        {
            "label": "52-week low",
            "value": format_inr(week52_low), "source": "Yahoo Finance",
        },
        {
            "label": "Market capitalization",
            "value": format_inr(market_cap), "source": "Yahoo Finance",
        },
        {
            "label": "Market capitalization (INR crore)",
            "value": (
                f"₹{cap_cr:,.2f} crore"
                if cap_cr is not None else None
            ),
            "source": "Yahoo Finance",
        },
        {
            "label": "Forward P/E",
            "value": fmt_ratio(clean_number(market.get("forwardPE"))),
            "source": "Yahoo Finance",
        },
        {
            "label": "Price-to-book",
            "value": fmt_ratio(clean_number(market.get("priceToBook"))),
            "source": "Yahoo Finance",
        },
        {
            "label": "Trailing EPS",
            "value": format_inr(clean_number(market.get("trailingEps"))),
            "source": "Yahoo Finance",
        },
        {
            "label": "Enterprise value",
            "value": format_inr(clean_number(market.get("enterpriseValue"))),
            "source": "Yahoo Finance",
        },
        {
            "label": "Sector",
            "value": market.get("sector"), "source": "Yahoo Finance",
        },
        {
            "label": "Industry",
            "value": market.get("industry"), "source": "Yahoo Finance",
        },
        {
            "label": "Order value / market cap",
            "value": f"{ratio:.4f}%" if ratio is not None else None,
            "source": (
                "Calculated only when reported INR order value "
                "and INR market cap are both available"
            ),
        },
        {
            "label": "All monetary figures found in filing",
            "value": (
                "; ".join(x["text"] for x in amounts)
                if amounts else None
            ),
            "source": exch,
        },
        {
            "label": "Complete extracted filing text",
            "value": pdf_text or None, "source": exch,
        },
    ]

    details.extend(raw_details(item.get("source_data"), exch))

    raw_id = str(item.get("id") or "")
    change = clean_number(market.get("regularMarketChangePercent"))

    return {
        "id": hashlib.sha256(
            raw_id.encode("utf-8")
        ).hexdigest()[:24],
        "parser_version": 7,
        "time": datetime.now(IST).isoformat(timespec="seconds"),
        "folder": "orders",
        "subfolder": subfolder,
        "symbol": str(item.get("symbol") or ""),
        "company": item.get("company") or "",
        "title": (
            item.get("title") or item.get("category")
            or "Order win announcement"
        ),
        "price": price,
        "pe_ratio": pe,
        "week52_high": week52_high,
        "week52_low": week52_low,
        "change_pct": change,
        "details": details,
        "link": item.get("link") or "",
        "source_data": item.get("source_data") or {},
        "filing_text": full_text,
        "source_id": raw_id,
    }


# ---------------------------------------------------------
# STORAGE
# ---------------------------------------------------------

def load_seen():
    try:
        value = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_seen(values):
    unique = list(dict.fromkeys(values))
    SEEN_FILE.write_text(
        json.dumps(
            unique[-MAX_SEEN:], ensure_ascii=False, indent=1
        ) + "\n",
        encoding="utf-8",
    )


ALERT_DOCUMENT = None

DEFAULT_FOLDERS = [
    {"id": "emas", "name": "EMAs", "subfolders": [
        {"id": "ema21", "name": "21 EMA"},
        {"id": "ema50", "name": "50 EMA"},
        {"id": "ema200", "name": "200 EMA"},
        {"id": "golden", "name": "Golden Cross"},
        {"id": "death", "name": "Death Cross"},
    ]},
    {"id": "orders", "name": "Orders", "subfolders": [
        {"id": "big", "name": "Big orders"},
        {"id": "medium", "name": "Medium orders"},
        {"id": "small", "name": "Small orders"},
    ]},
    {"id": "corporate-actions", "name": "Corporate Actions", "subfolders": [
        {"id": "buyback", "name": "Buybacks"},
        {"id": "split", "name": "Stock splits"},
        {"id": "bonus", "name": "Bonus issues"},
        {"id": "dividend", "name": "Dividends"},
        {"id": "rights", "name": "Rights issues"},
        {"id": "merger", "name": "Mergers"},
        {"id": "demerger", "name": "Demergers"},
        {"id": "other", "name": "Other corporate actions"},
    ]},
    {"id": "results", "name": "Results", "subfolders": [
        {"id": "quarterly", "name": "Quarterly results"},
        {"id": "profit-up", "name": "Profit up"},
        {"id": "profit-down", "name": "Profit down"},
    ]},
    {"id": "promoter", "name": "Promoter activity", "subfolders": [
        {"id": "buying", "name": "Promoter buying"},
        {"id": "selling", "name": "Promoter selling"},
        {"id": "bulk", "name": "Bulk deals"},
        {"id": "block", "name": "Block deals"},
    ]},
    {"id": "volume", "name": "Volume", "subfolders": [
        {"id": "high", "name": "High volume"},
    ]},
    {"id": "news", "name": "News", "subfolders": [
        {"id": "company", "name": "By company"},
    ]},
]


def load_alerts():
    """Load the app feed and fail closed on malformed data."""
    global ALERT_DOCUMENT

    try:
        value = json.loads(ALERT_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(
            "alerts.json is missing; refusing to replace alert history."
        )
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Cannot read alerts.json; refusing to replace alert history: {exc}"
        ) from exc

    if isinstance(value, list):
        ALERT_DOCUMENT = {
            "sample": False,
            "folders": DEFAULT_FOLDERS,
            "alerts": value,
        }
        log("Migrating legacy alerts.json list; preserving", len(value), "alerts")
        return value

    if not isinstance(value, dict) or not isinstance(value.get("alerts"), list):
        raise ValueError(
            "alerts.json must be a list or an object containing an alerts list; "
            "refusing to replace invalid alert history."
        )

    if not isinstance(value.get("folders"), list):
        value["folders"] = DEFAULT_FOLDERS

    ALERT_DOCUMENT = value
    return value["alerts"]


def save_alerts(values):
    """Save alerts inside the app's feed object, retaining folders/history."""
    global ALERT_DOCUMENT

    if not isinstance(ALERT_DOCUMENT, dict):
        raise RuntimeError("alerts.json was not loaded; refusing to overwrite it.")

    ALERT_DOCUMENT["alerts"] = values[:MAX_ALERTS]
    ALERT_DOCUMENT["updated"] = datetime.now(IST).isoformat(timespec="seconds")
    ALERT_FILE.write_text(
        json.dumps(ALERT_DOCUMENT, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def send_telegram(alert):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return

    lines = [
        f"🟢 {alert['company']} ({alert['symbol']})",
        alert["title"],
    ]

    if alert.get("price") is not None:
        lines.append(f"Price: {format_inr(alert['price'])}")

    if alert.get("pe_ratio") is not None:
        lines.append(f"Current P/E: {fmt_ratio(alert['pe_ratio'])}")

    if (
        alert.get("week52_high") is not None
        and alert.get("week52_low") is not None
    ):
        lines.append(
            f"52-week range: {format_inr(alert['week52_low'])} – "
            f"{format_inr(alert['week52_high'])}"
        )

    for detail in alert["details"]:
        if detail["label"] in (
            "Reported order value",
            "Client / counterparty",
            "Execution / delivery period",
            "Order size band",
        ):
            if detail["value"]:
                lines.append(f"{detail['label']}: {detail['value']}")

    if alert["link"]:
        lines.append(alert["link"])

    message = "\n".join(lines)[:4000]

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        response.raise_for_status()

    except Exception as exc:
        log("Telegram send failed (alert still saved):", repr(exc))


# ---------------------------------------------------------
def queue_telegram(alert): Path("telegram-outbox.json").open("a", encoding="utf-8").write(json.dumps(alert, ensure_ascii=False) + "__SS_TELEGRAM_ALERT__"); # MAIN SCANNER
# ---------------------------------------------------------

def run():
    today = datetime.now(IST).date()
    start = today - timedelta(days=LOOKBACK_DAYS)

    log(f"scan {start} to {today}")

    seen_list = load_seen()
    seen = set(seen_list)

    alerts = load_alerts()
    market_cache = {}

    existing_alert_ids = {
        str(alert.get("source_id"))
        for alert in alerts
        if alert.get("source_id")
    }

    fetched = []

    for name, fetcher in (
        ("NSE", fetch_nse),
        ("BSE", fetch_bse),
    ):
        try:
            rows = fetcher(start, today)
            log(name, len(rows), "announcements")
            fetched.extend(rows)

        except Exception as exc:
            # A BSE failure must not stop the NSE results.
            log(name, "fetch failed, will retry next run:", repr(exc))

    new_alerts = 0
    skipped = 0
    duplicate_count = 0
    matched_orders = 0
    rejected_count = 0
    excluded_count = 0
    rejected_samples = []

    for item in fetched:
        raw_id = str(item.get("id") or "")

        if not raw_id:
            continue

        if raw_id in existing_alert_ids:
            duplicate_count += 1
            continue

        reason = order_filter_reason(item)

        if reason == "excluded":
            excluded_count += 1
            continue

        if reason != "order":
            rejected_count += 1

            if len(rejected_samples) < 30:
                rejected_samples.append({
                    "exchange": item.get("exchange"),
                    "company": item.get("company"),
                    "title": item.get("title"),
                    "category": item.get("category"),
                })

            # Already processed non-orders remain skipped.
            if raw_id in seen:
                continue

            seen.add(raw_id)
            seen_list.append(raw_id)
            skipped += 1
            continue

        matched_orders += 1

        try:
            alert = order_alert(item, market_cache)

        except Exception as exc:
            log("alert build failed, will retry:", raw_id, repr(exc))
            continue

        alerts.insert(0, alert)

        seen.add(raw_id)
        seen_list.append(raw_id)
        existing_alert_ids.add(raw_id)

        new_alerts += 1
        queue_telegram(alert)

        log(
            "new order alert:",
            alert["symbol"] or alert["company"],
            alert["title"][:100],
        )

    save_seen(seen_list)
    save_alerts(alerts)

    log("total announcements:", len(fetched))
    log("order filter matches:", matched_orders)
    log("new alerts:", new_alerts)
    log("duplicates:", duplicate_count)
    log("non-order announcements skipped:", skipped)
    log("excluded announcements:", excluded_count)
    log("unmatched announcements:", rejected_count)

    log("unmatched announcement samples:")

    for sample in rejected_samples:
        log(json.dumps(sample, ensure_ascii=False))

    log(f"done: {new_alerts} new alert(s)")


# ---------------------------------------------------------
# BASIC FILTER TESTS
# ---------------------------------------------------------

def test_order_filter():
    test_cases = [
        ("Awarding of order(s)/contract(s)", True),
        ("Awarding of order", True),
        ("Awarding of contract", True),
        ("Receipt of order", True),
        ("Letter of Award", True),
        ("New work order received", True),
        ("Action(s) initiated or orders passed", True),
        ("Actions initiated or orders passed", True),
        ("Orders passed", True),
        ("Order(s) passed", True),
        ("Purchase order received", True),
        ("Company secured a new contract", True),
        ("Trading window closure", False),
        ("Credit rating update", False),
        ("Cancellation of the order", False),
        ("Newspaper publication", False),
    ]

    for title, expected in test_cases:
        actual = is_order_filing({
            "title": title,
            "text": "",
            "category": "",
        })

        assert actual == expected, (
            f"FAILED: {title!r}: expected {expected}, got {actual}"
        )

    log("All order filter tests passed.")


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_order_filter()
    else:
        run()
