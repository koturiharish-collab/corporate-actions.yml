"""Share Sanket Results PDF extractor (first validated NSE statement-table parser).

Extracts quarterly columns from text-based NSE financial-results PDFs.
It deliberately returns None when a required row/column cannot be identified.
Amounts are normalised to INR crore; EPS remains INR/share.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional
from pypdf import PdfReader

NUMBER_RE = re.compile(r"(?<![A-Za-z])\(?-?\d[\d,]*(?:\.\d+)?\)?|(?<![A-Za-z])[-–—](?![A-Za-z])")

def _number(token: str) -> Optional[float]:
    token = token.strip()
    if token in {"-", "–", "—"}:
        return None
    neg = token.startswith("(") and token.endswith(")")
    token = token.strip("()").replace(",", "")
    try:
        value = float(token)
        return -value if neg else value
    except ValueError:
        return None

def _row_values(lines: list[str], labels: tuple[str, ...], occurrence: int = 0):
    matches = []
    for line in lines:
        low = re.sub(r"\s+", " ", line.lower()).strip()
        if any(label in low for label in labels):
            matches.append(line)
    if len(matches) <= occurrence:
        return None
    vals = [_number(m.group(0)) for m in NUMBER_RE.finditer(matches[occurrence])]
    # Remove a leading statement row index (e.g. "1", "7a") when present.
    if vals and re.match(r"^\s*\d+[a-z]?\s", matches[occurrence], re.I):
        vals = vals[1:]
    # The table rows have six numeric columns: Q2 current, Q1 current,
    # Q2 previous year, H1 current, H1 previous year, FY previous year.
    return vals[-6:] if len(vals) >= 6 else None

def _scope_pages(reader: PdfReader):
    found = {}
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        low = text.lower()
        if "statement of standalone" in low and "financial results" in low:
            found["standalone"] = (i, text)
        if "statement of consolidated" in low and "financial results" in low:
            found["consolidated"] = (i, text)
    return found

def _get(lines, labels, col):
    row = _row_values(lines, labels)
    return row[col] if row is not None and len(row) > col else None

def _growth(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * 100.0

def _scope_data(text: str):
    lines = text.splitlines()
    consolidated = "statement of consolidated" in text.lower()
    if consolidated:
        labels = {
            "revenue": ("revenue from operations",),
            "other_income": ("other income",),
            "pbt_before_exceptional": ("profit / (loss) before exceptional and tax", "profit/(loss) before exceptional and tax"),
            "finance_costs": ("finance costs",),
            "depreciation": ("depreciation and amortisation expense",),
            "pbt": ("profit / (loss) before tax", "profit/(loss) before tax"),
            "pat": ("profit/(loss) for the period/year -i+ii",),
            "eps": ("(a) basic - rs.",),
        }
    else:
        labels = {
            "revenue": ("revenue from operations",),
            "other_income": ("other income",),
            "pbt_before_exceptional": ("profit/(loss) before exceptional items and tax",),
            "finance_costs": ("finance costs",),
            "depreciation": ("depreciation and amortisation expense",),
            "pbt": ("profit/(loss) before tax",),
            "pat": ("profit/(loss) for the period",),
            "eps": ("a basic (₹)", "a basic (rs.)"),
        }
    # Column indexes: Q2 current, Q1 current, Q2 previous year.
    result = {}
    for field, variants in labels.items():
        row = _row_values(lines, variants, occurrence=2 if field == "eps" and consolidated else 0)
        if field == "eps" and consolidated and row is None:
            # If combined continuing+discontinued EPS row is absent, do not
            # silently substitute continuing-operations EPS.
            result[field] = {"q2": None, "q1": None, "q2_prev_year": None}
        else:
            result[field] = {
                "q2": row[0] if row and len(row) > 0 else None,
                "q1": row[1] if row and len(row) > 1 else None,
                "q2_prev_year": row[2] if row and len(row) > 2 else None,
            }
    # Operating EBITDA: PBT before exceptional items + finance costs + D&A
    # less other income, to avoid treating non-operating income as EBITDA.
    for period, col in (("q2", 0), ("q1", 1), ("q2_prev_year", 2)):
        pbt = result["pbt_before_exceptional"][period]
        fin = result["finance_costs"][period]
        da = result["depreciation"][period]
        other = result["other_income"][period]
        revenue = result["revenue"][period]
        ebitda_lakh = (pbt + fin + da - other) if None not in (pbt, fin, da, other) else None
        # Statement values in lakhs; convert to crore.
        result.setdefault("ebitda", {})[period] = ebitda_lakh / 100 if ebitda_lakh is not None else None
        result.setdefault("revenue_cr", {})[period] = revenue / 100 if revenue is not None else None
        result.setdefault("ebitda_margin_pct", {})[period] = (ebitda_lakh / revenue * 100) if ebitda_lakh is not None and revenue not in (None, 0) else None
    # Convert financial statement amounts from lakhs to crore; EPS stays rupees.
    for field in ("revenue", "other_income", "pbt_before_exceptional", "finance_costs", "depreciation", "pbt", "pat"):
        for period in ("q2", "q1", "q2_prev_year"):
            value = result[field][period]
            result[field][period] = value / 100 if value is not None else None
    result["comparisons"] = {}
    for field in ("revenue", "ebitda", "pbt", "pat", "eps"):
        vals = result[field]
        result["comparisons"][field] = {
            "yoy_pct": _growth(vals["q2"], vals["q2_prev_year"]),
            "qoq_pct": _growth(vals["q2"], vals["q1"]),
        }
    return result

def extract_results_pdf(pdf_path: str | Path):
    reader = PdfReader(str(pdf_path))
    scopes = _scope_pages(reader)
    if not scopes:
        raise ValueError("No recognisable NSE standalone/consolidated results tables found")
    out = {scope: _scope_data(text) for scope, (_, text) in scopes.items()}
    return {"source_file": Path(pdf_path).name, "pages": {k: v[0] + 1 for k, v in scopes.items()}, "financial_units": "INR crore (EPS in INR/share)", "financials_by_scope": out}

if __name__ == "__main__":
    import json, sys
    print(json.dumps(extract_results_pdf(sys.argv[1]), indent=2))
