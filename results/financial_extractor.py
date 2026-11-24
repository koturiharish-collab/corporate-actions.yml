
"""Share Sanket - Financial Statement Review Extractor.

Locates the actual quarterly financial-results pages rather than
matching the auditors' limited-review report.

OCR figures are preserved as raw text for verification.
No financial values are automatically published.
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

STATEMENTS = {
    "standalone": (
        "statement of unaudited standalone financial results "
        "for the quarter and half year ended"
    ),
    "consolidated": (
        "statement of unaudited consolidated financial results "
        "for the quarter and half year ended"
    ),
}

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
        raise FileNotFoundError(
            f"PDF text file not found: {path}"
        )

    return path.read_text(
        encoding="utf-8",
        errors="replace"
    )


def split_pdf_pages(text):
    """Split extracted text using PDF page markers."""
    parts = re.split(
        r"---\s*PAGE\s+(\d+)\s*---",
        text,
        flags=re.IGNORECASE
    )

    pages = []

    for index in range(1, len(parts), 2):
        page_number = int(parts[index])
        page_text = parts[index + 1]

        pages.append({
            "page": page_number,
            "text": page_text.strip()
        })

    if not pages:
        pages.append({
            "page": None,
            "text": text
        })

    return pages


def find_actual_statement(pages, statement_type):
    """Find the financial-results page, not the auditor report."""

    heading = STATEMENTS[statement_type]

    for page in pages:
        normalized = " ".join(
            page["text"].lower().split()
        )

        if heading in normalized:
            return {
                "found": True,
                "page": page["page"],
                "raw_text": page["text"],
                "detection_method": "Exact financial-results heading",
            }

    return {
        "found": False,
        "page": None,
        "raw_text": "",
        "detection_method": "Heading not found",
    }


def extract_candidate_lines(statement_text):
    """Keep original OCR lines without guessing numeric columns."""

    candidates = []

    for line_number, line in enumerate(
        statement_text.splitlines(),
        start=1
    ):
        cleaned = " ".join(line.split())

        if not cleaned:
            continue

        for label in FINANCIAL_LABELS:
            if label.lower() in cleaned.lower():
                candidates.append({
                    "line": line_number,
                    "label": label,
                    "raw_text": cleaned,
                    "verified": False,
                })
                break

    return candidates


def detect_units(statement_text):
    normalized = " ".join(statement_text.lower().split())

    if "indian rupees in millions" in normalized:
        return "Indian Rupees in Millions"

    if "indian rupees in crores" in normalized:
        return "Indian Rupees in Crores"

    if "indian rupees" in normalized:
        return "Indian Rupees; denomination needs review"

    return "Not identified"


def build_statement_review(pages, statement_type):
    statement = find_actual_statement(
        pages,
        statement_type
    )

    if not statement["found"]:
        return {
            **statement,
            "financial_units": "Not identified",
            "candidate_lines": [],
            "figures_verified": False,
        }

    raw_text = statement["raw_text"]

    return {
        **statement,
        "financial_units": detect_units(raw_text),
        "candidate_lines": extract_candidate_lines(raw_text),
        "figures_verified": False,
    }


def build_report(symbol, text):
    pages = split_pdf_pages(text)

    standalone = build_statement_review(
        pages,
        "standalone"
    )

    consolidated = build_statement_review(
        pages,
        "consolidated"
    )

    return {
        "symbol": symbol,
        "extracted_at": datetime.now(IST).isoformat(),
        "extraction_status": "Review required",
        "source_type": "Official PDF text extraction",
        "automatic_publication": False,
        "statements": {
            "standalone": standalone,
            "consolidated": consolidated,
        },
        "validation": {
            "standalone_found": standalone["found"],
            "consolidated_found": consolidated["found"],
            "standalone_page": standalone["page"],
            "consolidated_page": consolidated["page"],
            "figures_verified": False,
            "reason": (
                "OCR values and reporting columns require "
                "verification against the original PDF."
            ),
        },
    }


def main():
    symbol = (
        sys.argv[1].strip().upper()
        if len(sys.argv) > 1
        else "SFC_TEST"
    )

    try:
        text = read_text(symbol)
    except FileNotFoundError as error:
        print(error)
        sys.exit(1)

    report = build_report(symbol, text)

    REVIEW_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        REVIEW_DIR /
        f"{symbol}_financial_review.json"
    )

    output_file.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print("Financial statement review completed.")
    print("Symbol:", symbol)
    print(
        "Standalone page:",
        report["validation"]["standalone_page"]
    )
    print(
        "Consolidated page:",
        report["validation"]["consolidated_page"]
    )
    print(
        "Standalone candidates:",
        len(report["statements"]["standalone"]["candidate_lines"])
    )
    print(
        "Consolidated candidates:",
        len(report["statements"]["consolidated"]["candidate_lines"])
    )
    print("Figures verified: No")
    print("Output:", output_file)


if __name__ == "__main__":
    main()
