"""Invoice schemas with normalisation and validation."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

_NULL_STRINGS = {"", "null", "none", "n/a", "na", "nil", "-", "--", "not available", "not provided"}

_CURRENCY_MAP = {
    "₹": "INR",
    "₹.": "INR",
    "RS": "INR",
    "RS.": "INR",
    "INR": "INR",
    "RUPEE": "INR",
    "RUPEES": "INR",
    "$": "USD",
    "US$": "USD",
    "USD": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "¥": "JPY",
    "JPY": "JPY",
    "AED": "AED",
    "SGD": "SGD",
    "CAD": "CAD",
    "AUD": "AUD",
}

_CURRENCY_SYMBOLS = {
    "₹": "INR",
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%Y%m%d",
)


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _NULL_STRINGS)


def parse_decimal(value: Any) -> Decimal | None:
    """Convert messy numeric strings ("₹ 1,18,000.50", "(12.00)") to Decimal."""
    if _is_null(value):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))

    if isinstance(value, int):
        return Decimal(value).quantize(Decimal("0.01"))

    if isinstance(value, float):
        return Decimal(str(value)).quantize(Decimal("0.01"))

    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)

    if cleaned in {"", "-", ".", "-.", "--"}:
        return None

    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None

    if negative and number > 0:
        number = -number

    return number.quantize(Decimal("0.01"))


def parse_date(value: Any) -> date | None:
    """Parse common invoice date formats into a ``date``."""
    if _is_null(value):
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()
    # Remove a trailing time component, e.g. "2026-04-01 00:00:00".
    text = re.sub(r"[ T]\d{2}:\d{2}(:\d{2})?.*$", "", text)

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def normalize_currency(value: Any) -> str | None:
    if _is_null(value):
        return None

    text = str(value).strip()
    upper = text.upper().replace(" ", "")

    if upper in _CURRENCY_MAP:
        return _CURRENCY_MAP[upper]

    # Symbols are unambiguous and must be checked before word tokens: a naive
    # substring search would match "RS" inside "DOLLARS".
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code

    # Match alphabetic tokens on word boundaries, e.g. "USD" in "USD 19.99"
    # but not "RS" in "DOLLARS".
    for token, code in _CURRENCY_MAP.items():
        if not token.isalpha():
            continue
        if re.search(rf"(?<![A-Z]){re.escape(token)}(?![A-Z])", upper):
            return code

    if re.fullmatch(r"[A-Z]{3}", upper):
        return upper

    return None


def _clean_text(value: Any) -> str | None:
    if _is_null(value):
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> str | None:
        return _clean_text(value)

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def _numbers(cls, value: Any) -> Decimal | None:
        return parse_decimal(value)


class InvoiceData(BaseModel):
    """Validated invoice fields. Missing information is always ``null``."""

    model_config = ConfigDict(extra="ignore")

    invoice_number: str | None = None
    invoice_date: date | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    gst_number: str | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    payment_due_date: date | None = None

    @field_validator(
        "invoice_number",
        "vendor_name",
        "vendor_address",
        "customer_name",
        "customer_address",
        mode="before",
    )
    @classmethod
    def _strings(cls, value: Any) -> str | None:
        return _clean_text(value)

    @field_validator("gst_number", mode="before")
    @classmethod
    def _gstin(cls, value: Any) -> str | None:
        cleaned = _clean_text(value)
        if cleaned is None:
            return None
        return re.sub(r"\s+", "", cleaned).upper()

    @field_validator("invoice_date", "payment_due_date", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> date | None:
        return parse_date(value)

    @field_validator("subtotal", "tax_amount", "total_amount", mode="before")
    @classmethod
    def _amounts(cls, value: Any) -> Decimal | None:
        return parse_decimal(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> str | None:
        return normalize_currency(value)

    @field_validator("line_items", mode="before")
    @classmethod
    def _line_items(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, (dict, str)) or item]
        return []


class ExtractionTimings(BaseModel):
    total: float = 0.0
    analysis: float | None = None
    text_extraction: float | None = None
    ocr: float | None = None
    vision: float | None = None
    llm: float | None = None


class ExtractionResult(BaseModel):
    """Complete extraction result returned by the API and persisted to disk."""

    invoice: InvoiceData
    pdf_type: Literal["digital", "scanned"]
    approach: str
    model_used: str | None = None
    timings: ExtractionTimings = Field(default_factory=ExtractionTimings)
    tokens: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    raw_text_chars: int = 0
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# JSON schema sent to Gemini. Everything is a string on purpose:
# it avoids schema/Decimal/date incompatibilities between the SDK and Gemini,
# and Pydantic performs the real coercion afterwards. An empty string means
# "not present" and is converted to null.
# ---------------------------------------------------------------------------
INVOICE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "invoice_number": {
            "type": "string",
            "description": "Invoice number or reference. Empty string if absent.",
        },
        "invoice_date": {
            "type": "string",
            "description": "Invoice date in YYYY-MM-DD format. Empty string if absent.",
        },
        "vendor_name": {
            "type": "string",
            "description": "Supplier / vendor / seller legal name. Empty string if absent.",
        },
        "vendor_address": {
            "type": "string",
            "description": "Supplier full address. Empty string if absent.",
        },
        "customer_name": {
            "type": "string",
            "description": "Buyer / customer / bill-to name. Empty string if absent.",
        },
        "customer_address": {
            "type": "string",
            "description": "Buyer full address. Empty string if absent.",
        },
        "gst_number": {
            "type": "string",
            "description": "Supplier GSTIN / tax identification number. Empty string if absent.",
        },
        "subtotal": {
            "type": "string",
            "description": "Net amount before tax, digits and decimal point only. Empty string if absent.",
        },
        "tax_amount": {
            "type": "string",
            "description": "Total tax amount, digits and decimal point only. Empty string if absent.",
        },
        "total_amount": {
            "type": "string",
            "description": "Grand total payable including tax, digits and decimal point only. Empty string if absent.",
        },
        "currency": {
            "type": "string",
            "description": "ISO 4217 currency code such as INR, USD, EUR. Empty string if absent.",
        },
        "line_items": {
            "type": "array",
            "description": "Every purchased line item. Empty array if none are present.",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "string"},
                    "unit_price": {"type": "string"},
                    "amount": {"type": "string"},
                },
                "required": ["description", "quantity", "unit_price", "amount"],
            },
        },
        "payment_due_date": {
            "type": "string",
            "description": "Payment due date in YYYY-MM-DD format. Empty string if absent.",
        },
    },
    "required": [
        "invoice_number",
        "invoice_date",
        "vendor_name",
        "vendor_address",
        "customer_name",
        "customer_address",
        "gst_number",
        "subtotal",
        "tax_amount",
        "total_amount",
        "currency",
        "line_items",
        "payment_due_date",
    ],
}
