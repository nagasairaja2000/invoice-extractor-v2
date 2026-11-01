"""Unit tests for parsing, normalisation and validation of invoice schemas."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.schemas.invoice import (
    INVOICE_JSON_SCHEMA,
    ExtractionResult,
    ExtractionTimings,
    InvoiceData,
    LineItem,
    normalize_currency,
    parse_date,
    parse_decimal,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234.50", Decimal("1234.50")),
        ("₹ 1,18,000.50", Decimal("118000.50")),
        ("USD 19.99", Decimal("19.99")),
        ("(12.00)", Decimal("-12.00")),
        ("-5", Decimal("-5.00")),
        ("12.5%", Decimal("12.50")),
        (1234, Decimal("1234.00")),
        (12.5, Decimal("12.50")),
        (Decimal("7.129"), Decimal("7.13")),
        ("", None),
        ("   ", None),
        (None, None),
        ("N/A", None),
        ("not available", None),
        ("-", None),
        ("--", None),
        (True, None),
        ("abc", None),
    ],
)
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-15", date(2026, 3, 15)),
        ("2026/03/15", date(2026, 3, 15)),
        ("15-03-2026", date(2026, 3, 15)),
        ("15/03/2026", date(2026, 3, 15)),
        ("15.03.2026", date(2026, 3, 15)),
        ("15-Mar-2026", date(2026, 3, 15)),
        ("15 March 2026", date(2026, 3, 15)),
        ("Mar 15, 2026", date(2026, 3, 15)),
        ("March 15 2026", date(2026, 3, 15)),
        ("2026-04-01 00:00:00", date(2026, 4, 1)),
        ("2026-04-01T00:00:00Z", date(2026, 4, 1)),
        ("20260315", date(2026, 3, 15)),
        ("2026-13-45", None),
        ("sometime in March", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_parse_date_accepts_date_and_datetime_objects():
    assert parse_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert parse_date(datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)) == date(2026, 1, 2)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("₹", "INR"),
        ("Rs.", "INR"),
        ("₹ 1,000", "INR"),
        ("inr", "INR"),
        ("$", "USD"),
        ("US$ 19", "USD"),
        ("dollars", "USD"),
        ("€", "EUR"),
        ("£", "GBP"),
        ("AED", "AED"),
        ("XYZ", "XYZ"),
        ("", None),
        ("123", None),
        (None, None),
    ],
)
def test_normalize_currency(raw, expected):
    assert normalize_currency(raw) == expected


def test_invoice_data_normalises_messy_values():
    invoice = InvoiceData.model_validate(
        {
            "invoice_number": "  INV 42 \n",
            "invoice_date": "15 Mar 2026",
            "vendor_name": "  ACME   Components ",
            "vendor_address": "",
            "customer_name": None,
            "customer_address": "  ",
            "gst_number": " 29abcde1234f1z5 ",
            "subtotal": "₹ 20,000.00",
            "tax_amount": "3,600",
            "total_amount": 23600,
            "currency": "₹",
            "payment_due_date": "14/04/2026",
            "line_items": [
                {"description": " Widget ", "quantity": "2", "unit_price": "1,000", "amount": "2000"},
                {"description": None, "quantity": None, "unit_price": None, "amount": None},
            ],
        }
    )

    assert invoice.invoice_number == "INV 42"
    assert invoice.invoice_date == date(2026, 3, 15)
    assert invoice.vendor_name == "ACME Components"
    assert invoice.vendor_address is None
    assert invoice.customer_name is None
    assert invoice.customer_address is None
    assert invoice.gst_number == "29ABCDE1234F1Z5"
    assert invoice.subtotal == Decimal("20000.00")
    assert invoice.tax_amount == Decimal("3600.00")
    assert invoice.total_amount == Decimal("23600.00")
    assert invoice.currency == "INR"
    assert invoice.payment_due_date == date(2026, 4, 14)
    assert len(invoice.line_items) == 2
    assert invoice.line_items[0].description == "Widget"
    assert invoice.line_items[0].quantity == Decimal("2.00")
    assert invoice.line_items[1].description is None


def test_invoice_data_defaults_are_null_not_invented():
    invoice = InvoiceData()

    assert invoice.invoice_number is None
    assert invoice.invoice_date is None
    assert invoice.total_amount is None
    assert invoice.currency is None
    assert invoice.line_items == []


def test_line_items_accept_a_single_dict_and_drop_empty_values():
    invoice = InvoiceData.model_validate(
        {"line_items": {"description": "Only item", "amount": "10"}}
    )
    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].amount == Decimal("10.00")

    assert InvoiceData.model_validate({"line_items": None}).line_items == []
    assert InvoiceData.model_validate({"line_items": "oops"}).line_items == []


def test_invoice_data_ignores_unknown_fields():
    invoice = InvoiceData.model_validate({"invoice_number": "X", "hallucinated": "value"})
    assert invoice.invoice_number == "X"
    assert not hasattr(invoice, "hallucinated")


def test_line_item_parses_quantity_with_units():
    item = LineItem.model_validate({"description": "Cable", "quantity": "2 pcs"})
    assert item.quantity == Decimal("2.00")


def test_extraction_result_defaults():
    result = ExtractionResult(
        invoice=InvoiceData(),
        pdf_type="digital",
        approach="pymupdf+gemini",
    )
    assert result.timings == ExtractionTimings()
    assert result.timings.total == 0.0
    assert result.warnings == []
    assert result.tokens == {}
    assert isinstance(result.extracted_at, datetime)


def test_json_schema_covers_every_invoice_field():
    properties = set(INVOICE_JSON_SCHEMA["properties"])
    assert properties == set(InvoiceData.model_fields)
    assert set(INVOICE_JSON_SCHEMA["required"]) == properties
    assert INVOICE_JSON_SCHEMA["properties"]["line_items"]["type"] == "array"
    assert (
        set(INVOICE_JSON_SCHEMA["properties"]["line_items"]["items"]["properties"])
        == set(LineItem.model_fields)
    )
