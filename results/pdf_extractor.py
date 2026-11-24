
"""
Share Sanket - PDF Results Extractor

Downloads an official NSE/BSE results PDF,
extracts its text and saves the extracted text.

This version does not automatically calculate
or publish financial figures.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "extracted"

ALLOWED_DOMAINS = {
    "nseindia.com",
    "www.nseindia.com",
    "nsearchives.nseindia.in",
    "bseindia.com",
    "www.bseindia.com",
}

MAX_PDF_SIZE = 25 * 1024 * 1024
TIMEOUT = 45


def is_official_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_DOMAINS
        )
    except (TypeError, ValueError):
        return False


def safe_filename(value):
    value = re.sub(r"[^A-Za-z0-9_-]", "_", str(value))
    return value[:80] or "results"


def download_pdf(url):
    if not is_official_url(url):
        raise ValueError("URL is not an approved official NSE/BSE URL")

    response = requests.get(
        url,
        timeout=TIMEOUT,
        stream=True,
        headers={"User-Agent": "ShareSanketResults/1.0"},
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type", ""
    ).lower()

    if "pdf" not in content_type and not url.lower().endswith(".pdf"):
        response.close()
        raise ValueError("The URL did not return a PDF response")

    content = bytearray()

    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue

            content.extend(chunk)

            if len(content) > MAX_PDF_SIZE:
                raise ValueError("PDF exceeds 25 MB limit")
    finally:
        response.close()

    if not content.startswith(b"%PDF"):
        raise ValueError("Downloaded file is not a valid PDF")

    return bytes(content)


def extract_pdf_text(pdf_bytes):
    import io

    reader = PdfReader(io.BytesIO(pdf_bytes))

    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported")

    pages = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""

        pages.append(
            f"\n--- PAGE {page_number} ---\n{text}"
        )

    return "\n".join(pages), len(reader.pages)


def save_extraction(symbol, source_url, text, page_count):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(symbol)

    text_path = OUTPUT_DIR / f"{filename}.txt"
    json_path = OUTPUT_DIR / f"{filename}.json"

    text_path.write_text(text, encoding="utf-8")

    metadata = {
        "symbol": symbol,
        "source_url": source_url,
        "extracted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "page_count": page_count,
        "text_file": text_path.name,
        "extraction_status": (
            "Text extracted - figures require verification"
        ),
    }

    json_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return text_path, json_path


def extract_results_pdf(symbol, pdf_url):
    print("Share Sanket PDF Results Extractor")
    print("Downloading official PDF...")

    pdf_bytes = download_pdf(pdf_url)

    print("Extracting PDF text...")

    text, page_count = extract_pdf_text(pdf_bytes)

    if not text.strip():
        raise ValueError(
            "No selectable text found. PDF may require OCR."
        )

    text_path, json_path = save_extraction(
        symbol,
        pdf_url,
        text,
        page_count,
    )

    print("Extraction completed.")
    print(f"Pages: {page_count}")
    print(f"Text file: {text_path}")
    print(f"Metadata: {json_path}")
    print("Financial figures have not been verified.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python results/pdf_extractor.py "
            "SYMBOL PDF_URL"
        )
        sys.exit(1)

    try:
        extract_results_pdf(
            sys.argv[1],
            sys.argv[2],
        )
    except Exception as exc:
        print(f"Extraction failed: {exc}")
        sys.exit(1)
