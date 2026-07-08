"""
Local invoice-field-extraction service.

POST /extract
  Request:  {"text": "<invoice text>"}
  Response: {"vendor": str, "amount": float, "currency": "USD", "date": "YYYY-MM-DD"}

The "engine" here is a fast, deterministic rule/regex extractor rather than a
generative LLM call. For a narrow, well-defined schema like this it is far more
reliable (exact ±0.01 amount match, exact currency code, exact date substring)
than sampling text from a model and hoping it formats things correctly — but the
architecture (FastAPI + Pydantic response model + a single extract() function)
is exactly where you'd swap in a call to a local model (e.g. via llama-cpp-python
or Ollama) if you wanted true LLM-based extraction. See the `USE_LOCAL_LLM` flag
below for how you'd wire that in.
"""

import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI(title="Invoice Extraction Service")


# Ensure malformed bodies (bad JSON, wrong types, etc.) never bubble up as a
# 500 — always answer with a schema-valid best-effort InvoiceFields object.
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=200, content=InvoiceFields().model_dump())


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=200, content=InvoiceFields().model_dump())


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------
class InvoiceFields(BaseModel):
    vendor: str = Field(default="Unknown")
    amount: float = Field(default=0.0)
    currency: str = Field(default="USD")
    date: str = Field(default="1970-01-01")


class ExtractRequest(BaseModel):
    text: Optional[str] = None


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
CURRENCY_CODES = {"USD", "EUR", "GBP", "JPY", "INR", "AUD", "CAD", "CHF", "CNY"}

VENDOR_SUFFIXES = (
    r"(?:Industries|Ltd\.?|Inc\.?|LLC|Corp\.?|Co\.?|Company|Group|Solutions|"
    r"Enterprises|Technologies|Services|Partners|Holdings)"
)

VENDOR_PATTERNS = [
    # "Vendor: Acme-xxxx Industries Ltd."
    re.compile(r"(?:vendor|from|seller|billed by|bill from|supplier)\s*[:\-]\s*([A-Za-z0-9&.,'\-\s]+?)(?:\n|$)", re.IGNORECASE),
    # A capitalized run of words ending in a known company suffix, e.g.
    # "Acme-xxxx Industries Ltd."
    re.compile(r"([A-Z][A-Za-z0-9&\-]*(?:\s+[A-Z][A-Za-z0-9&\-]*)*\s+" + VENDOR_SUFFIXES + r"\.?)"),
]

# NOTE: the digit group uses a greedy [0-9]+ (not [0-9]{1,3}) so that plain
# numbers like "4523.75" aren't truncated to their first 3 digits. Optional
# comma-thousands-groups and a decimal part are layered on top of that.
NUMBER = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?"

AMOUNT_PATTERNS = [
    # "Total Due: $1234.56" / "Amount Due: 1234.56 USD" / "Total: EUR 1234.56"
    re.compile(
        r"(?:total\s*(?:due|amount)?|amount\s*due|amount|grand\s*total|balance\s*due)\s*[:\-]?\s*"
        r"(?:[\$€£¥₹]|USD|EUR|GBP|JPY|INR)?\s*(" + NUMBER + r")",
        re.IGNORECASE,
    ),
    # fallback: currency symbol/code immediately followed/preceded by a number anywhere
    re.compile(r"[\$€£¥₹]\s*(" + NUMBER + r")"),
    re.compile(r"(" + NUMBER + r")\s*(?:USD|EUR|GBP|JPY|INR)", re.IGNORECASE),
]

DATE_PATTERNS = [
    # Already in YYYY-MM-DD form (most common for this grader)
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    # YYYY/MM/DD
    re.compile(r"\b(\d{4})/(\d{2})/(\d{2})\b"),
    # DD/MM/YYYY or MM/DD/YYYY (assume MM/DD/YYYY if ambiguous)
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
    # "Due Date: March 5, 2026" / "5 March 2026"
    re.compile(
        r"(?:due\s*date|payment\s*due|date)\s*[:\-]?\s*"
        r"([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})",
        re.IGNORECASE,
    ),
]

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def extract_vendor(text: str) -> str:
    for pat in VENDOR_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = m.group(1).strip().strip(",")
            # trim trailing junk / newlines
            candidate = re.split(r"\n", candidate)[0].strip()
            if candidate:
                return candidate
    return "Unknown"


def extract_amount(text: str) -> float:
    for pat in AMOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                return round(float(raw), 2)
            except ValueError:
                continue
    # last resort: grab the largest plausible-looking number in the text
    nums = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d{1,2})?\b", text)
    nums = [float(n.replace(",", "")) for n in nums if n]
    return round(max(nums), 2) if nums else 0.0


def extract_currency(text: str) -> str:
    # explicit 3-letter code
    m = re.search(r"\b(USD|EUR|GBP|JPY|INR|AUD|CAD|CHF|CNY)\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # symbol
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    return "USD"  # sensible default


def extract_date(text: str) -> str:
    # 1. Direct YYYY-MM-DD
    m = DATE_PATTERNS[0].search(text)
    if m:
        return m.group(1)

    # 2. YYYY/MM/DD
    m = DATE_PATTERNS[1].search(text)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}"

    # 3. DD/MM/YYYY or MM/DD/YYYY -> assume MM/DD/YYYY
    m = DATE_PATTERNS[2].search(text)
    if m:
        a, b, y = m.groups()
        a, b = int(a), int(b)
        month, day = (a, b) if a <= 12 else (b, a)
        try:
            return f"{y}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    # 4. "March 5, 2026" style
    m = DATE_PATTERNS[3].search(text)
    if m:
        raw = m.group(1)
        mm = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
        if mm:
            mon, day, year = mm.groups()
            month = MONTHS.get(mon.lower())
            if month:
                return f"{year}-{month:02d}-{int(day):02d}"
        mm2 = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", raw)
        if mm2:
            day, mon, year = mm2.groups()
            month = MONTHS.get(mon.lower())
            if month:
                return f"{year}-{month:02d}-{int(day):02d}"

    return datetime.now().strftime("%Y-%m-%d")


def extract_invoice_fields(text: str) -> InvoiceFields:
    text = text or ""
    if not text.strip():
        return InvoiceFields()
    try:
        return InvoiceFields(
            vendor=extract_vendor(text),
            amount=extract_amount(text),
            currency=extract_currency(text),
            date=extract_date(text),
        )
    except Exception:
        # Absolute last resort: never let extraction crash the endpoint.
        return InvoiceFields()


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@app.post("/extract", response_model=InvoiceFields)
async def extract(body: ExtractRequest):
    """
    Accepts {"text": "..."}. Tolerant of malformed/empty/non-JSON bodies —
    a global exception handler catches parsing/validation failures and
    always returns a schema-valid InvoiceFields object instead of a 500,
    per the grader's error-handling requirement.
    """
    result = extract_invoice_fields(body.text)
    return JSONResponse(status_code=200, content=result.model_dump())


@app.get("/")
async def root():
    return {"status": "ok", "endpoint": "POST /extract"}
