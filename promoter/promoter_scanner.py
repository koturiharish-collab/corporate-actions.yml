#!/usr/bin/env python3
"""NSE-only Share Sanket promoter activity scanner."""
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

PROMOTER_DIR = Path(__file__).resolve().parent
ROOT = PROMOTER_DIR.parent
ALERTS = ROOT / "alerts.json"
STATE = PROMOTER_DIR / "promoter_state.json"
IST = ZoneInfo("Asia/Kolkata")
HOME = "https://www.nseindia.com"
API = HOME + "/api/corporate-announcements"
PAGE = HOME + "/companies-listing/corporate-filings-announcements"
HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE,
}
SUBS = [
    {"id": "buying", "name": "🟢 Promoter Buying"},
    {"id": "pledge-release", "name": "🟢 Promoter Pledge Release"},
    {"id": "selling", "name": "🔴 Promoter Selling"},
    {"id": "pledge-creation", "name": "🔴 Promoter Pledge Creation"},
    {"id": "pledge-invocation", "name": "🔴 Promoter Pledge Invocation"},
]
PROMOTER = re.compile(r"\bpromoter(?:s)?\b|\bpromoter group\b", re.I)
HINT = re.compile(r"regulation\s*29|regulation\s*31|encumbrance|pledge|shareholding|sast", re.I)
EVENTS = [
    ("pledge-invocation", re.compile(r"invocation\s+of\s+(?:the\s+)?pledge|pledge.{0,30}invoked|invoked.{0,30}lender", re.I)),
    ("pledge-release", re.compile(r"release\s+of\s+(?:the\s+)?pledge|pledge.{0,30}released|unpledged|release\s+of\s+encumbrance", re.I)),
    ("pledge-creation", re.compile(r"creation\s+of\s+(?:a\s+)?pledge|pledge.{0,30}created|shares?\s+pledged|creation\s+of\s+encumbrance", re.I)),
]
BUY = re.compile(r"\b(acquisition|acquired|purchase|purchased|bought|buying)\b", re.I)
SELL = re.compile(r"\b(disposal|disposed|sale|sold|selling)\b", re.I)


def atomic_write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
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
        raise RuntimeError(f"Cannot safely read {path.name}: {exc}")


def fetch_rows():
    start = (datetime.now(IST).date() - timedelta(days=3)).strftime("%d-%m-%Y")
    end = datetime.now(IST).date().strftime("%d-%m-%Y")
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(HOME, timeout=20)
    response = session.get(API, params={"index": "equities", "from_date": start, "to_date": end}, timeout=30)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("Unexpected NSE announcement response")
    return rows


def pdf_text(url):
    if not url or not url.lower().split("?")[0].endswith(".pdf"):
        return ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, stream=True)
        r.raise_for_status()
        content = bytearray()
        for chunk in r.iter_content(65536):
            content.extend(chunk)
            if len(content) > 8 * 1024 * 1024:
                return ""
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:30])
    except Exception as exc:
        print(f"PDF extraction unavailable: {exc}")
        return ""


def classify(text):
    if not PROMOTER.search(text):
        return None
    for folder, pattern in EVENTS:
        if pattern.search(text):
            return folder
    b, s = bool(BUY.search(text)), bool(SELL.search(text))
    if b != s:
        return "buying" if b else "selling"
    return None


def main():
    rows = fetch_rows()
    data = read_json(ALERTS, {"sample": False, "folders": [], "alerts": []})
    if isinstance(data, list):
        data = {"sample": False, "folders": [], "alerts": data}
    if not isinstance(data, dict) or not isinstance(data.get("alerts", []), list):
        raise RuntimeError("alerts.json format invalid; refusing to overwrite")
    state = read_json(STATE, [])
    if isinstance(state, dict):
        state = list(state)
    if not isinstance(state, list):
        raise RuntimeError("promoter_state.json format invalid")
    seen = set(map(str, state))
    folders = data.setdefault("folders", [])
    folder = next((x for x in folders if isinstance(x, dict) and x.get("id") == "promoter"), None)
    if folder is None:
        folder = {"id": "promoter", "name": "Promoter Activity", "subfolders": []}
        folders.append(folder)
    folder["name"] = "Promoter Activity"
    folder["subfolders"] = SUBS
    alerts = data.setdefault("alerts", [])
    ids = {a.get("id") for a in alerts if isinstance(a, dict)}
    added = 0
    for row in rows:
        category = str(row.get("desc") or "")
        summary = str(row.get("attchmntText") or "")
        attachment = str(row.get("attchmntFile") or "")
        # The attached filing may contain the promoter name, even if the API summary does not.
        if not (PROMOTER.search(category + "\n" + summary) or HINT.search(category + "\n" + summary)):
            continue
        extracted = pdf_text(attachment)
        body = category + "\n" + summary + "\n" + extracted
        sub = classify(body)
        if not sub:
            continue
        source = str(row.get("seq_id") or row.get("seqId") or "")
        if not source:
            source = hashlib.sha256((str(row.get("symbol")) + category + str(row.get("an_dt")) + attachment).encode()).hexdigest()
        if source in seen:
            continue
        alert_id = "promoter-" + hashlib.sha256(source.encode()).hexdigest()[:24]
        if alert_id in ids:
            seen.add(source)
            continue
        sub_name = next(x["name"] for x in SUBS if x["id"] == sub)
        details = [
            {"label": "NSE disclosure category", "value": category or None, "source": "NSE"},
            {"label": "NSE announcement date", "value": row.get("an_dt") or row.get("sort_date"), "source": "NSE"},
            {"label": "NSE announcement summary", "value": summary or None, "source": "NSE"},
            {"label": "Promoter activity", "value": sub_name, "source": "NSE disclosure classification"},
            {"label": "Extracted filing text", "value": extracted[:12000] if extracted else "Not available as extractable PDF text", "source": "NSE attachment"},
        ]
        symbol = str(row.get("symbol") or "")
        alerts.append({
            "id": alert_id, "source_id": source, "time": datetime.now(IST).isoformat(timespec="seconds"),
            "folder": "promoter", "subfolder": sub, "exchange": "NSE",
            "symbol": symbol, "company": str(row.get("sm_name") or ""),
            "title": sub_name + " – " + str(row.get("sm_name") or symbol or "NSE disclosure"),
            "details": details,
            "link": attachment or (f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}" if symbol else PAGE),
        })
        ids.add(alert_id)
        seen.add(source)
        added += 1
    data["sample"] = False
    data["updated"] = datetime.now(IST).isoformat(timespec="seconds")
    atomic_write(ALERTS, data)
    atomic_write(STATE, sorted(seen)[-12000:])
    print(f"New promoter alerts added: {added}")


if __name__ == "__main__":
    main()
