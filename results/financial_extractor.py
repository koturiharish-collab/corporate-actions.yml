
"""Share Sanket - Financial Statement Extractor."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
EXTRACTED_DIR = BASE_DIR / "extracted"
REVIEW_DIR = BASE_DIR / "review"
IST = ZoneInfo("Asia/Kolkata")

FINANCIAL_LABELS = [
    "Revenue from operations",
    "Other income",
    "Total income",
    "Total expenses",
    "Profit before tax",
    "Profit after tax",
    "Profit for the period",
    "Owners of the Parent",
    "Basic earnings per share",
    "Diluted earnings per share",
    "Finance costs",
    "Depreciation and amortization",
]


def read_text(symbol):
    path = EXTRACTED_DIR / f"{symbol}.txt"

    if not path.exists():
        raise FileNotFoundError(f"Missing PDF text: {path}")

    return path.read_text(
        encoding="utf-8",
        errors="replace"
    )


def split_pdf_pages(text):
    parts = re.split(
        r"---\s*PAGE\s+(\d+)\s*---",
        text,
        flags=re.IGNORECASE
    )

    pages = []

    for i in range(1, len(parts), 2):
        pages.append({
            "page": int(parts[i]),
            "text": parts[i + 1].strip()
        })

    if not pages:
        raise ValueError(
            "No PDF page markers found in extracted text."
        )

    return pages


def normalize(text):
    text = text.lower()
    text = text.replace("anded", "ended")
    text = text.replace("conso1idated", "consolidated")
    text = text.replace("unaudited", "unaudited")
    return " ".join(text.split())


def find_actual_statement(pages, statement_type):
    """Find a financial statement page, excluding auditor reports."""

    for page in pages:
        raw = page["text"]
        clean = normalize(raw)

        has_type = statement_type in clean
        has_financial_results = "financial results" in clean
        has_revenue = "revenue from operations" in clean
        has_particulars = "particulars" in clean
        has_period = (
            "quarter" in clean
            and "half year" in clean
        )

        if (
            has_type
            and has_financial_results
            and has_revenue
            and has_particulars
            and has_period
        ):
            return {
                "found": True,
                "page": page["page"],
                "raw_text": raw,
                "detection_method": "Financial statement table markers"
            }

    return {
        "found": False,
        "page": None,
        "raw_text": "",
        "detection_method": "Statement table not identified"
    }


def detect_units(text):
    clean = normalize(text)

    if "rupees in millions" in clean:
        return "Indian Rupees in Millions"

    if "rupees in crores" in clean:
        return "Indian Rupees in Crores"

    if "indian rupees" in clean:
        return "Indian Rupees - denomination needs review"

    return "Not identified"


def extract_candidate_lines(text):
    candidates = []

    for number, line in enumerate(text.splitlines(), start=1):
        cleaned = " ".join(line.split())

        if not cleaned:
            continue

        for label in FINANCIAL_LABELS:
            if label.lower() in cleaned.lower():
                candidates.append({
                    "line": number,
                    "label": label,
                    "raw_text": cleaned,
                    "verified": False
                })
                break

    return candidates


def build_statement(pages, statement_type):
    result = find_actual_statement(
        pages,
        statement_type
    )

    if not result["found"]:
        return {
            **result,
            "financial_units": "Not identified",
            "candidate_lines": [],
            "figures_verified": False
        }

    return {
        **result,
        "financial_units": detect_units(result["raw_text"]),
        "candidate_lines": extract_candidate_lines(
            result["raw_text"]
        ),
        "figures_verified": False
    }


def build_report(symbol, text):
    pages = split_pdf_pages(text)

    standalone = build_statement(
        pages,
        "standalone"
    )

    consolidated = build_statement(
        pages,
        "consolidated"
    )

    return {
        "symbol": symbol,
        "extracted_at": datetime.now(IST).isoformat(),
        "extraction_status": "Review required",
        "automatic_publication": False,
        "statements": {
            "standalone": standalone,
            "consolidated": consolidated
        },
        "validation": {
            "standalone_found": standalone["found"],
            "consolidated_found": consolidated["found"],
            "standalone_page": standalone["page"],
            "consolidated_page": consolidated["page"],
            "figures_verified": False
        }
    }


def main():
    symbol = (
        sys.argv[1].strip().upper()
        if len(sys.argv) > 1
        else "SFC_TEST"
    )

    text = read_text(symbol)
    report = build_report(symbol, text)

    REVIEW_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = REVIEW_DIR / f"{symbol}_financial_review.json"

    output.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print("Financial statement review completed.")
    print("Symbol:", symbol)
    print("Standalone page:", report["validation"]["standalone_page"])
    print("Consolidated page:", report["validation"]["consolidated_page"])
    print(
        "Standalone candidates:",
        len(report["statements"]["standalone"]["candidate_lines"])
    )
    print(
        "Consolidated candidates:",
        len(report["statements"]["consolidated"]["candidate_lines"])
    )
    print("Figures verified: No")
    print("Output:", output)


if __name__ == "__main__":
    main()
