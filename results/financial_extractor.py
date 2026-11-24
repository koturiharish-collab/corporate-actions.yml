
"""Share Sanket - Financial Statement Extractor

Extracts standalone and consolidated financial statement sections.
Unverified OCR figures are retained for review, not auto-published.
"""

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

STATEMENT_MARKERS = {
    "standalone": [
        "Statement of Unaudited Standalone Financial Results",
        "Statement of Unaudited Standalone",
    ],
    "consolidated": [
        "Statement of Unaudited Consolidated Financial Results",
        "Statement of Unaudited Consolidated",
    ],
}

FINANCIAL_LABELS = [
    "Revenue from operations",
    "Other income",
    "Total income",
    "Total expenses",
    "Profit before tax",
    "Profit after tax",
    "Owners of the Parent",
    "Net profit",
    "Basic earnings per share",
    "Diluted earnings per share",
    "Operating profit before working capital changes",
    "Net cash from operating activities",
    "Borrowings",
]


def read_text(symbol):
    path = EXTRACTED_DIR / f"{symbol}.txt"

    if not path.exists():
        raise FileNotFoundError(
            f"Extracted text not found: {path}"
        )

    return path.read_text(encoding="utf-8", errors="replace")


def find_statement_sections(text):
    lines = text.splitlines()
    sections = {}

    for statement_type, markers in STATEMENT_MARKERS.items():
        start = None

        for index, line in enumerate(lines):
            if any(marker.lower() in line.lower() for marker in markers):
                start = index
                break

        if start is None:
            sections[statement_type] = {
                "found": False,
                "text": "",
            }
            continue

        # Capture a limited block for human verification.
        end = min(start + 45, len(lines))

        sections[statement_type] = {
            "found": True,
            "start_line": start + 1,
            "text": "\n".join(lines[start:end]),
        }

    return sections


def find_financial_lines(text):
    found = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        cleaned = " ".join(line.split())

        for label in FINANCIAL_LABELS:
            if label.lower() in cleaned.lower():
                found.append({
                    "line": line_number,
                    "label": label,
                    "raw_text": cleaned,
                })
                break

    return found


def extract_eps_candidates(text):
    candidates = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if "earnings per share" not in line.lower():
            continue

        # Preserve the original text. Do not guess missing decimal points.
        candidates.append({
            "line": line_number,
            "raw_text": " ".join(line.split()),
            "verification": "Required",
        })

    return candidates


def build_report(symbol, text):
    sections = find_statement_sections(text)

    return {
        "symbol": symbol,
        "extracted_at": datetime.now(IST).isoformat(),
        "extraction_status": "Review required",
        "source_type": "PDF text extraction",
        "automatic_publication": False,
        "financial_units": "Not yet verified",
        "statements": sections,
        "financial_lines": find_financial_lines(text),
        "eps_candidates": extract_eps_candidates(text),
        "verified_financials": {
            "standalone": {},
            "consolidated": {},
        },
        "validation": {
            "standalone_found": sections["standalone"]["found"],
            "consolidated_found": sections["consolidated"]["found"],
            "figures_verified": False,
            "reason": (
                "OCR text must be checked against the original "
                "financial statement before publishing figures."
            ),
        },
    }


def main():
    symbol = (
        sys.argv[1].strip().upper()
        if len(sys.argv) > 1
        else "SFCTECH"
    )

    try:
        text = read_text(symbol)
    except FileNotFoundError as error:
        print(error)
        sys.exit(1)

    report = build_report(symbol, text)

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    output_file = REVIEW_DIR / f"{symbol}_financial_review.json"

    output_file.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("Financial extraction review completed.")
    print("Symbol:", symbol)
    print("Standalone statement:", report["validation"]["standalone_found"])
    print("Consolidated statement:", report["validation"]["consolidated_found"])
    print("Financial lines found:", len(report["financial_lines"]))
    print("EPS candidates found:", len(report["eps_candidates"]))
    print("Figures verified: No")
    print("Review file:", output_file)


if __name__ == "__main__":
    main()
