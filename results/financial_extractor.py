
def find_actual_statement(pages, statement_type):
    """Find the actual financial-results table page."""

    heading = (
        "statement of unaudited standalone financial results"
        if statement_type == "standalone"
        else "statement of unaudited consolidated financial results"
    )

    for page in pages:
        normalized = " ".join(
            page["text"].lower().split()
        )

        has_heading = heading in normalized
        has_revenue = "revenue from operations" in normalized
        has_particulars = "particulars" in normalized
        has_currency = "indian rupees" in normalized

        # Require financial-table markers to avoid auditor reports.
        if (
            has_heading
            and has_revenue
            and has_particulars
            and has_currency
        ):
            return {
                "found": True,
                "page": page["page"],
                "raw_text": page["text"],
                "detection_method": "Financial table heading and table markers",
            }

    return {
        "found": False,
        "page": None,
        "raw_text": "",
        "detection_method": "Financial table not identified",
    }
