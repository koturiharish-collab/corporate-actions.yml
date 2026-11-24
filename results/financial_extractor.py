
"""Share Sanket - Financial Statement Extractor.

Detects standalone and consolidated financial statement pages.
Extracts candidate lines for manual verification.
Never publishes unverified financial figures.
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

STATEMENT_HEADINGS = {
    "standalone": (
        "statement of unaudited standalone financial results"
    ),
    "consolidated": (
        "statement of unaudited consolidated financial results"
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
            f"Extracted PDF text not found: {path}"
        )

    return path.read_text(
        encoding="utf-8",
        errors="replace"
    )


def split_pdf_pages(text):
    """Split text using the page markers from the PDF extractor."""

    parts = re.split(
        r"---\s*PAGE\s+(\d+)\s*---",
        text,
        flags=re.IGNORECASE
    )

    pages = []

    for index in range(1, len(parts), 2):
        pages.append({
            "page": int(parts[index]),
            "text": parts[index + 1].strip()
        })

    if not pages:
        raise ValueError(
            "PDF page markers were not found. "
            "Check the output of pdf_extractor.py."
        )

    return pages


def normalize_text(text):
    """Normalize common OCR errors without changing source values."""

    text = text.lower()

    # Common OCR substitutions
    text = text.replace("conso1idated", "consolidated")
    text = text.replace("consolidat ed", "consolidated")
    text = text.replace("anded", "ended")

    return " ".join(text.split())


def has_exact_heading(text, statement_type):
    """Require the correct complete statement heading."""

    if statement_type not in STATEMENT_HEADINGS:
        raise ValueError(
            f"Unknown statement type: {statement_type}"
        )

    clean = normalize_text(text)

    if statement_type == "standalone":
        pattern = (
            r"\bstatement\s+of\s+unaudited\s+"
            r"standalone\s+financial\s+results\b"
        )
    else:
        pattern = (
            r"\bstatement\s+of\s+unaudited\s+"
            r"consolidated\s+financial\s+results\b"
        )

    return re.search(pattern, clean) is not None


def find_actual_statement(pages, statement_type):
    """Find the correct financial-results table page."""

    for page in pages:
        raw_text = page["text"]
        clean = normalize_text(raw_text)

        correct_heading = has_exact_heading(
            raw_text,
            statement_type
        )

        has_revenue = (
            "revenue from operations" in clean
        )

        has_particulars = (
            "particulars" in clean
        )

        has_quarter = (
            "quarter" in clean
        )

        has_half_year = (
            "half year" in clean
        )

        # All conditions are required.
        if (
            correct_heading
            and has_revenue
            and has_particulars
            and has_quarter
            and has_half_year
        ):
            return {
                "found": True,
                "page": page["page"],
                "raw_text": raw_text,
                "detection_method": (
                    "Exact statement heading and financial table markers"
                )
            }

    return {
        "found": False,
        "page": None,
        "raw_text": "",
        "detection_method": (
            "Correct financial statement heading not identified"
        )
    }


def detect_units(text):
    clean = normalize_text(text)

    if "rupees in millions" in clean:
        return "Indian Rupees in Millions"

    if "rupees in crores" in clean:
        return "Indian Rupees in Crores"

    if "indian rupees" in clean:
        return "Indian Rupees - denomination needs review"

    return "Not identified"


def extract_candidate_lines(statement_text):
    """Preserve original lines; do not guess financial values."""

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
                    "verified": False
                })
                break

    return candidates


def build_statement_review(pages, statement_type):
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

    raw_text = result["raw_text"]

    return {
        **result,
        "financial_units": detect_units(raw_text),
        "candidate_lines": extract_candidate_lines(
            raw_text
        ),
        "figures_verified": False
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
            "figures_verified": False,
            "reason": (
                "Extracted financial figures require "
                "verification against the original PDF."
            )
        }
    }


def main():
    symbol = (
        sys.argv[1].strip().upper()
        if len(sys.argv) > 1
        else "SFC_TEST"
    )

    if not re.fullmatch(r"[A-Z0-9_-]+", symbol):
        raise ValueError("Invalid symbol format.")

    text = read_text(symbol)
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
