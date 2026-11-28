
#!/usr/bin/env python3
"""
Share Sanket - NSE Promoter Activity Scanner

NSE-only promoter disclosures:
- Promoter buying and selling
- Pledge creation, release and invocation
- Promoter names
- Shares and transaction percentage
- Transaction value and price, when disclosed
- Previous and post-transaction holding
- Purpose and transaction mode
- Transaction-wise breakdown
- Original NSE filing link
- Duplicate prevention

Missing information is never invented.
"""

import hashlib
import io
import json
import os
import re
import tempfile

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from pypdf import PdfReader


# =========================================================
# CONFIGURATION
# =========================================================

PROMOTER_DIR = Path(__file__).resolve().parent
ROOT = PROMOTER_DIR.parent

# Promoter alerts must use their own JSON feed.
# Do not read or overwrite the website's root alerts.json.
ALERTS = PROMOTER_DIR / "alerts.json"
STATE = PROMOTER_DIR / "promoter_state.json"

IST = ZoneInfo("Asia/Kolkata")

HOME = "https://www.nseindia.com"
API = HOME + "/api/corporate-announcements"
PAGE = HOME + "/companies-listing/corporate-filings-announcements"

NOT_DISCLOSED = "Not Disclosed"

HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE,
}


# =========================================================
# PROMOTER CATEGORIES
# =========================================================

SUBS = [
    {"id": "buying", "name": "🟢 Promoter Buying"},
    {"id": "pledge-release", "name": "🟢 Promoter Pledge Release"},
    {"id": "selling", "name": "🔴 Promoter Selling"},
    {"id": "pledge-creation", "name": "🔴 Promoter Pledge Creation"},
    {"id": "pledge-invocation", "name": "🔴 Promoter Pledge Invocation"},
]

PROMOTER = re.compile(
    r"\bpromoter(?:s)?\b|\bpromoter group\b",
    re.I
)

HINT = re.compile(
    r"regulation\s*29|regulation\s*31|encumbrance|"
    r"pledge|shareholding|sast",
    re.I
)

EVENTS = [
    (
        "pledge-invocation",
        re.compile(
            r"invocation\s+of\s+(?:the\s+)?pledge|"
            r"pledge.{0,30}invoked|invoked.{0,30}lender",
            re.I
        )
    ),
    (
        "pledge-release",
        re.compile(
            r"release\s+of\s+(?:the\s+)?pledge|"
            r"pledge.{0,30}released|unpledged|"
            r"release\s+of\s+encumbrance",
            re.I
        )
    ),
    (
        "pledge-creation",
        re.compile(
            r"creation\s+of\s+(?:a\s+)?pledge|"
            r"pledge.{0,30}created|shares\s+pledged|"
            r"creation\s+of\s+encumbrance",
            re.I
        )
    ),
]

BUY = re.compile(
    r"\b(acquisition|acquired|purchase|purchased|bought|buying)\b",
    re.I
)

SELL = re.compile(
    r"\b(disposal|disposed|sale|sold|selling|transferor)\b",
    re.I
)


# =========================================================
# JSON UTILITIES
# =========================================================

def atomic_write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False
        ) as f:
            json.dump(
                obj,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )
            f.write("\n")
            tmp = f.name

        os.replace(tmp, path)

    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot safely read {path.name}: {exc}"
        )


# =========================================================
# NSE ANNOUNCEMENTS
# =========================================================

def fetch_rows():
    today = datetime.now(IST).date()

    start = (
        today - timedelta(days=3)
    ).strftime("%d-%m-%Y")

    end = today.strftime("%d-%m-%Y")

    session = requests.Session()
    session.headers.update(HEADERS)

    session.get(HOME, timeout=20)

    response = session.get(
        API,
        params={
            "index": "equities",
            "from_date": start,
            "to_date": end
        },
        timeout=30
    )

    response.raise_for_status()

    rows = response.json()

    if not isinstance(rows, list):
        raise RuntimeError(
            "Unexpected NSE announcement response"
        )

    return rows


# =========================================================
# PDF EXTRACTION
# =========================================================

def pdf_text(url):
    if (
        not url
        or not url.lower().split("?")[0].endswith(".pdf")
    ):
        return ""

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            stream=True
        )

        response.raise_for_status()

        content = bytearray()

        for chunk in response.iter_content(65536):
            content.extend(chunk)

            if len(content) > 8 * 1024 * 1024:
                print("PDF exceeds 8 MB extraction limit")
                return ""

        reader = PdfReader(io.BytesIO(content))

        return "\n".join(
            page.extract_text() or ""
            for page in reader.pages[:40]
        )

    except Exception as exc:
        print(f"PDF extraction unavailable: {exc}")
        return ""


# =========================================================
# TEXT UTILITIES
# =========================================================

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def number_value(raw):
    if raw is None:
        return None

    value = str(raw).replace(",", "").replace(" ", "")

    try:
        number = float(value)

        if number.is_integer():
            return int(number)

        return number

    except (TypeError, ValueError):
        return None


def fmt_number(value):
    if value is None:
        return NOT_DISCLOSED

    if float(value).is_integer():
        return f"{int(value):,}"

    return f"{value:,.2f}"


def first_match(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)

        if match:
            return clean_text(match.group(1))

    return None


def labeled_number(text, labels, percent=False):
    label = "(?:" + "|".join(labels) + ")"
    suffix = r"\s*%?" if percent else ""

    patterns = [
        label
        + r"[^0-9]{0,100}"
        + r"([0-9][0-9,]*(?:\.[0-9]+)?)"
        + suffix,

        r"([0-9][0-9,]*(?:\.[0-9]+)?)"
        + r"\s*%?\s*[^.\n]{0,55}"
        + label
    ]

    return number_value(first_match(patterns, text))


# =========================================================
# TRANSACTION BREAKDOWN
# =========================================================

def extract_transaction_breakdown(text):
    """
    Extract individual transaction rows when the PDF text
    contains a recognisable promoter/date/shares/percentage
    table.

    Unrecognised rows are not fabricated.
    """

    normalized = clean_text(text)

    # Repair common PDF extraction issue:
    # "November 2 5, 2026" -> "November 25, 2026"
    months = (
        "January|February|March|April|May|June|July|"
        "August|September|October|November|December"
    )

    normalized = re.sub(
        rf"\b({months})\s+(\d)\s+(\d),\s*(20\d{{2}})",
        r"\1 \2\3, \4",
        normalized,
        flags=re.I
    )

    pattern = re.compile(
        rf"(?P<name>"
        rf"(?:Mr\.?|Mrs\.?|Ms\.?|M/s\.?)?\s*"
        rf"[A-Z][A-Za-z.' -]{{2,75}}?"
        rf")\s+"
        rf"(?P<date>"
        rf"(?:{months})\s+\d{{1,2}},?\s+20\d{{2}}"
        rf")\s+"
        rf"(?P<shares>\d[\d,]*)\s+"
        rf"(?P<pct>\d+(?:\.\d+)?\s*%)",
        re.I
    )

    matches = list(pattern.finditer(normalized))

    if not matches:
        return NOT_DISCLOSED

    lines = []

    for match in matches[:30]:
        name = clean_text(match.group("name"))
        date = clean_text(match.group("date"))
        shares = number_value(match.group("shares"))
        pct = clean_text(match.group("pct"))

        if not name or shares is None:
            continue

        lines.append(
            f"{name} | {date} | "
            f"{fmt_number(shares)} shares | {pct}"
        )

    if not lines:
        return NOT_DISCLOSED

    return "\n".join(lines)


# =========================================================
# PROMOTER TRANSACTION EXTRACTION
# =========================================================

def extract_details(text, row):
    flat = clean_text(text)

    # PROMOTER NAME

    promoter_name = first_match(
        [
            r"(?:name\s+of\s+(?:the\s+)?"
            r"(?:acquirer|seller|promoter|person)"
            r"\s*[:\-]?\s*)"
            r"([A-Z][A-Za-z .,'&()-]{2,100}?)"
            r"(?=\s+(?:category|date|number|of shares|being|is|has)\b|[,;.]|$)",

            r"(?:promoter(?:'s)?\s+name\s*[:\-]\s*)"
            r"([A-Z][A-Za-z .,'&()-]{2,100}?)"
            r"(?=\s+(?:has|had|is|was)\b|[,;.]|$)"
        ],
        text
    )

    if not promoter_name:
        promoter_name = first_match(
            [
                r"((?:Mr\.?|Mrs\.?|Ms\.?)\s+"
                r"[A-Z][A-Za-z .'-]{2,70})"
                r"\s+(?:has\s+)?"
                r"(?:submitted|acquired|purchased|sold|disposed)"
            ],
            text
        )

    # SHARES

    quantity = labeled_number(
        text,
        [
            r"no\.?\s+of\s+shares\s+(?:acquired|purchased|bought|sold|disposed)",
            r"number\s+of\s+shares\s+(?:acquired|purchased|bought|sold|disposed)",
            r"shares\s+(?:acquired|purchased|bought|sold|disposed)",
            r"quantity\s+of\s+shares",
            r"no\.?\s+of\s+shares",
            r"number\s+of\s+securities",
        ]
    )

    if quantity is None:
        quantity = number_value(
            first_match(
                [
                    r"(?:acquired|purchased|bought|sold|disposed\s+of)"
                    r"\s+([0-9][0-9,]*)\s+(?:equity\s+)?shares",

                    r"([0-9][0-9,]*)\s+(?:equity\s+)?shares"
                    r"\s+(?:were\s+)?"
                    r"(?:acquired|purchased|bought|sold|disposed)"
                ],
                text
            )
        )

    # TRANSACTION PERCENTAGE

    transaction_pct = labeled_number(
        text,
        [
            r"percentage\s+of\s+(?:total\s+)?share\s+capital",
            r"%\s+of\s+(?:total\s+)?share\s+capital",
            r"shares\s+as\s+a\s+percentage",
            r"percentage\s+of\s+shares\s+(?:acquired|disposed)",
            r"transaction\s+percentage",
        ],
        percent=True
    )

    # PREVIOUS HOLDING

    before_pct = labeled_number(
        text,
        [
            r"shareholding\s+before\s+acquisition",
            r"holding\s+before\s+acquisition",
            r"pre[- ]transaction\s+shareholding",
            r"prior\s+to\s+acquisition",
            r"before\s+the\s+transaction",
            r"shareholding\s+before",
        ],
        percent=True
    )

    # POST-TRANSACTION HOLDING

    after_pct = labeled_number(
        text,
        [
            r"shareholding\s+after\s+acquisition",
            r"holding\s+after\s+acquisition",
            r"post[- ]transaction\s+shareholding",
            r"after\s+the\s+transaction",
            r"shareholding\s+after",
        ],
        percent=True
    )

    # PRICE PER SHARE

    price = labeled_number(
        text,
        [
            r"price\s+per\s+share",
            r"average\s+price\s+per\s+share",
            r"purchase\s+price",
            r"sale\s+price",
            r"transaction\s+price",
        ]
    )

    # TRANSACTION VALUE

    amount = None

    amount_raw = first_match(
        [
            r"(?:total\s+)?(?:transaction\s+)?"
            r"(?:consideration|value|amount|proceeds)"
            r"\s*(?:inr|rs\.?|₹)?\s*[:\-]?\s*"
            r"(?:inr|rs\.?|₹)?\s*"
            r"([0-9][0-9,]*(?:\.[0-9]+)?)",

            r"(?:inr|rs\.?|₹)\s*"
            r"([0-9][0-9,]*(?:\.[0-9]+)?)"
        ],
        text
    )

    if amount_raw:
        amount = number_value(amount_raw)

    amount_source = "NSE filing"

    # Calculate only when both figures are disclosed.

    if amount is None and quantity is not None and price is not None:
        amount = quantity * price
        amount_source = "Calculated from disclosed shares × disclosed price"

    # PURPOSE

    purpose = first_match(
        [
            r"(?:purpose|reason)"
            r"\s*(?:of\s+(?:acquisition|purchase|sale|disposal|transaction))?"
            r"\s*[:\-]\s*"
            r"(.{5,240}?)"
            r"(?=\s+(?:date|mode|number|quantity|no\.?\s+of\s+shares|percentage|shareholding)\b|$)",

            r"(?:the\s+purpose\s+of\s+(?:the\s+)?"
            r"(?:acquisition|purchase|sale|transaction)\s+is)"
            r"\s+(.{5,240}?)(?=[.;]|$)"
        ],
        text
    )

    # TRANSACTION MODE

    mode = first_match(
        [
            r"(?:mode|type)\s+of\s+"
            r"(?:acquisition|disposal|transaction)"
            r"\s*[:\-]\s*([^.;]{3,100})",

            r"(open\s+market|off\s+market|inter\s+se\s+transfer|"
            r"preferential\s+allotment|gift|inheritance|rights\s+issue)"
        ],
        text
    )

    # ACTIVITY

    buying = bool(BUY.search(text))
    selling = bool(SELL.search(text))

    if buying and not selling:
        activity = "Buying"
    elif selling and not buying:
        activity = "Selling"
    else:
        activity = NOT_DISCLOSED

    # TRANSACTION BREAKDOWN

    breakdown = extract_transaction_breakdown(text)

    return [
        {
            "label": "Promoter name",
            "value": promoter_name or NOT_DISCLOSED,
            "source": "NSE filing"
        },
        {
            "label": "Activity",
            "value": activity,
            "source": "NSE filing text"
        },
        {
            "label": "Transaction type / mode",
            "value": mode or NOT_DISCLOSED,
            "source": "NSE filing"
        },
        {
            "label": "Shares involved",
            "value": fmt_number(quantity),
            "source": "NSE filing"
        },
        {
            "label": "Transaction percentage",
            "value": (
                f"{transaction_pct:g}%"
                if transaction_pct is not None
                else NOT_DISCLOSED
            ),
            "source": "NSE filing"
        },
        {
            "label": "Transaction value",
            "value": (
                f"₹{amount:,.2f}"
                if amount is not None
                else NOT_DISCLOSED
            ),
            "source": amount_source if amount is not None else "NSE filing"
        },
        {
            "label": "Price per share",
            "value": (
                f"₹{price:,.2f}"
                if price is not None
                else NOT_DISCLOSED
            ),
            "source": "NSE filing"
        },
        {
            "label": "Previous holding",
            "value": (
                f"{before_pct:g}%"
                if before_pct is not None
                else NOT_DISCLOSED
            ),
            "source": "NSE filing"
        },
        {
            "label": "Post-transaction holding",
            "value": (
                f"{after_pct:g}%"
                if after_pct is not None
                else NOT_DISCLOSED
            ),
            "source": "NSE filing"
        },
        {
            "label": "Purpose of transaction",
            "value": purpose or NOT_DISCLOSED,
            "source": "NSE filing"
        },
        {
            "label": "Transaction breakdown",
            "value": breakdown,
            "source": "NSE filing transaction table"
        }
    ]


# =========================================================
# CLASSIFICATION
# =========================================================

def classify(text):
    if not PROMOTER.search(text):
        return None

    for folder, pattern in EVENTS:
        if pattern.search(text):
            return folder

    buying = bool(BUY.search(text))
    selling = bool(SELL.search(text))

    if buying != selling:
        return "buying" if buying else "selling"

    return None


# =========================================================
# MAIN SCANNER
# =========================================================

def main():
    rows = fetch_rows()

    data = read_json(
        ALERTS,
        {
            "sample": False,
            "folders": [],
            "alerts": []
        }
    )

    if isinstance(data, list):
        data = {
            "sample": False,
            "folders": [],
            "alerts": data
        }

    if (
        not isinstance(data, dict)
        or not isinstance(data.get("alerts", []), list)
    ):
        raise RuntimeError(
            "alerts.json format invalid; refusing to overwrite"
        )

    state = read_json(STATE, [])

    if isinstance(state, dict):
        state = list(state)

    if not isinstance(state, list):
        raise RuntimeError(
            "promoter_state.json format invalid"
        )

    seen = set(map(str, state))

    # CREATE PROMOTER FOLDER

    folders = data.setdefault("folders", [])

    folder = next(
        (
            x for x in folders
            if isinstance(x, dict)
            and x.get("id") == "promoter"
        ),
        None
    )

    if folder is None:
        folder = {
            "id": "promoter",
            "name": "Promoter Activity",
            "subfolders": []
        }
        folders.append(folder)

    folder["name"] = "Promoter Activity"
    folder["subfolders"] = SUBS

    alerts = data.setdefault("alerts", [])

    ids = {
        a.get("id")
        for a in alerts
        if isinstance(a, dict)
    }

    added = 0

    # PROCESS NSE DISCLOSURES

    for row in rows:
        category = str(row.get("desc") or "")
        summary = str(row.get("attchmntText") or "")
        attachment = str(row.get("attchmntFile") or "")

        if not (
            PROMOTER.search(category + "\n" + summary)
            or HINT.search(category + "\n" + summary)
        ):
            continue

        extracted = pdf_text(attachment)

        body = (
            category
            + "\n"
            + summary
            + "\n"
            + extracted
        )

        sub = classify(body)

        if not sub:
            continue

        source = str(
            row.get("seq_id")
            or row.get("seqId")
            or ""
        )

        if not source:
            source = hashlib.sha256(
                (
                    str(row.get("symbol"))
                    + category
                    + str(row.get("an_dt"))
                    + attachment
                ).encode()
            ).hexdigest()

        if source in seen:
            continue

        alert_id = (
            "promoter-"
            + hashlib.sha256(source.encode()).hexdigest()[:24]
        )

        if alert_id in ids:
            seen.add(source)
            continue

        sub_name = next(
            x["name"]
            for x in SUBS
            if x["id"] == sub
        )

        details = [
            {
                "label": "NSE disclosure category",
                "value": category or NOT_DISCLOSED,
                "source": "NSE"
            },
            {
                "label": "NSE announcement date",
                "value": (
                    row.get("an_dt")
                    or row.get("sort_date")
                    or NOT_DISCLOSED
                ),
                "source": "NSE"
            },
            {
                "label": "NSE announcement summary",
                "value": summary or NOT_DISCLOSED,
                "source": "NSE"
            },
            {
                "label": "Promoter activity",
                "value": sub_name,
                "source": "NSE disclosure classification"
            }
        ]

        details.extend(
            extract_details(body, row)
        )

        # Keep original PDF text for reference.
        # The website will hide this long field from the
        # simple promoter details display.

        details.append(
            {
                "label": "Extracted filing text",
                "value": (
                    extracted[:12000]
                    if extracted
                    else (
                        "PDF text could not be extracted; "
                        "open the original NSE filing."
                    )
                ),
                "source": "NSE attachment"
            }
        )

        symbol = str(row.get("symbol") or "")
        company = str(row.get("sm_name") or "")

        alert = {
            "id": alert_id,
            "source_id": source,
            "time": datetime.now(IST).isoformat(
                timespec="seconds"
            ),
            "folder": "promoter",
            "subfolder": sub,
            "exchange": "NSE",
            "symbol": symbol,
            "company": company,
            "title": (
                sub_name
                + " – "
                + (company or symbol or "NSE disclosure")
            ),
            "details": details,
            "link": (
                attachment
                or (
                    f"https://www.nseindia.com/"
                    f"get-quotes/equity?symbol={symbol}"
                    if symbol
                    else PAGE
                )
            )
        }

        alerts.append(alert)
        ids.add(alert_id)
        seen.add(source)
        added += 1

    # SAVE

    data["sample"] = False

    data["updated"] = datetime.now(IST).isoformat(
        timespec="seconds"
    )

    atomic_write(ALERTS, data)

    atomic_write(
        STATE,
        sorted(seen)[-12000:]
    )

    print(f"New promoter alerts added: {added}")


# =========================================================
# EXECUTION
# =========================================================

if __name__ == "__main__":
    main()
