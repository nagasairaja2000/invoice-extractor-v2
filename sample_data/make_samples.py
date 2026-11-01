"""Generate synthetic invoice PDFs for demos and manual testing.

Everything in this folder is fictional. No real company, person, GSTIN or
bank account is represented. Run from the project root:

    .venv\\Scripts\\python.exe sample_data\\make_samples.py
"""

from __future__ import annotations

from pathlib import Path

import fitz

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 595, 842  # A4 at 72 dpi
MARGIN = 48
INK = (0.07, 0.09, 0.15)
MUTED = (0.42, 0.46, 0.55)
BRAND = (0.25, 0.23, 0.86)
LINE = (0.86, 0.88, 0.93)

INVOICES = [
    {
        "filename": "digital_invoice_apex.pdf",
        "vendor": "Apex Software Solutions Pvt Ltd",
        "vendor_address": "4th Floor, Orion Tech Park, Outer Ring Road, Bengaluru, Karnataka 560103",
        "vendor_gstin": "29ABCDE1234F1Z5",
        "vendor_email": "billing@apexsoftware.example",
        "customer": "Nimbus Retail LLP",
        "customer_address": "Unit 12, Harbour Business Centre, Andheri East, Mumbai, Maharashtra 400069",
        "customer_gstin": "27PQRST6789K1Z2",
        "invoice_number": "INV-2026-0142",
        "invoice_date": "12 Mar 2026",
        "due_date": "11 Apr 2026",
        "currency": "INR",
        "items": [
            ("Enterprise platform licence (annual)", "1", "85,000.00", "85,000.00"),
            ("Implementation & onboarding services", "40", "750.00", "30,000.00"),
            ("Priority support add-on", "1", "10,000.00", "10,000.00"),
        ],
        "subtotal": "1,25,000.00",
        "tax_lines": [("CGST @ 9%", "11,250.00"), ("SGST @ 9%", "11,250.00")],
        "total": "1,47,500.00",
        "notes": "Payment due within 30 days. Please quote the invoice number in all remittances.",
        "bank": "Synthetic Bank · A/c 0000 1111 2222 · IFSC TEST0001234",
    },
    {
        "filename": "digital_invoice_globex.pdf",
        "vendor": "Globex Analytics Inc.",
        "vendor_address": "180 Market Street, Suite 900, San Francisco, CA 94105, United States",
        "vendor_gstin": "",
        "vendor_email": "accounts@globex.example",
        "customer": "Northwind Studios LLC",
        "customer_address": "77 West 24th Street, New York, NY 10011, United States",
        "customer_gstin": "",
        "invoice_number": "GA-2026-0087",
        "invoice_date": "02 Feb 2026",
        "due_date": "04 Mar 2026",
        "currency": "USD",
        "items": [
            ("Data enrichment API — 250k records", "1", "4,500.00", "4,500.00"),
            ("Custom dashboard build", "18", "145.00", "2,610.00"),
            ("Monthly platform fee", "3", "300.00", "900.00"),
        ],
        "subtotal": "8,010.00",
        "tax_lines": [("Sales tax @ 8.5%", "680.85")],
        "total": "8,690.85",
        "notes": "Net 30. Late payments accrue interest at 1.5% per month.",
        "bank": "Synthetic Credit Union · A/c 987654321 · Routing 000000000",
    },
    {
        "filename": "scanned_invoice_zenith.pdf",
        "vendor": "Zenith Industrial Supplies",
        "vendor_address": "Plot 18, Phase II, Peenya Industrial Area, Bengaluru, Karnataka 560058",
        "vendor_gstin": "29LMNOP4321Q1Z8",
        "vendor_email": "sales@zenithsupplies.example",
        "customer": "Meridian Manufacturing Co.",
        "customer_address": "Survey 44/2, Hosur Road, Bengaluru, Karnataka 560068",
        "customer_gstin": "29VWXYZ8765R1Z4",
        "invoice_number": "ZIS/2026/0451",
        "invoice_date": "28 Jan 2026",
        "due_date": "27 Feb 2026",
        "currency": "INR",
        "items": [
            ("Stainless steel fasteners M8 (box of 500)", "12", "1,450.00", "17,400.00"),
            ("Industrial bearing 6205-2RS", "24", "385.00", "9,240.00"),
            ("Courier & handling", "1", "650.00", "650.00"),
        ],
        "subtotal": "27,290.00",
        "tax_lines": [("IGST @ 18%", "4,912.20")],
        "total": "32,202.20",
        "notes": "Goods once sold will not be taken back. Warranty as per manufacturer terms.",
        "bank": "Synthetic Bank · A/c 5555 6666 7777 · IFSC TEST0005678",
    },
]


def _text(page: fitz.Page, x: float, y: float, value: str, size: float = 10,
          color: tuple[float, float, float] = INK, font: str = "helv") -> None:
    page.insert_text((x, y), value, fontsize=size, fontname=font, color=color)


def _right_text(page: fitz.Page, right: float, y: float, value: str, size: float = 10,
                color: tuple[float, float, float] = INK, font: str = "helv") -> None:
    width = fitz.get_text_length(value, fontname=font, fontsize=size)
    _text(page, right - width, y, value, size=size, color=color, font=font)


def build_page(document: fitz.Document, data: dict) -> fitz.Page:
    page = document.new_page(width=WIDTH, height=HEIGHT)

    # Header band
    page.draw_rect(fitz.Rect(0, 0, WIDTH, 104), color=None, fill=(0.965, 0.972, 0.996))
    page.draw_rect(fitz.Rect(0, 102, WIDTH, 104), color=None, fill=BRAND)

    _text(page, MARGIN, 52, data["vendor"], size=17, font="hebo")
    _text(page, MARGIN, 68, data["vendor_address"], size=7.6, color=MUTED)
    if data["vendor_gstin"]:
        _text(page, MARGIN, 80, f"GSTIN: {data['vendor_gstin']}", size=8, color=MUTED)
    _text(page, MARGIN, 92, data["vendor_email"], size=8, color=MUTED)

    _right_text(page, WIDTH - MARGIN, 48, "TAX INVOICE", size=19, color=BRAND, font="hebo")
    _right_text(page, WIDTH - MARGIN, 66, f"Invoice no. {data['invoice_number']}", size=9)
    _right_text(page, WIDTH - MARGIN, 80, f"Invoice date: {data['invoice_date']}", size=9)
    _right_text(page, WIDTH - MARGIN, 94, f"Due date: {data['due_date']}", size=9)

    # Bill-to block
    y = 132
    _text(page, MARGIN, y, "BILL TO", size=8, color=MUTED, font="hebo")
    _text(page, MARGIN, y + 15, data["customer"], size=11, font="hebo")
    _text(page, MARGIN, y + 30, data["customer_address"], size=8.2, color=MUTED)
    if data["customer_gstin"]:
        _text(page, MARGIN, y + 42, f"GSTIN: {data['customer_gstin']}", size=8.2, color=MUTED)

    _right_text(page, WIDTH - MARGIN, y, f"Currency: {data['currency']}", size=9, color=MUTED)
    _right_text(page, WIDTH - MARGIN, y + 15, "Payment terms: Net 30", size=9, color=MUTED)

    # Line-item table
    table_top = 210
    header_h = 24
    page.draw_rect(
        fitz.Rect(MARGIN, table_top, WIDTH - MARGIN, table_top + header_h),
        color=None,
        fill=(0.965, 0.972, 0.996),
    )
    columns = {
        "description": MARGIN + 8,
        "qty": 372,
        "rate": 432,
        "amount_right": WIDTH - MARGIN - 8,
    }
    header_y = table_top + 16
    _text(page, columns["description"], header_y, "DESCRIPTION", size=7.4, color=MUTED, font="hebo")
    _right_text(page, 400, header_y, "QTY", size=7.4, color=MUTED, font="hebo")
    _right_text(page, 470, header_y, "RATE", size=7.4, color=MUTED, font="hebo")
    _right_text(page, columns["amount_right"], header_y, "AMOUNT", size=7.4, color=MUTED, font="hebo")

    row_y = table_top + header_h + 18
    for description, qty, rate, amount in data["items"]:
        _text(page, columns["description"], row_y, description, size=8.8)
        _right_text(page, 400, row_y, qty, size=8.8)
        _right_text(page, 470, row_y, rate, size=8.8)
        _right_text(page, columns["amount_right"], row_y, amount, size=8.8)
        page.draw_line(
            fitz.Point(MARGIN, row_y + 9),
            fitz.Point(WIDTH - MARGIN, row_y + 9),
            color=LINE,
            width=0.6,
        )
        row_y += 24

    # Totals
    totals_y = row_y + 18
    label_x = 400
    _text(page, label_x, totals_y, "Subtotal", size=8.8, color=MUTED)
    _right_text(page, columns["amount_right"], totals_y, data["subtotal"], size=8.8)

    for label, value in data["tax_lines"]:
        totals_y += 16
        _text(page, label_x, totals_y, label, size=8.8, color=MUTED)
        _right_text(page, columns["amount_right"], totals_y, value, size=8.8)

    totals_y += 12
    page.draw_rect(
        fitz.Rect(label_x - 10, totals_y - 2, WIDTH - MARGIN, totals_y + 24),
        color=None,
        fill=(0.937, 0.945, 1.0),
    )
    _text(page, label_x, totals_y + 14, "TOTAL", size=10, font="hebo", color=BRAND)
    _right_text(
        page, columns["amount_right"], totals_y + 14,
        f"{data['currency']} {data['total']}", size=11, font="hebo", color=BRAND,
    )

    # Notes / footer
    footer_y = HEIGHT - 118
    page.draw_line(fitz.Point(MARGIN, footer_y), fitz.Point(WIDTH - MARGIN, footer_y), color=LINE, width=0.8)
    _text(page, MARGIN, footer_y + 16, "NOTES", size=7.4, color=MUTED, font="hebo")
    _text(page, MARGIN, footer_y + 29, data["notes"], size=7.8, color=MUTED)
    _text(page, MARGIN, footer_y + 48, "REMITTANCE", size=7.4, color=MUTED, font="hebo")
    _text(page, MARGIN, footer_y + 61, data["bank"], size=7.8, color=MUTED)
    _right_text(page, WIDTH - MARGIN, HEIGHT - 28, "Synthetic sample — not a real invoice.", size=7, color=MUTED)

    return page


def build_digital(data: dict, path: Path) -> None:
    document = fitz.open()
    build_page(document, data)
    document.save(path, garbage=3, deflate=True)
    document.close()


def build_scanned(data: dict, path: Path) -> None:
    source = fitz.open()
    page = build_page(source, data)
    pixmap = page.get_pixmap(dpi=170, alpha=False)
    png = pixmap.tobytes("png")
    source.close()

    scanned = fitz.open()
    image_page = scanned.new_page(width=WIDTH, height=HEIGHT)
    image_page.insert_image(image_page.rect, stream=png)
    scanned.save(path, garbage=3, deflate=True)
    scanned.close()


def main() -> None:
    for data in INVOICES:
        path = HERE / data["filename"]
        if data["filename"].startswith("scanned_"):
            build_scanned(data, path)
        else:
            build_digital(data, path)
        print(f"wrote {path.name} ({path.stat().st_size / 1024:.0f} KB)")

    corrupt = HERE / "corrupt_invoice.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nthis is deliberately not a valid PDF\n%%EOF\n")
    print(f"wrote {corrupt.name} (invalid, for error-handling demos)")


if __name__ == "__main__":
    main()
