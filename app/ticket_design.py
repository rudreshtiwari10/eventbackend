"""
Builds the ONE shared decorative ticket background (frame, gradient, logos,
sponsors) that every ticket in a batch reuses. Only the QR code, short code,
and ticket-number text differ per ticket and are drawn later, per page.

This keeps 300 tickets sharing a single embedded background image instead of
re-embedding multi-megabyte artwork 300 times (see pdf_gen.py for why that
matters).
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ASSETS = os.path.join(os.path.dirname(__file__), "..", "assets")  # backend/assets/ — self-contained in this repo

FRAME_PATH = os.path.join(ASSETS, "Event Poster Frame (embroidered zigzag).png")
EVENT_LOGO_PATH = os.path.join(ASSETS, "Gayatri Maharas logo (transparent).png")
ORG_LOGO_PATH = os.path.join(ASSETS, "RT MediaTech Logo (event colors).png")
ORG_BADGE_PATH = os.path.join(ASSETS, "RT Badge Circle (cutout).png")

SPONSORS = [
    (os.path.join(ASSETS, "Shikshit Bharat .jpeg"), "Shikshit Bharat"),
    (os.path.join(ASSETS, "Srishti Beauty Parlour .jpeg"), "Srishti Beauty Parlour"),
    (os.path.join(ASSETS, "Star Birds Pre-School.jpeg"), "Star Birds Preschool"),
    (os.path.join(ASSETS, "Raj Education Hub.jpeg"), "Raj Education Hub"),
]

# Maroon-red to reddish-pink glow, per the reference mood board.
GLOW_INNER = (196, 46, 84)
GLOW_OUTER = (58, 10, 22)
CREAM = (250, 240, 222)
GOLD = (232, 184, 75)

# Single source of truth for ticket geometry — pdf_gen.py imports these so the
# high-res raster background (built here, in pixels) and the PDF page (built
# there, in points) can never drift out of sync with each other.
DPI = 300
WIDTH_IN = 11.0
HEIGHT_IN = 4.0
TEAR_OFF_IN = 2.4  # width of the white QR / tear-off panel, right side

WIDTH_PX = int(WIDTH_IN * DPI)
HEIGHT_PX = int(HEIGHT_IN * DPI)
SPLIT_X_PX = WIDTH_PX - int(TEAR_OFF_IN * DPI)


def _radial_gradient(w, h, center, inner, outer, inner_radius_frac=0.05, outer_radius_frac=0.95):
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = center
    max_r = np.hypot(max(cx, w - cx), max(cy, h - cy))
    dist = np.hypot(xx - cx, yy - cy) / max_r
    dist = np.clip((dist - inner_radius_frac) / (outer_radius_frac - inner_radius_frac), 0, 1)
    dist = dist[..., None]
    inner_arr = np.array(inner, dtype=np.float32)
    outer_arr = np.array(outer, dtype=np.float32)
    rgb = inner_arr * (1 - dist) + outer_arr * dist
    return rgb.astype(np.uint8)


def _add_sunburst(rgb_arr, center, ray_count=48, strength=26):
    h, w, _ = rgb_arr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = center
    angle = np.arctan2(yy - cy, xx - cx)
    ray = np.abs(np.sin(angle * ray_count / 2))
    ray = ray ** 6  # narrow, subtle rays rather than broad stripes
    boost = (ray * strength).astype(np.int16)[..., None]
    out = np.clip(rgb_arr.astype(np.int16) + boost, 0, 255).astype(np.uint8)
    return out


def _tile_strip(strip, scale, horizontal, length, thickness):
    """Scale a border strip uniformly (preserving its motif proportions) then
    tile it to fill `length`, instead of stretching one copy — stretching
    distorts the zigzag motif differently on each side depending on that
    side's length, which is what made the border look inconsistently thick."""
    sw = max(1, round(strip.width * scale))
    sh = max(1, round(strip.height * scale))
    scaled = strip.resize((sw, sh), Image.LANCZOS)

    if horizontal:
        out = Image.new("RGBA", (length, thickness), (0, 0, 0, 0))
        x = 0
        while x < length:
            out.alpha_composite(scaled, (x, 0))
            x += sw
    else:
        out = Image.new("RGBA", (thickness, length), (0, 0, 0, 0))
        y = 0
        while y < length:
            out.alpha_composite(scaled, (0, y))
            y += sh

    return out


def _nine_slice_frame(target_w, target_h, border_px):
    frame = Image.open(FRAME_PATH).convert("RGBA")
    fw, fh = frame.size
    src_border = 46  # measured: the opaque zigzag band is ~44-46px on the 1588x2246 source
    scale = border_px / src_border

    tl = frame.crop((0, 0, src_border, src_border))
    tr = frame.crop((fw - src_border, 0, fw, src_border))
    bl = frame.crop((0, fh - src_border, src_border, fh))
    br = frame.crop((fw - src_border, fh - src_border, fw, fh))
    top = frame.crop((src_border, 0, fw - src_border, src_border))
    bottom = frame.crop((src_border, fh - src_border, fw - src_border, fh))
    left = frame.crop((0, src_border, src_border, fh - src_border))
    right = frame.crop((fw - src_border, src_border, fw, fh - src_border))

    out = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    b = border_px

    out.alpha_composite(tl.resize((b, b)), (0, 0))
    out.alpha_composite(tr.resize((b, b)), (target_w - b, 0))
    out.alpha_composite(bl.resize((b, b)), (0, target_h - b))
    out.alpha_composite(br.resize((b, b)), (target_w - b, target_h - b))

    out.alpha_composite(_tile_strip(top, scale, True, target_w - 2 * b, b), (b, 0))
    out.alpha_composite(_tile_strip(bottom, scale, True, target_w - 2 * b, b), (b, target_h - b))
    out.alpha_composite(_tile_strip(left, scale, False, target_h - 2 * b, b), (0, b))
    out.alpha_composite(_tile_strip(right, scale, False, target_h - 2 * b, b), (target_w - b, b))

    return out


def _circular_logo(path, diameter, ring_color=GOLD, ring_width=6, backing=CREAM):
    im = Image.open(path).convert("RGBA")
    size = min(im.size)
    im = im.crop(((im.width - size) // 2, (im.height - size) // 2,
                  (im.width + size) // 2, (im.height + size) // 2))
    im = im.resize((diameter, diameter), Image.LANCZOS)

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)

    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    if backing:
        ImageDraw.Draw(out).ellipse((0, 0, diameter, diameter), fill=(*backing, 255))
    out.paste(im, (0, 0), mask)

    if ring_width:
        d = ImageDraw.Draw(out)
        d.ellipse((ring_width // 2, ring_width // 2, diameter - ring_width // 2, diameter - ring_width // 2),
                  outline=ring_color, width=ring_width)
    return out


def _font(size, bold=False, family="georgia"):
    families = {
        "georgia": ("Georgia Bold.ttf", "Georgia.ttf"),
        "arial": ("Arial Bold.ttf", "Arial.ttf"),
        "helvetica": ("Helvetica Neue Bold.ttf", "Helvetica Neue.ttf"),
    }
    bold_name, regular_name = families.get(family, families["georgia"])
    candidates = [
        f"/System/Library/Fonts/Supplemental/{bold_name if bold else regular_name}",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()


def _two_tone_line(draw, x, y, label, value, label_font, value_font, label_color, value_color):
    draw.text((x, y), label, font=label_font, fill=label_color)
    label_w = draw.textbbox((0, 0), label, font=label_font)[2]
    draw.text((x + label_w + 6, y), value, font=value_font, fill=value_color)


def _centered_text(draw, cx, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, font=font, fill=fill)
    return bbox[3] - bbox[1]


def _fit_font(draw, text, bold, max_width, start_size, min_size=12):
    size = start_size
    while size > min_size:
        font = _font(size, bold=bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(min_size, bold=bold)


def _wrap_to_width(draw, text, font, max_width):
    words = text.split()
    lines, line = [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _ribbon_metrics(draw, max_w, lines, font, point_frac, pad_x, pad_y, line_gap):
    line_heights = [draw.textbbox((0, 0), ln, font=font)[3] for ln in lines]
    pad_x = pad_x if pad_x is not None else int(max_w * 0.03)
    pad_y = pad_y if pad_y is not None else int(sum(line_heights) * 0.18)
    line_gap = line_gap if line_gap is not None else int(sum(line_heights) * 0.08 / max(1, len(lines)))

    text_block_w = max(draw.textbbox((0, 0), ln, font=font)[2] for ln in lines)
    banner_w = min(max_w, text_block_w + pad_x * 2 + int(max_w * point_frac) * 2)
    banner_h = sum(line_heights) + line_gap * (len(lines) - 1) + pad_y * 2
    return banner_w, banner_h, pad_x, pad_y, line_gap, line_heights


def _draw_ribbon_banner(bg, draw, cx, top_y, max_w, lines, font, text_color,
                         fill=CREAM, outline=GOLD, point_frac=0.09, pad_x=None, pad_y=None, line_gap=None):
    """An old-scroll/ribbon banner shape (hexagonal — tapered points on both
    ends) with centered text on it, used for the venue address."""
    banner_w, banner_h, pad_x, pad_y, line_gap, line_heights = _ribbon_metrics(
        draw, max_w, lines, font, point_frac, pad_x, pad_y, line_gap
    )
    point_w = int(banner_w * point_frac)

    x0 = cx - banner_w / 2
    x1 = cx + banner_w / 2
    y0 = top_y
    y1 = top_y + banner_h
    ymid = (y0 + y1) / 2

    polygon = [
        (x0 + point_w, y0), (x1 - point_w, y0), (x1, ymid), (x1 - point_w, y1),
        (x0 + point_w, y1), (x0, ymid),
    ]
    draw.polygon(polygon, fill=(*fill, 245), outline=(*outline, 255), width=4)
    # inner hairline for a bit of scroll-like detail
    inset = 7
    inner = [
        (x0 + point_w, y0 + inset), (x1 - point_w, y0 + inset), (x1 - inset, ymid),
        (x1 - point_w, y1 - inset), (x0 + point_w, y1 - inset), (x0 + inset, ymid),
    ]
    draw.line(inner + [inner[0]], fill=(*outline, 160), width=1)

    ty = y0 + pad_y
    for ln, lh in zip(lines, line_heights):
        _centered_text(draw, cx, ty, ln, font, text_color)
        ty += lh + line_gap

    return banner_h


def _draw_ribbon_banner_centered(bg, draw, cx, available_top, available_bottom, max_w, lines, font, text_color,
                                  point_frac=0.09):
    """Fits the ribbon banner into [available_top, available_bottom], shrinking
    the font if the venue text would otherwise overflow that gap, then centers
    it — so the banner and the sponsor row below it never collide."""
    available_h = max(1, available_bottom - available_top)
    size = font.size if hasattr(font, "size") else 28
    bold = False

    working_font = font
    _, banner_h, *_ = _ribbon_metrics(draw, max_w, lines, working_font, point_frac, None, None, None)
    while banner_h > available_h and size > 14:
        size -= 2
        working_font = _font(size, bold=bold)
        _, banner_h, *_ = _ribbon_metrics(draw, max_w, lines, working_font, point_frac, None, None, None)

    top_y = available_top + max(0, (available_h - banner_h) / 2)
    _draw_ribbon_banner(bg, draw, cx, top_y, max_w, lines, working_font, text_color, point_frac=point_frac)


def build_master_background(width_px, height_px, split_x_px, event_name, event_date_label, event_venue,
                             sponsors=None):
    """`sponsors` defaults to the module-level SPONSORS list (the original
    400-ticket batch's set). Pass an explicit list to use a different set
    for a different batch — this never mutates the shared default, so the
    original batch's generation stays exactly reproducible if ever re-run."""
    sponsors = SPONSORS if sponsors is None else sponsors
    # Based on height, not width — a ticket that gets longer shouldn't grow a
    # thicker border, and this keeps it thin/consistent as requested.
    border_px = max(24, round(height_px * 0.045))

    # The gradient/sunburst only need to fill the main stub — the tear-off
    # panel is drawn flat white further down, so painting the glow under it
    # too would just be wasted work.
    center = (split_x_px * 0.5, height_px * 0.5)
    rgb = _radial_gradient(split_x_px, height_px, center, GLOW_INNER, GLOW_OUTER)
    rgb = _add_sunburst(rgb, center)
    stub_bg = Image.fromarray(rgb, mode="RGB").convert("RGBA")

    bg = Image.new("RGBA", (width_px, height_px), (252, 249, 244, 255))
    bg.alpha_composite(stub_bg, (0, 0))

    # Decorative frame wraps ONLY the main stub — the QR / tear-off panel
    # stays plain white with no border, so it photographs and scans cleanly.
    frame = _nine_slice_frame(split_x_px, height_px, border_px)
    bg.alpha_composite(frame)

    draw = ImageDraw.Draw(bg)

    # Dashed tear line marking where the stub is torn from the QR panel.
    dash_len, gap_len = int(height_px * 0.018), int(height_px * 0.012)
    y = 0
    while y < height_px:
        draw.line((split_x_px, y, split_x_px, min(y + dash_len, height_px)), fill=(150, 40, 60, 255), width=3)
        y += dash_len + gap_len

    stub_left = border_px + int(width_px * 0.03)
    stub_right = split_x_px - border_px - int(width_px * 0.025)
    stub_w = stub_right - stub_left
    content_bottom = height_px - border_px - int(height_px * 0.06)

    # Organizer badge (small circular seal), top-right of the main stub.
    # Positioned first so the title text width can be reserved around it.
    badge_d = int(height_px * 0.16)
    badge_pad = int(width_px * 0.015)
    badge_x = stub_right - badge_d
    badge_y = border_px + int(height_px * 0.02)
    badge = Image.open(ORG_BADGE_PATH).convert("RGBA").resize((badge_d, badge_d), Image.LANCZOS)

    # Event logo, top of the main stub.
    event_logo = Image.open(EVENT_LOGO_PATH).convert("RGBA")
    logo_h = int(height_px * 0.52)
    logo_w = int(event_logo.width * logo_h / event_logo.height)
    logo_w = min(logo_w, stub_w)
    logo_h = int(event_logo.height * logo_w / event_logo.width)
    event_logo = event_logo.resize((logo_w, logo_h), Image.LANCZOS)
    logo_x = stub_left
    logo_y = border_px + int(height_px * 0.015)
    bg.alpha_composite(event_logo, (logo_x, logo_y))

    text_x = logo_x + logo_w + int(width_px * 0.015)
    text_top = logo_y + int(height_px * 0.02)

    theme_font = _font(int(height_px * 0.05), bold=True)
    subtitle_font = _font(int(height_px * 0.034), bold=True)
    date_font = _font(int(height_px * 0.045))
    timing_font = _font(int(height_px * 0.032))
    venue_font = _font(int(height_px * 0.038))
    small_font = _font(int(height_px * 0.03))

    text_max_w = stub_right - text_x
    title_max_w = badge_x - badge_pad - text_x
    title_font = _fit_font(draw, event_name, True, title_max_w, int(height_px * 0.088), min_size=int(height_px * 0.05))

    draw.text((text_x, text_top), event_name, font=title_font, fill=GOLD)
    title_h = draw.textbbox((0, 0), event_name, font=title_font)[3]

    bg.alpha_composite(badge, (badge_x, badge_y))

    # "GARBA NIGHT" theme line, then "ENTRY PASS" subtitle, both letter-spaced.
    # Kept tight — every extra line here eats into the venue banner's space below.
    y = text_top + title_h + int(height_px * 0.01)
    garba_spaced = " ".join("GARBA NIGHT")
    garba_font = _fit_font(draw, garba_spaced, True, title_max_w, int(height_px * 0.05), min_size=int(height_px * 0.032))
    draw.text((text_x, y), garba_spaced, font=garba_font, fill=GOLD)
    y += draw.textbbox((0, 0), garba_spaced, font=garba_font)[3] + int(height_px * 0.006)

    entry_spaced = " ".join("ENTRY PASS")
    draw.text((text_x, y), entry_spaced, font=subtitle_font, fill=CREAM)
    y += draw.textbbox((0, 0), entry_spaced, font=subtitle_font)[3] + int(height_px * 0.016)

    draw.text((text_x, y), event_date_label, font=date_font, fill=CREAM)
    y += draw.textbbox((0, 0), event_date_label, font=date_font)[3] + int(height_px * 0.01)

    timing_line = "Gates 5:30–6:30 PM   •   Event 6:30–10:30 PM"
    timing_line_font = _fit_font(draw, timing_line, False, text_max_w, int(height_px * 0.032), min_size=16)
    draw.text((text_x, y), timing_line, font=timing_line_font, fill=CREAM)
    y += draw.textbbox((0, 0), timing_line, font=timing_line_font)[3] + int(height_px * 0.012)

    draw.text((text_x, y), "General Admission — Valid for one entry only", font=small_font, fill=GOLD)
    header_bottom = y + draw.textbbox((0, 0), "General Admission", font=small_font)[3] + int(height_px * 0.02)

    # --- Organizer logo + contact block, bottom-anchored to content_bottom,
    # independent of the sponsor row's own height so neither can push into
    # the other regardless of how tall either block ends up being. ---
    org_col_w = int(width_px * 0.22)

    org_logo = Image.open(ORG_LOGO_PATH).convert("RGBA")
    org_h = int(height_px * 0.15)
    org_w = int(org_logo.width * org_h / org_logo.height)
    if org_w > org_col_w:
        org_w = org_col_w
        org_h = int(org_logo.height * org_w / org_logo.width)
    org_logo = org_logo.resize((org_w, org_h), Image.LANCZOS)

    # Two-tone contact block (bold gold label + cream value, sans-serif —
    # deliberately distinct from the serif body copy elsewhere) sized to fit
    # the longest line, then reused for both so label/value line up in style.
    value_font = _fit_font(draw, "Email: rtmediatech1508@gmail.com", False, org_col_w * 0.96,
                            int(height_px * 0.038), min_size=16)
    contact_size = value_font.size if hasattr(value_font, "size") else 20
    value_font = _font(contact_size, bold=False, family="helvetica")
    label_font = _font(contact_size, bold=True, family="helvetica")

    mobile_label, mobile_value = "Mobile: ", "9214943720"
    email_label, email_value = "Email: ", "rtmediatech1508@gmail.com"
    contact_gap = int(height_px * 0.006)
    line_h = draw.textbbox((0, 0), "Ag", font=value_font)[3]

    org_block_h = org_h + int(height_px * 0.008) + line_h + contact_gap + line_h
    org_x = stub_left
    org_y = content_bottom - org_block_h

    bg.alpha_composite(org_logo, (org_x, org_y))
    cy = org_y + org_h + int(height_px * 0.008)
    _two_tone_line(draw, org_x, cy, mobile_label, mobile_value, label_font, value_font, GOLD, CREAM)
    cy += line_h + contact_gap
    _two_tone_line(draw, org_x, cy, email_label, email_value, label_font, value_font, GOLD, CREAM)

    sponsor_d = int(height_px * 0.17)
    label_font_base = int(height_px * 0.036)
    label_gap = int(height_px * 0.008)
    label_probe_font = _font(label_font_base)
    label_line_h = draw.textbbox((0, 0), "Ag", font=label_probe_font)[3]
    label_block_h = 2 * label_line_h + label_gap + int(height_px * 0.012)  # reserve worst case: 2-line name

    sponsor_y = content_bottom - sponsor_d - label_block_h

    sponsors_start_x = org_x + org_col_w + int(width_px * 0.02)
    sponsors_area_w = stub_right - sponsors_start_x
    n = len(sponsors)
    slot_w = sponsors_area_w // n if n else 0

    label_prefix_font = _font(int(height_px * 0.034), bold=True)
    label_prefix_h = draw.textbbox((0, 0), "EVENT SPONSORS", font=label_prefix_font)[3]
    prefix_gap = int(height_px * 0.02)
    sponsor_section_top = sponsor_y - prefix_gap - label_prefix_h

    _centered_text(draw, sponsors_start_x + sponsors_area_w / 2, sponsor_section_top,
                    "EVENT SPONSORS", label_prefix_font, GOLD)

    label_max_w = slot_w - int(width_px * 0.01)
    for i, (path, name) in enumerate(sponsors):
        slot_cx = sponsors_start_x + i * slot_w + slot_w / 2
        circ = _circular_logo(path, sponsor_d)
        bg.alpha_composite(circ, (int(slot_cx - sponsor_d / 2), sponsor_y))

        label_font = _fit_font(draw, name, False, label_max_w, label_font_base, min_size=20)
        label_lines = _wrap_to_width(draw, name, label_font, label_max_w) if \
            draw.textbbox((0, 0), name, font=label_font)[2] > label_max_w else [name]

        label_y = sponsor_y + sponsor_d + int(height_px * 0.014)
        for line in label_lines[:2]:
            lh = _centered_text(draw, slot_cx, label_y, line, label_font, CREAM)
            label_y += lh + label_gap

    # --- Venue address, centered to the right of the event logo (same column
    # as the header text above it) on an old-scroll ribbon banner. Centering
    # it across the full stub width would put its left point under the logo. ---
    banner_region_left = text_x
    banner_region_right = stub_right
    banner_cx = (banner_region_left + banner_region_right) / 2
    banner_max_w = (banner_region_right - banner_region_left) * 0.94
    venue_lines = _wrap_to_width(draw, event_venue, venue_font, banner_max_w - int(banner_max_w * 0.09) * 2 - 40)

    available_top = header_bottom + int(height_px * 0.02)
    available_bottom = sponsor_section_top - int(height_px * 0.02)
    _draw_ribbon_banner_centered(bg, draw, banner_cx, available_top, available_bottom,
                                  banner_max_w, venue_lines, venue_font, GLOW_OUTER)

    return bg
