import io
import uuid

import qrcode
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from app.config import settings
from app.ticket_design import (
    build_master_background,
    WIDTH_IN, HEIGHT_IN, TEAR_OFF_IN,
    WIDTH_PX, HEIGHT_PX, SPLIT_X_PX,
)

WIDTH_PT = WIDTH_IN * inch
HEIGHT_PT = HEIGHT_IN * inch
SPLIT_X_PT = (WIDTH_IN - TEAR_OFF_IN) * inch


def make_qr_reader(data: str) -> ImageReader:
    img = qrcode.make(data, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def _build_background_reader(sponsors=None):
    bg = build_master_background(
        WIDTH_PX, HEIGHT_PX, SPLIT_X_PX,
        settings.event_name, settings.event_date_label, settings.event_venue,
        sponsors=sponsors,
    )
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return ImageReader(buf)


def draw_ticket_page(c: canvas.Canvas, bg_reader: ImageReader, short_code: str, qr_reader: ImageReader):
    c.drawImage(bg_reader, 0, 0, width=WIDTH_PT, height=HEIGHT_PT)

    panel_cx = SPLIT_X_PT + (WIDTH_PT - SPLIT_X_PT) / 2

    qr_size = 1.7 * inch
    qr_x = panel_cx - qr_size / 2
    qr_y = HEIGHT_PT - qr_size - 0.5 * inch
    c.drawImage(qr_reader, qr_x, qr_y, width=qr_size, height=qr_size)

    c.setFillColorRGB(0.1, 0.05, 0.05)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(panel_cx, qr_y - 0.35 * inch, short_code)

    c.setFont("Helvetica", 9)
    c.drawCentredString(panel_cx, qr_y - 0.55 * inch, "ADMIT ONE  •  SCAN TO ENTER")

    c.setFont("Helvetica-Oblique", 7.5)
    c.drawCentredString(panel_cx, 0.35 * inch, "This ticket becomes void once scanned at entry.")

    c.showPage()


def build_tickets(count: int, sponsors=None):
    """Generate `count` unique tickets on a single multi-page PDF.

    The decorative background (frame, logos, sponsors) is built exactly once
    and reused for every page — only the QR code and short code differ per
    ticket. Pass `sponsors` to use a different sponsor set than the default
    (e.g. for a later batch with a different sponsor lineup) without
    affecting any previously-generated batch. Returns (rows, combined_pdf_bytes).
    """
    bg_reader = _build_background_reader(sponsors=sponsors)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(WIDTH_PT, HEIGHT_PT))

    rows = []
    for seq in range(1, count + 1):
        ticket_id = str(uuid.uuid4())
        short_code = ticket_id.split("-")[0].upper()

        qr_reader = make_qr_reader(ticket_id)
        draw_ticket_page(c, bg_reader, short_code, qr_reader)

        rows.append({
            "_id": ticket_id,
            "ticket_id": ticket_id,
            "short_code": short_code,
            "status": "valid",
            "used_at": None,
            "scanned_by": None,
            "cancelled_at": None,
            "seq": seq,
        })

    c.save()
    buf.seek(0)
    return rows, buf.read()
