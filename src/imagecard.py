"""Render on-brand post cards as PNGs for the current account (pure Pillow).

Uses the current account's logo (with the packaged one as a fallback) and rotates across several distinct, coherent
layouts so consecutive posts look varied and deliberately designed rather than
template-stamped.

Public API:
    render_card(headline, subtext, out_path, kicker=..., highlight=..., style=None)
        -> (Path, style_name)
    STYLE_NAMES  -> list[str] of available style names

A style is chosen at random when `style` is None.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

import tenants

_LIST_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
_LABEL_RE = re.compile(r"^\s*([A-Za-z]{3,12}):\s+(\S.*)")

# Canvas: Facebook's recommended shared-image size.
W, H = 1200, 630

# Palette. Navy background + the logo's green and blue as accents.
BG_TOP = (10, 26, 47)        # #0a1a2f
BG_BOTTOM = (10, 14, 20)     # #0a0e14
GREEN = (52, 174, 76)        # logo green
GREEN_SOFT = (46, 204, 113)
BLUE = (43, 108, 196)        # logo "Sky" blue
WHITE = (245, 248, 252)      # #f5f8fc
MUTED = (159, 179, 200)      # #9fb3c8

PAD_X = 80

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_FONT_DIR = _ASSETS / "fonts"
# Packaged asset (SkySystems logo) -- ONLY ever used for the SkySystems tenant,
# never as a cross-brand fallback. Accounts without a logo get a wordmark of
# their OWN name so one client's branding never appears on another's card.
_LOGO_FULL = _ASSETS / "logo_full.png"
_LOGO_MARK = _ASSETS / "logo_mark.png"


def _logo_image() -> Image.Image:
    """The current account's logo as an RGBA image. If the account has no logo
    file, render a clean wordmark of ITS OWN name (never the packaged Sky logo).
    """
    p = tenants.logo_full()
    if p.exists():
        return Image.open(p).convert("RGBA")
    return _wordmark_image()


def _logo_mark_image() -> Image.Image:
    p = tenants.logo_mark()
    if p.exists():
        return Image.open(p).convert("RGBA")
    return _wordmark_image()


def _wordmark_image() -> Image.Image:
    """A text logo of the account name in its design font + brand accent,
    lightened so it reads on the navy footer. Transparent background."""
    try:
        name = (tenants.account().get("name") or "").strip()
    except Exception:
        name = ""
    name = name or "Brand"
    accent = tenants.accent_colors()[0]
    color = _lighten(accent, 0.30)
    file, weight = _design()["head"]
    font = _face(file, 84, weight)
    tmp = Image.new("RGBA", (10, 10))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), name, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 14
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), name, font=font, fill=color)
    return img


def _domain() -> str:
    """The current account's bare domain for card footers."""
    return tenants.domain()


def _style() -> str:
    """Visual style for the current account: 'bright' (light, clean, for most
    consumer/service brands) or 'dark' (navy premium, for tech/security brands)."""
    try:
        return tenants.style()
    except Exception:
        return "dark"


def _accent_pick(rng) -> tuple[int, int, int]:
    """Pick one of the current account's two accent colors."""
    return rng.choice(tenants.accent_colors())


# ---------------------------------------------------------------------------
# Style definitions
# ---------------------------------------------------------------------------
STYLES: dict[str, dict] = {
    "grid-left": dict(
        texture="grid", glow=(0.74, 0.16, GREEN), layout="left",
        highlight=GREEN_SOFT, watermark=False,
    ),
    "mark-right": dict(
        texture="plain", glow=(0.78, 0.22, BLUE), layout="left",
        highlight=GREEN_SOFT, watermark=True,
    ),
    "centered": dict(
        texture="grid-faint", glow=(0.5, 0.10, GREEN), layout="center",
        highlight=BLUE, watermark=False,
    ),
    "dots-left": dict(
        texture="dots", glow=(0.80, 0.82, BLUE), layout="left",
        highlight=GREEN_SOFT, watermark=False,
    ),
    "diagonal-left": dict(
        texture="diagonal", glow=(0.18, 0.85, GREEN), layout="left",
        highlight=BLUE, watermark=False,
    ),
}
STYLE_NAMES = list(STYLES.keys())


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(_FONT_DIR / name), size)
    if name.startswith("NunitoSans"):
        # Variable font default weight is 200 (too thin); axis order is
        # [Weight, Width, Optical size, YTLC]. Pin weight 400.
        try:
            f.set_variation_by_axes([400, 100, 12, 500])
        except Exception:
            pass
    return f


# --- font registry: how to weight each (variable) family ----------------------
# For variable fonts PIL needs the FULL axis vector, in the font's axis order.
_VAR_AXES = {
    "Quicksand.ttf": lambda w, size: [w],
    "SpaceGrotesk.ttf": lambda w, size: [w],
    "Baloo2.ttf": lambda w, size: [w],
    # Fraunces axis order: Optical Size, Weight, Softness, Wonky.
    "Fraunces.ttf": lambda w, size: [min(144, max(9, size)), w, 0, 0],
    # NunitoSans axis order: Weight, Width, Optical size, YTLC.
    "NunitoSans.ttf": lambda w, size: [w, 100, min(72, max(6, size)), 500],
}


def _face(file: str, size: int, weight: int | None = 700) -> ImageFont.FreeTypeFont:
    """Load any bundled face at a size/weight, handling variable-font axes."""
    f = ImageFont.truetype(str(_FONT_DIR / file), size)
    setter = _VAR_AXES.get(file)
    if setter and weight:
        try:
            f.set_variation_by_axes(setter(weight, size))
        except Exception:
            pass
    return f


def _nunito(size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    return _face("NunitoSans.ttf", size, weight)


# --- per-account design systems ----------------------------------------------
# Each account renders in ONE coherent identity so no two clients look alike.
# Dark/tech accounts use 'tech-condensed'; bright accounts are deterministically
# assigned one of the bright systems by slug (stable per client).
_DESIGNS = {
    "tech-condensed": dict(mood="dark",
        head=("Rajdhani-Bold.ttf", None), kfont=("Rajdhani-SemiBold.ttf", None),
        case="upper", kicker="tab", align="left", rule="bar", motif="none"),
    "soft-rounded": dict(mood="bright",
        head=("Quicksand.ttf", 700), kfont=("Quicksand.ttf", 600),
        case="title", kicker="pill", align="left", rule="bar", motif="blob"),
    "friendly-round": dict(mood="bright",
        head=("Baloo2.ttf", 700), kfont=("Baloo2.ttf", 600),
        case="title", kicker="pill", align="center", rule="dot", motif="arc"),
    "elegant-serif": dict(mood="bright",
        head=("Fraunces.ttf", 600), kfont=("NunitoSans.ttf", 650),
        case="title", kicker="plain", align="left", rule="long", motif="none"),
    "bold-impact": dict(mood="bright",
        head=("Anton.ttf", None), kfont=("SpaceGrotesk.ttf", 600),
        case="upper", kicker="outline", align="left", rule="none", motif="stripe"),
    "modern-grotesk": dict(mood="bright",
        head=("SpaceGrotesk.ttf", 700), kfont=("SpaceGrotesk.ttf", 500),
        case="title", kicker="underline", align="left", rule="none", motif="corner"),
}
_BRIGHT_DESIGNS = ["soft-rounded", "friendly-round", "elegant-serif",
                   "bold-impact", "modern-grotesk"]

# Exposed for the settings UI (id -> human label).
DESIGN_LABELS = {
    "soft-rounded": "Soft & rounded (Quicksand)",
    "friendly-round": "Friendly & centered (Baloo)",
    "elegant-serif": "Elegant serif (Fraunces)",
    "bold-impact": "Bold impact (Anton)",
    "modern-grotesk": "Modern grotesk (Space Grotesk)",
    "tech-condensed": "Tech condensed (Rajdhani)",
}


def current_design_id() -> str:
    """The design id the current account resolves to (auto or explicit)."""
    d = _design()
    for k, v in _DESIGNS.items():
        if v is d:
            return k
    return "soft-rounded"


def _design() -> dict:
    """The current account's design system (explicit override, else auto)."""
    try:
        acct = tenants.account()
    except Exception:
        acct = {}
    chosen = acct.get("design")
    if chosen in _DESIGNS:
        return _DESIGNS[chosen]
    if _style() == "dark":
        return _DESIGNS["tech-condensed"]
    slug = tenants.current()
    idx = sum(ord(c) for c in slug) % len(_BRIGHT_DESIGNS)
    return _DESIGNS[_BRIGHT_DESIGNS[idx]]


def _head_font(size: int) -> ImageFont.FreeTypeFont:
    file, weight = _design()["head"]
    return _face(file, size, weight)


def _head_text(text: str) -> str:
    t = (text or "").strip()
    case = _design()["case"]
    if case == "upper":
        return t.upper()
    if case == "title":
        return t
    return t


# ---------------------------------------------------------------------------
# Background, texture, glow
# ---------------------------------------------------------------------------
def _vertical_gradient() -> Image.Image:
    img = Image.new("RGB", (W, H), BG_BOTTOM)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = (y / (H - 1)) ** 0.85
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return img.convert("RGBA")


def _radial_mask(focus=(0.72, 0.18), reach=0.62) -> Image.Image:
    """A soft mask: bright at `focus`, fading to dark at the edges."""
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    cx, cy, rad = int(W * focus[0]), int(H * focus[1]), int(W * reach)
    for i in range(rad, 0, -3):
        a = int(255 * (1 - i / rad))
        md.ellipse([cx - i, cy - i, cx + i, cy + i], fill=a)
    return mask


def _texture(base: Image.Image, kind: str, focus) -> None:
    if kind == "plain":
        return
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    faint = kind == "grid-faint"
    alpha = 10 if faint else 18
    col = (*GREEN, alpha)
    step = 48

    if kind in ("grid", "grid-faint"):
        for x in range(0, W, step):
            od.line([(x, 0), (x, H)], fill=col, width=1)
        for y in range(0, H, step):
            od.line([(0, y), (W, y)], fill=col, width=1)
    elif kind == "dots":
        r = 2
        for x in range(0, W, step):
            for y in range(0, H, step):
                od.ellipse([x - r, y - r, x + r, y + r], fill=(*GREEN, 26))
    elif kind == "diagonal":
        gap = 56
        for x in range(-H, W, gap):
            od.line([(x, 0), (x + H, H)], fill=col, width=1)

    mask = _radial_mask(focus=focus[:2], reach=0.6)
    faded = Image.composite(overlay, Image.new("RGBA", (W, H), (0, 0, 0, 0)), mask)
    base.alpha_composite(faded)


def _glow(base: Image.Image, fx: float, fy: float, color) -> None:
    size = 600
    grad = ImageOps.invert(Image.radial_gradient("L")).resize((size, size))
    alpha = grad.point(lambda p: int(p * 0.28))
    layer = Image.new("RGBA", (size, size), (*color, 255))
    glow = Image.composite(layer, Image.new("RGBA", (size, size), (0, 0, 0, 0)), alpha)
    x = int(fx * W - size / 2)
    y = int(fy * H - size / 2)
    base.alpha_composite(glow, (x, y))


def _watermark(base: Image.Image) -> None:
    """Large, faint icon mark bleeding off the right edge."""
    mark = _logo_mark_image()
    target_h = 520
    scale = target_h / mark.height
    mark = mark.resize((int(mark.width * scale), target_h))
    # Knock the opacity way down.
    a = mark.split()[3].point(lambda p: int(p * 0.10))
    mark.putalpha(a)
    base.alpha_composite(mark, (W - mark.width + 120, (H - mark.height) // 2 - 30))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _draw_tracked(draw, xy, text: str, font, fill, tracking: int) -> float:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x - tracking


def _tracked_width(draw, text: str, font, tracking: int) -> float:
    return sum(draw.textlength(ch, font=font) + tracking for ch in text) - tracking


def _place_logo_full(base: Image.Image, x: int, y: int, height: int) -> int:
    logo = _logo_image()
    scale = height / logo.height
    logo = logo.resize((int(logo.width * scale), height))
    base.alpha_composite(logo, (x, y))
    return logo.width


def _logo_is_opaque() -> bool:
    """True if the account logo has a baked-in (non-transparent) background, so
    it would render as an ugly rectangle on a photo/dark footer."""
    try:
        a = _logo_image().split()[3]
        return a.getextrema()[0] > 210      # min alpha high -> no transparency
    except Exception:
        return False


def _image_luma(im: Image.Image) -> float:
    """Average luminance (0-255) of an image's visible (opaque) pixels."""
    small = im.convert("RGBA").resize((28, 28))
    px = small.load()
    tot, n = 0.0, 0
    for j in range(28):
        for i in range(28):
            r, g, b, a = px[i, j]
            if a > 40:
                tot += 0.299 * r + 0.587 * g + 0.114 * b
                n += 1
    return (tot / n) if n else 128.0


def _has_logo_file() -> bool:
    return tenants.logo_full().exists()


def _recolor_logo(logo: Image.Image, target) -> Image.Image:
    """Recolor a (transparent) logo to a solid target color, keeping its shape
    via the alpha channel. Turns a dark logo white on dark backgrounds, etc."""
    out = Image.new("RGBA", logo.size, (target[0], target[1], target[2], 0))
    out.putalpha(logo.split()[3])
    return out


def _place_logo_footer(base: Image.Image, x: int, y: int, height: int,
                       on_dark: bool = True) -> int:
    """Place the brand logo so it ALWAYS reads on its background.

    - No logo file -> draw the account name (wordmark) in a contrasting color.
    - Transparent logo with low contrast -> recolor it to contrast (white on
      dark, dark on light), so it changes with the background, no box.
    - Opaque logo (baked-in background) -> can't recolor a photo-style logo, so
      set it on a contrasting rounded chip instead.
    """
    if not _has_logo_file():
        return _draw_wordmark_footer(base, x, y, height, on_dark)

    logo = _logo_image()
    scale = height / logo.height
    logo = logo.resize((max(1, int(logo.width * scale)), height))
    opaque = logo.split()[3].getextrema()[0] > 210
    bg_l = 26 if on_dark else 242
    contrast = abs(_image_luma(logo) - bg_l)

    if opaque:
        pad = max(12, height // 4)
        chip_w, chip_h = logo.width + pad * 2, logo.height + pad * 2
        chip_fill = (255, 255, 255, 255) if on_dark else (13, 24, 36, 255)
        chip = Image.new("RGBA", (chip_w, chip_h), (0, 0, 0, 0))
        ImageDraw.Draw(chip).rounded_rectangle(
            [0, 0, chip_w - 1, chip_h - 1], radius=max(12, height // 3),
            fill=chip_fill)
        chip.alpha_composite(logo, (pad, pad))
        base.alpha_composite(chip, (x, y - pad))
        return chip_w

    if contrast < 95:
        target = (247, 250, 253) if on_dark else (17, 27, 39)
        logo = _recolor_logo(logo, target)
    base.alpha_composite(logo, (x, y))
    return logo.width


def _draw_wordmark_footer(base: Image.Image, x: int, y: int, height: int,
                          on_dark: bool) -> int:
    """Draw the account name as a wordmark in a contrasting brand color."""
    draw = ImageDraw.Draw(base)
    name = ((tenants.account().get("name") or "").strip()) or "Brand"
    accent = tenants.accent_colors()[0]
    color = _lighten(accent, 0.55) if on_dark else tuple(int(c * 0.62) for c in accent)
    file, weight = _design()["head"]
    font = _face(file, int(height * 0.92), weight)
    bbox = draw.textbbox((0, 0), name, font=font)
    draw.text((x, y - bbox[1]), name, font=font, fill=color)
    return int(draw.textlength(name, font=font))


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------
def render_card(
    headline: str,
    subtext: str,
    out_path: str | Path,
    kicker: str = "Austin, Texas",
    highlight: str = "",
    style: str | None = None,
    seed: int | None = None,
) -> tuple[Path, str]:
    rng = random.Random(seed)
    if style is None:
        style = rng.choice(STYLE_NAMES)
    cfg = STYLES[style]

    img = _vertical_gradient()
    _glow(img, *cfg["glow"])
    _texture(img, cfg["texture"], cfg["glow"])
    if cfg["watermark"]:
        _watermark(img)
    draw = ImageDraw.Draw(img)

    f_kicker = _font("Rajdhani-SemiBold.ttf", 30)
    f_head = _font("Rajdhani-Bold.ttf", 76)
    f_sub = _font("NunitoSans.ttf", 30)
    f_domain = _font("Rajdhani-SemiBold.ttf", 28)

    centered = cfg["layout"] == "center"
    max_w = W - 2 * PAD_X
    head_lines = _wrap(draw, headline.upper(), f_head, max_w)
    sub_lines = _wrap(draw, subtext, f_sub, max_w - (0 if centered else 120))

    kicker_h, head_lh, sub_lh = 56, 84, 42
    group_h = kicker_h + len(head_lines) * head_lh + 24 + len(sub_lines) * sub_lh
    top = max(92, (H - 150 - group_h) // 2)

    hl_words = (
        {w.strip(".,!?:;").lower() for w in highlight.split()} if highlight else set()
    )

    y = top
    # --- kicker ---
    if centered:
        kw = _tracked_width(draw, kicker.upper(), f_kicker, 5)
        kx = (W - kw) // 2
        draw.rectangle([(W // 2 - 23, y - 14), (W // 2 + 23, y - 10)], fill=GREEN)
        _draw_tracked(draw, (kx, y), kicker.upper(), f_kicker, GREEN, 5)
    else:
        draw.rectangle([PAD_X, y + 12, PAD_X + 46, y + 16], fill=GREEN)
        _draw_tracked(draw, (PAD_X + 64, y), kicker.upper(), f_kicker, GREEN, 5)
    y += kicker_h

    # --- headline (optional green/blue highlight words) ---
    for line in head_lines:
        line_w = draw.textlength(line, font=f_head)
        x = (W - line_w) // 2 if centered else PAD_X
        for word in line.split(" "):
            clean = word.strip(".,!?:;").lower()
            color = cfg["highlight"] if clean and clean in hl_words else WHITE
            draw.text((x, y), word, font=f_head, fill=color)
            x += draw.textlength(word + " ", font=f_head)
        y += head_lh

    # --- subtext ---
    y += 24
    for line in sub_lines:
        line_w = draw.textlength(line, font=f_sub)
        x = (W - line_w) // 2 if centered else PAD_X
        draw.text((x, y), line, font=f_sub, fill=MUTED)
        y += sub_lh

    # --- footer: real logo + domain ---
    logo_h = 64
    fy = H - logo_h - 40
    if centered:
        # Center the logo at the bottom; no domain (keeps it clean).
        logo = _logo_image()
        lw = int(logo.width * (logo_h / logo.height))
        _place_logo_full(img, (W - lw) // 2, fy, logo_h)
    else:
        _place_logo_full(img, PAD_X, fy, logo_h)
        domain = _domain()
        dom_w = draw.textlength(domain, font=f_domain)
        draw.text(
            (W - PAD_X - dom_w, fy + logo_h // 2 - 16),
            domain,
            font=f_domain,
            fill=MUTED,
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return out_path, style


PHOTO_VARIANTS = ["side", "bottom"]


def _photo_overlay(variant: str) -> Image.Image:
    """Navy gradient overlay that keeps text readable on any photo.

    'side'   -> dark on the left (text left), photo shows on the right.
    'bottom' -> photo shows up top, darkens toward the bottom (text along base).
    """
    navy = (8, 13, 24)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if variant == "bottom":
        for y in range(H):
            t = (y / (H - 1)) ** 1.4          # weight darkness toward the bottom
            od.line([(0, y), (W, y)], fill=(*navy, int(30 + (238 - 30) * t)))
    else:  # side
        for x in range(W):
            t = x / (W - 1)
            od.line([(x, 0), (x, H)], fill=(*navy, int(240 - (240 - 70) * t)))
        band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        for y in range(H - 210, H):
            t = (y - (H - 210)) / 210
            bd.line([(0, y), (W, y)], fill=(*navy, int(170 * t)))
        overlay = Image.alpha_composite(overlay, band)
    return overlay


def render_photo_card(
    headline: str,
    subtext: str,
    photo_path: str | Path,
    out_path: str | Path,
    kicker: str = "Austin, Texas",
    highlight: str = "",
    accent=None,
    variant: str | None = None,
    seed: int | None = None,
) -> tuple[Path, str]:
    """Render a card using a topic photo as the backdrop + brand overlay.

    `variant` (side|bottom) and `accent` (green|blue) are randomized when not
    given, so consecutive photo posts do not look identical. Returns
    (path, variant_label).
    """
    rng = random.Random(seed)
    if variant is None:
        variant = rng.choice(PHOTO_VARIANTS)
    if accent is None:
        accent = _accent_pick(rng)

    photo = Image.open(photo_path).convert("RGB")
    photo = ImageOps.fit(photo, (W, H), method=Image.LANCZOS)  # cover-crop
    img = photo.convert("RGBA")
    img.alpha_composite(_photo_overlay(variant))
    if variant == "bottom":
        _glow(img, 0.30, 0.92, accent)
    else:
        _glow(img, 0.16, 0.5, accent)
    draw = ImageDraw.Draw(img)

    f_kicker = _font("Rajdhani-SemiBold.ttf", 30)
    f_head = _font("Rajdhani-Bold.ttf", 76)
    f_sub = _font("NunitoSans.ttf", 30)
    f_domain = _font("Rajdhani-SemiBold.ttf", 28)

    max_w = W - 2 * PAD_X
    head_lines = _wrap(draw, headline.upper(), f_head, max_w)
    sub_lines = _wrap(draw, subtext, f_sub, max_w - (120 if variant == "side" else 0))

    kicker_h, head_lh, sub_lh = 56, 84, 42
    group_h = kicker_h + len(head_lines) * head_lh + 24 + len(sub_lines) * sub_lh
    logo_h = 64
    fy = H - logo_h - 40

    if variant == "bottom":
        # Anchor the text block just above the footer.
        y = fy - 34 - group_h
    else:
        y = max(92, (H - 150 - group_h) // 2)

    draw.rectangle([PAD_X, y + 12, PAD_X + 46, y + 16], fill=accent)
    _draw_tracked(draw, (PAD_X + 64, y), kicker.upper(), f_kicker, accent, 5)
    y += kicker_h

    hl_words = (
        {w.strip(".,!?:;").lower() for w in highlight.split()} if highlight else set()
    )
    for line in head_lines:
        x = PAD_X
        for word in line.split(" "):
            clean = word.strip(".,!?:;").lower()
            color = accent if clean and clean in hl_words else WHITE
            draw.text((x, y), word, font=f_head, fill=color)
            x += draw.textlength(word + " ", font=f_head)
        y += head_lh

    y += 24
    light = (216, 226, 238)  # brighter than MUTED for readability on photos
    for line in sub_lines:
        draw.text((PAD_X, y), line, font=f_sub, fill=light)
        y += sub_lh

    _place_logo_full(img, PAD_X, fy, logo_h)
    domain = _domain()
    dom_w = draw.textlength(domain, font=f_domain)
    draw.text((W - PAD_X - dom_w, fy + logo_h // 2 - 16), domain, font=f_domain, fill=light)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return out_path, f"photo-{variant}"


def _clean_body_for_image(post_text: str) -> str:
    """Drop pure-hashtag lines so the on-image copy stays clean."""
    kept = [ln for ln in post_text.splitlines() if not ln.strip().startswith("#")]
    return "\n".join(kept).strip()


_LW, _LH = 1080, 1080


def _center_start(block_h, top=140, bottom=175):
    """Vertical start y so a content block of height block_h sits centered
    between top padding and the footer zone."""
    return max(top, (_LH - bottom - block_h) // 2)


def _landscape_overlay() -> Image.Image:
    """Navy gradient: dark on the left (text side), photo shows on the right,
    with a darker band along the bottom for the logo row."""
    navy = (8, 13, 24)
    ov = Image.new("RGBA", (_LW, _LH), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for x in range(_LW):
        t = (x / (_LW - 1)) ** 0.9
        od.line([(x, 0), (x, _LH)], fill=(*navy, int(245 - (245 - 55) * t)))
    band = Image.new("RGBA", (_LW, _LH), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for y in range(_LH - 150, _LH):
        t = (y - (_LH - 150)) / 150
        bd.line([(0, y), (_LW, y)], fill=(*navy, int(150 * t)))
    return Image.alpha_composite(ov, band)


LANDSCAPE_LAYOUTS = ["overlay", "split"]


def _brand_tone(img: Image.Image, accent) -> Image.Image:
    """Grade a photo toward the brand palette so stock shots look art-directed
    and cohesive rather than like a random stock image. 'bright' accounts get a
    clean, airy, natural grade; 'dark' accounts get the navy-premium grade."""
    if _style() == "bright":
        # Keep the photo vivid, bright and real: punch up color/brightness and
        # apply only a whisper of brand wash for cohesion (no muddy grey).
        base = img.convert("RGB")
        base = ImageEnhance.Color(base).enhance(1.22)
        base = ImageEnhance.Brightness(base).enhance(1.06)
        base = ImageEnhance.Contrast(base).enhance(1.05)
        g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
        mid = tuple(int(accent[i] * 0.5 + 150 * 0.5) for i in range(3))
        duo = ImageOps.colorize(g, black=(28, 38, 52), white=(255, 255, 255),
                                mid=mid).convert("RGB")
        return Image.blend(base, duo, 0.10)
    g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    shadow = (7, 14, 26)
    high = (226, 234, 245)
    base_mid = (40, 64, 92)
    mid = tuple(int(accent[i] * 0.45 + base_mid[i] * 0.55) for i in range(3))
    duo = ImageOps.colorize(g, black=shadow, white=high, mid=mid).convert("RGB")
    return Image.blend(img.convert("RGB"), duo, 0.80)


def _navy_gradient(w: int, h: int) -> Image.Image:
    base = Image.new("RGB", (w, h), BG_BOTTOM)
    d = ImageDraw.Draw(base)
    for y in range(h):
        t = (y / (h - 1)) ** 0.9
        d.line([(0, y), (w, y)], fill=tuple(
            round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
    return base.convert("RGBA")


def _draw_text_block(draw, x, col_w, text_top, body_limit, kicker, headline,
                     paragraphs, accent, head_max=50):
    """Draw kicker + headline + accent rule + hierarchical body within a column,
    auto-sized to fit between text_top and body_limit."""
    f_kicker = _font("Rajdhani-SemiBold.ttf", 28)
    kicker_h = 50
    avail_h = body_limit - text_top
    headline = (headline or "").strip()

    head_lines: list[str] = []
    f_head = None
    head_lh = 0
    if headline:
        for hs in range(head_max, 31, -3):
            f_head = _font("Rajdhani-Bold.ttf", hs)
            head_lines = _wrap(draw, headline.upper(), f_head, col_w)
            head_lh = int(hs * 1.04)
            if len(head_lines) <= 2:
                break
    head_block = (len(head_lines) * head_lh + 22) if head_lines else 0

    body_avail = avail_h - kicker_h - head_block
    lh, gap, items = 22, 11, []
    for size in range(30, 13, -2):
        cand = _font("NunitoSans.ttf", size)
        strong = _font("Rajdhani-Bold.ttf", size + 3)
        clh, cgap = int(size * 1.4), int(size * 0.78)
        ci, total = _layout_body(draw, paragraphs, col_w, cand, strong, accent, clh, cgap)
        lh, gap, items = clh, cgap, ci
        if total <= body_avail:
            break

    y = text_top
    draw.rectangle([x, y + 11, x + 44, y + 15], fill=accent)
    _draw_tracked(draw, (x + 62, y), kicker.upper(), f_kicker, accent, 5)
    y += kicker_h
    if head_lines and f_head is not None:
        for ln in head_lines:
            draw.text((x, y), ln, font=f_head, fill=WHITE)
            y += head_lh
        draw.rectangle([x, y + 6, x + 64, y + 10], fill=accent)
        y += 22
    for kind, payload in items:
        if kind == "gap":
            y += payload
        else:
            if y + lh > body_limit:
                break
            for (seg_text, seg_font, seg_fill, dx) in payload:
                draw.text((x + dx, y), seg_text, font=seg_font, fill=seg_fill)
            y += lh


# --- design-system palette + helpers ---------------------------------------
NAVY_DEEP = (9, 18, 30)
INK = (22, 37, 53)            # near-black text on light templates
PAPER = (244, 246, 249)       # light template background
SUPPORT = (190, 202, 216)     # support text on navy
DOMAIN_DIM = (150, 166, 182)

_NUM_RE = re.compile(r"(\d[\d,]*\.?\d*\s?%|\$\d[\d,]*|\d+x|\d+\+|\d+(?:st|nd|rd|th)|\d+/\d+)")
_SENT_RE = re.compile(r"(.{18,150}?[.!?])(?:\s|$)")


def _lead_sentence(text: str) -> str:
    t = " ".join((text or "").split())
    m = _SENT_RE.match(t)
    return (m.group(1) if m else t[:140]).strip()


def _first_stat(text: str, headline: str) -> str | None:
    for s in (headline or "", text or ""):
        m = _NUM_RE.search(s)
        if m:
            return m.group(1).strip()
    return None


def _list_items(post_text: str) -> list[str]:
    items = []
    for p in _clean_body_for_image(post_text).split("\n"):
        m = _LIST_RE.match(p.strip())
        if m:
            items.append(m.group(2).strip())
    return items


def _kicker_width(draw, label, accent=None) -> int:
    """Total drawn width of the kicker for the current design (for centering)."""
    d = _design()
    kfile, kw = d["kfont"]
    f = _face(kfile, 22, kw)
    label = (label or "").upper()
    text_w = sum(draw.textlength(c, font=f) + 3 for c in label) - 3
    pad = {"tab": 30, "pill": 34, "outline": 34, "plain": 0, "underline": 0}
    return int(text_w) + pad.get(d["kicker"], 0)


def _kicker_tab(draw, x, y, label, accent, ink=(255, 255, 255)) -> int:
    """Draw the kicker in the current design's style: filled tab, rounded pill,
    outlined pill, plain tracked text, or underlined text."""
    d = _design()
    style = d["kicker"]
    kfile, kw = d["kfont"]
    f = _face(kfile, 22, kw)
    label = (label or "").upper()
    text_w = sum(draw.textlength(c, font=f) + 3 for c in label) - 3
    if style == "tab":
        draw.rectangle([x, y, x + int(text_w) + 30, y + 38], fill=accent)
        _draw_tracked(draw, (x + 15, y + 6), label, f, ink, 3)
    elif style == "pill":
        draw.rounded_rectangle([x, y, x + int(text_w) + 34, y + 40], radius=20, fill=accent)
        _draw_tracked(draw, (x + 17, y + 8), label, f, ink, 3)
    elif style == "outline":
        draw.rounded_rectangle([x, y, x + int(text_w) + 34, y + 40], radius=20,
                               outline=accent, width=3)
        _draw_tracked(draw, (x + 17, y + 8), label, f, accent, 3)
    elif style == "underline":
        _draw_tracked(draw, (x, y + 6), label, f, accent, 3)
        draw.rectangle([x, y + 34, x + int(text_w), y + 38], fill=accent)
    else:  # plain
        _draw_tracked(draw, (x, y + 6), label, f, accent, 3)
    return y + 40


def _motif(img: Image.Image, accent) -> None:
    """Draw the design's signature decorative element so each brand has its own
    recognizable shape language (not just a recolored template)."""
    motif = _design().get("motif", "none")
    if motif == "none":
        return
    layer = Image.new("RGBA", (_LW, _LH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    soft = (*accent, 30)
    if motif == "blob":
        d.ellipse([_LW - 360, -160, _LW + 160, 360], fill=soft)
        d.ellipse([-160, _LH - 300, 240, _LH + 200], fill=(*accent, 22))
    elif motif == "arc":
        d.ellipse([_LW // 2 - 620, -940, _LW // 2 + 620, 120], outline=(*accent, 70), width=14)
    elif motif == "stripe":
        for i in range(-1, 3):
            off = i * 66
            d.polygon([(_LW - 250 + off, 0), (_LW - 130 + off, 0),
                       (_LW - 380 + off, _LH), (_LW - 500 + off, _LH)],
                      fill=(*accent, 26))
    elif motif == "corner":
        d.rectangle([_LW - 150, 60, _LW - 60, 74], fill=(*accent, 120))
        d.rectangle([_LW - 76, 60, _LW - 60, 190], fill=(*accent, 120))
        d.rectangle([60, _LH - 190, 74, _LH - 60], fill=(*accent, 120))
        d.rectangle([60, _LH - 76, 190, _LH - 60], fill=(*accent, 120))
    img.alpha_composite(layer)


def _footer_navy(img, draw, margin, light=False):
    fy = _LH - 92
    _place_logo_footer(img, margin, fy, 60, on_dark=True)
    fd = _font("NunitoSans.ttf", 24)
    dw = draw.textlength(_domain(), font=fd)
    draw.text((_LW - margin - dw, fy + 18), _domain(), font=fd, fill=(226, 234, 244))


def render_statement(post_text, out_path, kicker, headline, accent, seed=None):
    """Bold typographic poster, no photo: kicker tab + huge headline + lead."""
    img = _premium_bg(accent, (seed or 0))
    try:
        mk = _logo_mark_image()
        th = 600
        mk = mk.resize((int(mk.width * th / mk.height), th))
        a = mk.split()[3].point(lambda p: int(p * 0.07))
        mk.putalpha(a)
        img.alpha_composite(mk, (_LW - mk.width + 130, (_LH - mk.height) // 2))
    except Exception:
        pass
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 9, _LH], fill=accent)
    margin = 78
    col_w = _LW - margin - 330
    _kicker_tab(draw, margin, 72, kicker, accent)
    y = 144
    headline = (headline or "").strip().upper()
    fh = _font("Rajdhani-Bold.ttf", 104)
    hl, head_lh = [headline], 104
    for hs in range(104, 57, -6):
        fh = _font("Rajdhani-Bold.ttf", hs)
        hl = _wrap(draw, headline, fh, col_w)
        head_lh = int(hs * 1.0)
        if len(hl) <= 3:
            break
    for ln in hl:
        draw.text((margin, y), ln, font=fh, fill=WHITE)
        y += head_lh
    draw.rectangle([margin, y + 12, margin + 92, y + 20], fill=accent)
    y += 46
    fs = _font("NunitoSans.ttf", 26)
    for ln in _wrap(draw, _lead_sentence(post_text), fs, col_w)[:3]:
        draw.text((margin, y), ln, font=fs, fill=SUPPORT)
        y += 38
    draw = ImageDraw.Draw(img)
    _footer_navy(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "statement"


def render_stat(post_text, out_path, kicker, headline, accent, seed=None):
    """Big-number hero for stat-driven posts -- gradient number with glow."""
    img = _premium_bg(accent, (seed or 0))
    draw = ImageDraw.Draw(img)
    margin = 92
    stat = _first_stat(post_text, headline) or "24/7"
    fn = _font("Rajdhani-Bold.ttf", 300)
    num_h = int(fn.size * 0.80)
    fh = _font("Rajdhani-Bold.ttf", 58)
    hl = _wrap(draw, (headline or "").upper(), fh, _LW - 2 * margin)[:3]
    fs = _font("NunitoSans.ttf", 27)
    support = _wrap(draw, _lead_sentence(post_text), fs, _LW - 2 * margin)[:2]
    kicker_h, head_lh, sup_lh = 58, 60, 37
    block_h = kicker_h + num_h + len(hl) * head_lh + 16 + len(support) * sup_lh
    y = _center_start(block_h, top=110, bottom=185)
    _kicker_tab(draw, margin, y, kicker, accent)
    y += kicker_h + 6
    _grad_text(img, margin - 10, y, stat, fn, _lighten(accent, 0.6), accent,
               shadow=True, glow=accent)
    y += num_h + 14
    draw = ImageDraw.Draw(img)
    y = _draw_headline_grad(img, margin, y, hl, fh, head_lh, accent)
    y += 12
    for ln in support:
        draw.text((margin, y), ln, font=fs, fill=(196, 208, 222))
        y += sup_lh
    _footer_navy(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "stat"


def render_checklist(post_text, out_path, kicker, headline, accent, seed=None):
    """Clean LIGHT card with numbered accent badges -- strong contrast to the
    dark photo/typographic cards."""
    img = Image.new("RGB", (_LW, _LH), PAPER).convert("RGBA")
    _motif(img, accent)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _LW, 8], fill=accent)
    margin = 90
    fh = _head_font(54 if _style() == "bright" else 58)
    hl = _wrap(draw, _head_text(headline), fh, _LW - 2 * margin)[:2]
    items = _list_items(post_text)
    if not items:
        items = [s for s in _lead_sentence(post_text).split(". ") if s][:3]
    fi = _font("NunitoSans.ttf", 28)
    fb = _nunito(27, 800) if _style() == "bright" else _font("Rajdhani-Bold.ttf", 27)
    item_lines = [_wrap(draw, it, fi, _LW - margin - 66 - margin)[:2] for it in items[:4]]
    kicker_h, head_lh = 54, 60
    items_h = sum(max(60, len(ls) * 37 + 16) for ls in item_lines)
    block_h = kicker_h + len(hl) * head_lh + 28 + items_h
    bar_h = 88
    y = max(60, (_LH - bar_h - block_h) // 2)
    _kicker_tab(draw, margin, y, kicker, accent)
    y += kicker_h
    for ln in hl:
        draw.text((margin, y), ln, font=fh, fill=INK)
        y += head_lh
    y += 28
    for idx, lines in enumerate(item_lines, 1):
        draw.ellipse([margin, y, margin + 44, y + 44], fill=accent)
        ns = str(idx)
        nw = draw.textlength(ns, font=fb)
        draw.text((margin + 22 - nw / 2, y + 8), ns, font=fb, fill=(255, 255, 255))
        ty = y + 4
        for ln in lines:
            draw.text((margin + 66, ty), ln, font=fi, fill=(54, 68, 82))
            ty += 37
        y = max(y + 60, ty + 16)
    draw.rectangle([0, _LH - bar_h, _LW, _LH], fill=NAVY_DEEP)
    _place_logo_footer(img, margin, _LH - bar_h + (bar_h - 54) // 2, 54, on_dark=True)
    fd = _font("NunitoSans.ttf", 24)
    dw = draw.textlength(_domain(), font=fd)
    draw.text((_LW - margin - dw, _LH - bar_h + (bar_h - 24) // 2), _domain(),
              font=fd, fill=(180, 194, 208))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "checklist"


def _draw_headline_white(img, x, y, lines, font, lh):
    """Crisp pure-white headline with a soft drop shadow for photo legibility."""
    for ln in lines:
        sh = Image.new("RGBA", (int(img.width), lh + 40), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((3, 5), ln, font=font, fill=(0, 0, 0, 170))
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        img.alpha_composite(sh, (x, y))
        ImageDraw.Draw(img).text((x, y), ln, font=font, fill=(255, 255, 255))
        y += lh
    return y


def render_editorial(post_text, out_path, kicker, headline, accent, photo_path, seed=None):
    """Photo-forward: brand-toned full photo, big headline over a bottom scrim."""
    bright = _style() == "bright"
    img = _brand_tone(ImageOps.fit(Image.open(photo_path).convert("RGB"),
                                   (_LW, _LH), method=Image.LANCZOS), accent).convert("RGBA")
    band = Image.new("RGBA", (_LW, _LH), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    # A slightly deeper, taller scrim so a white headline always reads on a
    # bright photo.
    reach, peak = (430, 250) if bright else (360, 244)
    for y in range(_LH):
        t = max(0.0, (y - (_LH - reach)) / reach)
        bd.line([(0, y), (_LW, y)], fill=(*NAVY_DEEP, int(peak * (t ** 1.15))))
    img.alpha_composite(band)
    if not bright:
        img.alpha_composite(_radial_glow(560, 560, accent, 0.22), (-180, _LH - 430))
        _grain(img, 7)
    draw = ImageDraw.Draw(img)
    margin = 80
    hsize = 60 if bright else 66
    fh = _head_font(hsize)
    hl = _wrap(draw, _head_text(headline), fh, _LW - 2 * margin)[:3]
    lh = int(hsize * 1.14) if bright else 66
    fy = _LH - 84
    y = fy - 24 - len(hl) * lh
    _kicker_tab(draw, margin, y - 56, kicker, accent)
    if bright:
        _draw_headline_white(img, margin, y, hl, fh, lh)
    else:
        _draw_headline_grad(img, margin, y, hl, fh, lh, accent)
    draw = ImageDraw.Draw(img)
    _footer_navy(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "editorial"


def _footer_bar(img, draw, margin, bar_h=88):
    """Navy footer strip (used by light / color templates so the logo reads)."""
    draw.rectangle([0, _LH - bar_h, _LW, _LH], fill=NAVY_DEEP)
    _place_logo_footer(img, margin, _LH - bar_h + (bar_h - 54) // 2, 54, on_dark=True)
    fd = _font("NunitoSans.ttf", 23)
    dw = draw.textlength(_domain(), font=fd)
    draw.text((_LW - margin - dw, _LH - bar_h + (bar_h - 23) // 2),
              _domain(), font=fd, fill=(180, 194, 208))


def _rule(draw, cx_or_x, y, accent, centered=False):
    """Draw the design's accent rule (bar / long / dot / none)."""
    style = _design().get("rule", "bar")
    if style == "none":
        return
    if style == "dot":
        r = 7
        gap = 26
        total = gap * 2
        x0 = (cx_or_x - total // 2) if centered else cx_or_x
        for i in range(3):
            cx = x0 + i * gap
            draw.ellipse([cx - r, y - r, cx + r, y + r], fill=accent)
        return
    w = 160 if style == "long" else 92
    x0 = (cx_or_x - w // 2) if centered else cx_or_x
    draw.rectangle([x0, y - 4, x0 + w, y + 4], fill=accent)


def render_light_statement(post_text, out_path, kicker, headline, accent, seed=None):
    """Light card, big headline in the account's own type + layout identity."""
    d = _design()
    centered = d["align"] == "center"
    img = Image.new("RGB", (_LW, _LH), PAPER).convert("RGBA")
    _motif(img, accent)
    draw = ImageDraw.Draw(img)
    if not centered:
        draw.rectangle([0, 0, 9, _LH], fill=accent)
    margin = 90
    headline = _head_text(headline)
    hi_size = 92 if _style() == "bright" else 108
    fh = _head_font(hi_size)
    hl, head_lh = [headline], int(hi_size * 1.04)
    for hs in range(hi_size, 51, -6):
        fh = _head_font(hs)
        hl = _wrap(draw, headline, fh, _LW - 2 * margin - 20)
        head_lh = int(hs * 1.12) if _style() == "bright" else int(hs * 1.0)
        if len(hl) <= 4:
            break
    fs = _font("NunitoSans.ttf", 27)
    support = _wrap(draw, _lead_sentence(post_text), fs, _LW - 2 * margin - 20)[:3]
    kicker_h, rule_h, sup_lh = 56, 46, 40
    block_h = kicker_h + len(hl) * head_lh + rule_h + len(support) * sup_lh
    y = _center_start(block_h, top=130, bottom=175)
    cx = _LW // 2

    if centered:
        kw = _kicker_width(draw, kicker)
        _kicker_tab(draw, cx - kw // 2, y, kicker, accent)
    else:
        _kicker_tab(draw, margin, y, kicker, accent)
    y += kicker_h
    for ln in hl:
        lw = draw.textlength(ln, font=fh)
        x = (cx - lw / 2) if centered else margin
        draw.text((x, y), ln, font=fh, fill=INK)
        y += head_lh
    _rule(draw, cx if centered else margin, y + 16, accent, centered=centered)
    y += rule_h
    for ln in support:
        lw = draw.textlength(ln, font=fs)
        x = (cx - lw / 2) if centered else margin
        draw.text((x, y), ln, font=fs, fill=(84, 98, 112))
        y += sup_lh
    _footer_bar(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "light-statement"


def render_bold_color(post_text, out_path, kicker, headline, accent, seed=None):
    """Full brand-color block, white type -- a punchy scroll-stopper."""
    deep = tuple(max(0, int(c * 0.72)) for c in accent)
    base = Image.new("RGB", (_LW, _LH), accent)
    d0 = ImageDraw.Draw(base)
    for y in range(_LH):
        t = y / (_LH - 1)
        d0.line([(0, y), (_LW, y)],
                fill=tuple(round(accent[i] + (deep[i] - accent[i]) * t) for i in range(3)))
    img = base.convert("RGBA")
    img.alpha_composite(_radial_glow(880, 880, _lighten(accent, 0.55), 0.30), (540, -320))
    _grain(img, 9)
    draw = ImageDraw.Draw(img)
    margin = 90
    headline = _head_text(headline)
    hi_size = 94 if _style() == "bright" else 112
    fh = _head_font(hi_size)
    hl, head_lh = [headline], int(hi_size * 1.06)
    for hs in range(hi_size, 53, -6):
        fh = _head_font(hs)
        hl = _wrap(draw, headline, fh, _LW - 2 * margin)
        head_lh = int(hs * 1.12) if _style() == "bright" else int(hs * 1.0)
        if len(hl) <= 4:
            break
    fs = _font("NunitoSans.ttf", 27)
    support = _wrap(draw, _lead_sentence(post_text), fs, _LW - 2 * margin)[:3]
    kicker_h, rule_h, sup_lh = 52, 46, 40
    block_h = kicker_h + len(hl) * head_lh + rule_h + len(support) * sup_lh
    y = _center_start(block_h, top=130, bottom=175)
    fk = _font("Rajdhani-SemiBold.ttf", 24)
    _draw_tracked(draw, (margin, y), (kicker or "").upper(), fk, (255, 255, 255), 5)
    y += kicker_h
    for ln in hl:
        draw.text((margin, y), ln, font=fh, fill=(255, 255, 255))
        y += head_lh
    draw.rectangle([margin, y + 12, margin + 92, y + 20], fill=(255, 255, 255))
    y += rule_h
    for ln in support:
        draw.text((margin, y), ln, font=fs, fill=(238, 244, 250))
        y += sup_lh
    _footer_bar(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "bold-color"


def render_two_block(post_text, out_path, kicker, headline, accent, seed=None):
    """Two stacked tone blocks (e.g. Myth / Reality, Before / After)."""
    img = Image.new("RGB", (_LW, _LH), NAVY_DEEP).convert("RGBA")
    draw = ImageDraw.Draw(img)
    paras = [p.strip() for p in _clean_body_for_image(post_text).split("\n") if p.strip()]

    def _split(p):
        m = re.match(r"^([A-Za-z][A-Za-z ]{1,16}):\s*(.*)", p or "")
        return (m.group(1).upper(), m.group(2)) if m else (None, p or "")

    b1 = paras[0] if paras else ""
    b2 = paras[1] if len(paras) > 1 else b1
    l1, t1 = _split(b1)
    l2, t2 = _split(b2)
    l1 = l1 or "THE PROBLEM"
    l2 = l2 or "THE FIX"
    half = _LH // 2
    draw.rectangle([0, 0, _LW, half], fill=(30, 43, 57))
    bot = tuple(int(accent[i] * 0.28 + NAVY_DEEP[i] * 0.72) for i in range(3))
    draw.rectangle([0, half, _LW, _LH], fill=bot)
    seam = Image.new("RGBA", (_LW, 50), (0, 0, 0, 0))
    ImageDraw.Draw(seam).rectangle([0, 23, _LW, 27], fill=(*accent, 255))
    img.alpha_composite(seam.filter(ImageFilter.GaussianBlur(7)), (0, half - 25))
    draw.rectangle([0, half - 3, _LW, half + 3], fill=accent)
    _grain(img, 8)
    draw = ImageDraw.Draw(img)
    margin = 90
    fl = _font("Rajdhani-Bold.ttf", 34)
    ft = _font("NunitoSans.ttf", 29)
    lab_h, line_h = 54, 40
    t1_lines = _wrap(draw, t1, ft, _LW - 2 * margin)[:4]
    top_h = lab_h + len(t1_lines) * line_h
    ty = (half - top_h) // 2
    draw.text((margin, ty), l1, font=fl, fill=(150, 164, 178))
    ty += lab_h
    for ln in t1_lines:
        draw.text((margin, ty), ln, font=ft, fill=(214, 224, 234))
        ty += line_h
    t2_lines = _wrap(draw, t2, ft, _LW - 2 * margin - 40)[:4]
    bot_h = lab_h + len(t2_lines) * line_h
    by = half + (half - 76 - bot_h) // 2
    draw.text((margin, by), l2, font=fl, fill=accent)
    by += lab_h
    for ln in t2_lines:
        draw.text((margin, by), ln, font=ft, fill=WHITE)
        by += line_h
    _place_logo_footer(img, margin, _LH - 84, 54, on_dark=True)
    fd = _font("Rajdhani-SemiBold.ttf", 23)
    dw = draw.textlength(_domain(), font=fd)
    draw.text((_LW - margin - dw, _LH - 56), _domain(), font=fd, fill=(150, 166, 182))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "two-block"


def render_band(post_text, out_path, kicker, headline, accent, photo_path, seed=None):
    """Brand-toned photo on top, solid navy band with text on the bottom."""
    ph_h = 560
    img = _premium_bg(accent, (seed or 0))
    photo = _brand_tone(ImageOps.fit(Image.open(photo_path).convert("RGB"),
                                     (_LW, ph_h), method=Image.LANCZOS), accent).convert("RGBA")
    fade = Image.new("RGBA", (_LW, ph_h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    for y in range(ph_h):
        t = max(0.0, (y - (ph_h - 120)) / 120)
        fd.line([(0, y), (_LW, y)], fill=(*NAVY_DEEP, int(235 * (t ** 1.4))))
    photo.alpha_composite(fade)
    img.alpha_composite(photo, (0, 0))
    draw = ImageDraw.Draw(img)
    seam = Image.new("RGBA", (_LW, 60), (0, 0, 0, 0))
    ImageDraw.Draw(seam).rectangle([0, 28, _LW, 32], fill=(*accent, 255))
    img.alpha_composite(seam.filter(ImageFilter.GaussianBlur(6)), (0, ph_h - 30))
    draw.rectangle([0, ph_h, _LW, ph_h + 4], fill=accent)
    margin = 82
    fh = _font("Rajdhani-Bold.ttf", 56)
    hl = _wrap(draw, (headline or "").upper(), fh, _LW - 2 * margin)[:3]
    kh, hlh = 44, 58
    block_h = kh + 16 + len(hl) * hlh
    band_top, band_bot = ph_h + 8, _LH - 96
    ty = band_top + max(0, (band_bot - band_top - block_h) // 2)
    _kicker_tab(draw, margin, ty, kicker, accent)
    ty += kh + 16
    _draw_headline_grad(img, margin, ty, hl, fh, hlh, accent)
    draw = ImageDraw.Draw(img)
    _footer_navy(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "band"


def render_quote(post_text, out_path, kicker, headline, accent, seed=None):
    """Pull-quote: glowing accent bar + large gradient statement."""
    img = _premium_bg(accent, (seed or 0))
    draw = ImageDraw.Draw(img)
    margin = 98
    q = (headline or "").strip() or _lead_sentence(post_text)
    fh = _font("Rajdhani-Bold.ttf", 84)
    hl, head_lh = [q.upper()], 88
    for hs in range(84, 47, -5):
        fh = _font("Rajdhani-Bold.ttf", hs)
        hl = _wrap(draw, q.upper(), fh, _LW - margin - 150)
        head_lh = int(hs * 1.06)
        if len(hl) <= 5:
            break
    block_h = len(hl) * head_lh + 46
    top = _center_start(block_h, top=140, bottom=185)
    bar = Image.new("RGBA", (44, block_h + 48), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle([18, 18, 26, block_h + 26], 4, fill=(*accent, 255))
    img.alpha_composite(bar.filter(ImageFilter.GaussianBlur(8)), (margin - 18, top - 18))
    img.alpha_composite(bar, (margin - 18, top - 18))
    y = top
    for ln in hl:
        _grad_text(img, margin + 36, y, ln, fh, (247, 250, 255), _lighten(accent, 0.35))
        y += head_lh
    draw = ImageDraw.Draw(img)
    fk = _font("Rajdhani-SemiBold.ttf", 24)
    _draw_tracked(draw, (margin + 36, y + 16),
                  (kicker or tenants.account().get("name", "")).upper(),
                  fk, accent, 4)
    _footer_navy(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "quote"


# ---------------------------------------------------------------------------
# Premium effects toolkit (glows, grain, gradient text, glass, mesh bg)
# ---------------------------------------------------------------------------
def _lighten(c, f=0.45):
    return tuple(min(255, int(c[i] + (255 - c[i]) * f)) for i in range(3))


def _vgrad(w, h, c1, c2):
    g = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    return g


def _radial_glow(w, h, color, intensity):
    g = Image.new("L", (w, h), 0)
    ImageDraw.Draw(g).ellipse([w * 0.12, h * 0.12, w * 0.88, h * 0.88], fill=255)
    g = g.filter(ImageFilter.GaussianBlur(int(w * 0.15)))
    layer = Image.new("RGBA", (w, h), (*color, 0))
    layer.putalpha(g.point(lambda p: int(p * intensity)))
    return layer


def _grain(img, opacity=10):
    n = Image.effect_noise(img.size, 24).convert("L")
    img.alpha_composite(Image.merge("RGBA", (n, n, n, n.point(lambda p: opacity))))


def _premium_bg(accent, seed=0, second=None):
    """Rich gradient-mesh navy background with soft accent glows + grain."""
    second = second or (BLUE if accent != BLUE else GREEN_SOFT)
    img = _vgrad(_LW, _LH, (13, 26, 44), (6, 10, 18)).convert("RGBA")
    img.alpha_composite(_radial_glow(880, 880, accent, 0.55), (560, -320))
    img.alpha_composite(_radial_glow(760, 760, second, 0.30), (-300, 560))
    img.alpha_composite(_radial_glow(460, 460, accent, 0.16), (330, 520))
    _grain(img, 9)
    return img


def _grad_text(img, x, y, text, font, c_top, c_bottom, shadow=True, glow=None):
    d0 = ImageDraw.Draw(img)
    w = int(d0.textlength(text, font=font)) + 8
    asc, desc = font.getmetrics()
    h = asc + desc + 6
    if glow is not None:
        gl = Image.new("RGBA", (w + 80, h + 80), (0, 0, 0, 0))
        ImageDraw.Draw(gl).text((40, 40), text, font=font, fill=(*glow, 150))
        gl = gl.filter(ImageFilter.GaussianBlur(18))
        img.alpha_composite(gl, (x - 40, y - 40))
    if shadow:
        sh = Image.new("RGBA", (w + 60, h + 60), (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((30, 34), text, font=font, fill=(0, 0, 0, 150))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        img.alpha_composite(sh, (x - 30, y - 30))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
    img.paste(_vgrad(w, h, c_top, c_bottom), (x, y), mask)


def _glass_panel(img, box, radius=22, tint=(10, 18, 30), alpha=150, border=True):
    """Frosted-glass panel: blur the region behind, overlay a tint + border."""
    x0, y0, x1, y1 = box
    region = img.crop(box).filter(ImageFilter.GaussianBlur(16))
    img.paste(region, (x0, y0))
    panel = Image.new("RGBA", (x1 - x0, y1 - y0), (*tint, alpha))
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, x1 - x0 - 1, y1 - y0 - 1], radius, fill=255)
    img.paste(panel, (x0, y0), mask)
    if border:
        ol = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ol).rounded_rectangle([x0, y0, x1 - 1, y1 - 1], radius,
                                             outline=(255, 255, 255, 38), width=1)
        img.alpha_composite(ol)


def _draw_headline_grad(img, x, y, lines, font, lh, accent):
    for ln in lines:
        _grad_text(img, x, y, ln, font, (247, 250, 255), _lighten(accent, 0.35))
        y += lh
    return y


def render_hero(post_text, out_path, kicker, headline, accent, seed=None):
    """Flagship typographic hero: gradient-mesh bg, glowing accent, gradient
    headline with depth, film grain."""
    img = _premium_bg(accent, (seed or 0))
    draw = ImageDraw.Draw(img)
    margin = 92
    col_w = _LW - 2 * margin - 20
    headline = (headline or "").strip().upper()
    fh = _font("Rajdhani-Bold.ttf", 120)
    hl, head_lh = [headline], 118
    for hs in range(120, 65, -7):
        fh = _font("Rajdhani-Bold.ttf", hs)
        hl = _wrap(draw, headline, fh, col_w)
        head_lh = int(hs * 0.98)
        if len(hl) <= 4:
            break
    fs = _font("NunitoSans.ttf", 29)
    support = _wrap(draw, _lead_sentence(post_text), fs, col_w)[:3]
    kicker_h, rule_h, sup_lh = 56, 58, 43
    block_h = kicker_h + len(hl) * head_lh + rule_h + len(support) * sup_lh
    y = _center_start(block_h, top=150, bottom=190)
    img.alpha_composite(_radial_glow(400, 170, accent, 0.5), (margin - 90, y - 46))
    draw = ImageDraw.Draw(img)
    _kicker_tab(draw, margin, y, kicker, accent)
    y += kicker_h
    y = _draw_headline_grad(img, margin, y, hl, fh, head_lh, accent)
    rule = Image.new("RGBA", (200, 44), (0, 0, 0, 0))
    ImageDraw.Draw(rule).rounded_rectangle([12, 18, 122, 26], 4, fill=(*accent, 255))
    img.alpha_composite(rule.filter(ImageFilter.GaussianBlur(7)), (margin - 12, y + 6))
    img.alpha_composite(rule, (margin - 12, y + 6))
    y += rule_h
    draw = ImageDraw.Draw(img)
    for ln in support:
        draw.text((margin, y), ln, font=fs, fill=(196, 208, 222))
        y += sup_lh
    _footer_navy(img, draw, margin)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return "hero"


# Each post format maps to a POOL of fitting designs; the dispatcher rotates
# through them (avoiding the few most-recently used) so the feed stays varied.
_PHOTO_TEMPLATES = {"editorial", "split", "overlay", "band"}
_FORMAT_POOL = {
    "stat-led": ["stat", "bold-color", "statement"],
    "listicle": ["checklist", "band", "statement", "editorial"],
    "how-to": ["checklist", "band", "statement"],
    "quick-tip": ["checklist", "bold-color", "statement", "editorial"],
    "one-bold-idea": ["statement", "bold-color", "quote", "editorial"],
    "question-led": ["statement", "quote", "light-statement", "editorial"],
    "myth-reality": ["two-block", "statement", "split"],
    "comparison": ["two-block", "band", "statement", "split"],
    "scenario": ["editorial", "split", "quote", "overlay"],
    "human-angle": ["quote", "overlay", "editorial", "light-statement", "split"],
}
_DEFAULT_PHOTO = ["editorial", "split", "overlay", "band", "quote"]
_DEFAULT_TEXT = ["statement", "light-statement", "bold-color", "quote"]


# ---------------------------------------------------------------------------
# Distinct per-design LAYOUTS (composition, not just type/color). Each bright
# design system owns a different set so no two brands are laid out the same way.
# ---------------------------------------------------------------------------
def _fit_head(draw, text, max_w, max_size, min_size, max_lines):
    """Auto-size the design headline font to fit within max_lines lines."""
    text = _head_text(text)
    fh, lines, lh = _head_font(max_size), [text], int(max_size * 1.12)
    for hs in range(max_size, min_size - 1, -5):
        fh = _head_font(hs)
        lines = _wrap(draw, text, fh, max_w)
        lh = int(hs * 1.12)
        if len(lines) <= max_lines:
            break
    return fh, lines[:max_lines], lh


def _body_lines(draw, post_text, font, max_w, n):
    return _wrap(draw, _lead_sentence(post_text), font, max_w)[:n]


def _lay_top_bar(post_text, out, kicker, headline, accent, photo_path=None, seed=None):
    """Content pinned to the TOP; airy space below. Distinct from a centered stack."""
    img = Image.new("RGB", (_LW, _LH), PAPER).convert("RGBA")
    _motif(img, accent)
    draw = ImageDraw.Draw(img)
    m, top = 90, 120
    _kicker_tab(draw, m, top, kicker, accent)
    y = top + 66
    fh, hl, lh = _fit_head(draw, headline, _LW - 2 * m, 98, 52, 3)
    for ln in hl:
        draw.text((m, y), ln, font=fh, fill=INK)
        y += lh
    _rule(draw, m, y + 18, accent)
    y += 52
    fs = _font("NunitoSans.ttf", 28)
    for ln in _body_lines(draw, post_text, fs, _LW - 2 * m, 3):
        draw.text((m, y), ln, font=fs, fill=(84, 98, 112))
        y += 40
    _footer_bar(img, draw, m)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return "top-bar"


def _lay_corner(post_text, out, kicker, headline, accent, photo_path=None, seed=None):
    """Big headline anchored at the BOTTOM-left; open space + motif up top."""
    img = Image.new("RGB", (_LW, _LH), PAPER).convert("RGBA")
    _motif(img, accent)
    draw = ImageDraw.Draw(img)
    m, bar_h = 90, 76
    _kicker_tab(draw, m, 120, kicker, accent)
    fh, hl, lh = _fit_head(draw, headline, _LW - 2 * m, 120, 60, 3)
    fs = _font("NunitoSans.ttf", 27)
    support = _body_lines(draw, post_text, fs, _LW - 2 * m, 2)
    sup_h = len(support) * 38
    y = (_LH - bar_h - 44 - sup_h) - len(hl) * lh
    for ln in hl:
        draw.text((m, y), ln, font=fh, fill=INK)
        y += lh
    _rule(draw, m, y + 14, accent)
    y += 34
    for ln in support:
        draw.text((m, y), ln, font=fs, fill=(84, 98, 112))
        y += 38
    _footer_bar(img, draw, m)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return "corner"


def _lay_side_band(post_text, out, kicker, headline, accent, photo_path=None, seed=None):
    """Vertical split: full-height accent band on the left, content on the right."""
    img = Image.new("RGB", (_LW, _LH), PAPER).convert("RGBA")
    bw = int(_LW * 0.36)
    band = _vgrad(bw, _LH, _lighten(accent, 0.06),
                  tuple(int(c * 0.82) for c in accent))
    img.paste(band, (0, 0))
    draw = ImageDraw.Draw(img)
    kf = _face(_design()["kfont"][0], 24, _design()["kfont"][1])
    _draw_tracked(draw, (56, 120), (kicker or "").upper(), kf, (255, 255, 255), 3)
    # Brand mark on the band: real logo if present, else the name in white.
    if tenants.logo_full().exists():
        _place_logo_footer(img, 56, _LH - 168, 62, on_dark=True)
        dd = _font("NunitoSans.ttf", 22)
        draw.text((56, _LH - 88), _domain(), font=dd, fill=(235, 242, 248))
    else:
        nf = _face(_design()["head"][0], 40, _design()["head"][1])
        name = (tenants.account().get("name") or "").strip()
        ny = _LH - 150
        for ln in _wrap(draw, name, nf, bw - 96)[:3]:
            draw.text((56, ny), ln, font=nf, fill=(255, 255, 255))
            ny += 46
        dd = _font("NunitoSans.ttf", 22)
        draw.text((56, _LH - 60), _domain(), font=dd, fill=(235, 242, 248))
    rx = bw + 70
    colw = _LW - rx - 70
    fh, hl, lh = _fit_head(draw, headline, colw, 76, 40, 4)
    fs = _font("NunitoSans.ttf", 26)
    support = _body_lines(draw, post_text, fs, colw, 4)
    block = len(hl) * lh + 30 + len(support) * 36
    y = max(110, (_LH - block) // 2)
    for ln in hl:
        draw.text((rx, y), ln, font=fh, fill=INK)
        y += lh
    _rule(draw, rx, y + 14, accent)
    y += 30
    for ln in support:
        draw.text((rx, y), ln, font=fs, fill=(84, 98, 112))
        y += 36
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return "side-band"


def _lay_frame(post_text, out, kicker, headline, accent, photo_path=None, seed=None):
    """Inset bordered card floating on a soft brand-tinted background."""
    tint = _lighten(accent, 0.85)
    img = Image.new("RGB", (_LW, _LH), tint).convert("RGBA")
    draw = ImageDraw.Draw(img)
    m = 62
    draw.rounded_rectangle([m, m, _LW - m, _LH - m], radius=26, fill=PAPER)
    draw.rounded_rectangle([m, m, _LW - m, _LH - m], radius=26, outline=accent, width=2)
    inm = m + 66
    colw = _LW - 2 * inm
    centered = _design()["align"] == "center"
    cx = _LW // 2
    fh, hl, lh = _fit_head(draw, headline, colw, 80, 42, 4)
    fs = _font("NunitoSans.ttf", 26)
    support = _body_lines(draw, post_text, fs, colw, 3)
    kh = 54
    block = kh + len(hl) * lh + 40 + len(support) * 36
    y = (_LH - block) // 2 - 10
    if centered:
        kw = _kicker_width(draw, kicker)
        _kicker_tab(draw, cx - kw // 2, y, kicker, accent)
    else:
        _kicker_tab(draw, inm, y, kicker, accent)
    y += kh
    for ln in hl:
        lw = draw.textlength(ln, font=fh)
        x = (cx - lw / 2) if centered else inm
        draw.text((x, y), ln, font=fh, fill=INK)
        y += lh
    _rule(draw, cx if centered else inm, y + 16, accent, centered=centered)
    y += 40
    for ln in support:
        lw = draw.textlength(ln, font=fs)
        x = (cx - lw / 2) if centered else inm
        draw.text((x, y), ln, font=fs, fill=(84, 98, 112))
        y += 36
    lf_y = _LH - m - 58
    _place_logo_footer(img, inm, lf_y, 54, on_dark=False)
    fd = _font("NunitoSans.ttf", 22)
    dw = draw.textlength(_domain(), font=fd)
    draw.text((_LW - inm - dw, lf_y + 8), _domain(), font=fd, fill=(140, 152, 166))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "PNG")
    return "frame"


# Each bright design owns a distinct set of layouts (primary first). Photo and
# list layouts are shared but only used when the post has a photo / a list.
# Each design keeps a recognizable identity (its own font/color/motif) but draws
# from several layouts so a single brand's feed stays varied. Pools differ per
# design so two brands are still composed differently.
_DESIGN_LAYOUTS = {
    "soft-rounded": ["top-bar", "center-hero", "corner", "editorial", "checklist"],
    "friendly-round": ["center-hero", "frame", "top-bar", "editorial", "checklist"],
    "elegant-serif": ["frame", "center-hero", "corner", "editorial", "side-band"],
    "bold-impact": ["side-band", "corner", "bold-color", "top-bar", "editorial"],
    "modern-grotesk": ["corner", "side-band", "top-bar", "center-hero", "editorial"],
}


def _render_designed(post_text, out_path, kicker, headline, accent,
                     format_id, photo_path, avoid, rng):
    """Route a bright account's post to one of ITS design's distinct layouts."""
    did = current_design_id()
    pool = list(_DESIGN_LAYOUTS.get(did, ["center-hero"]))
    has_list = bool(_list_items(post_text))
    valid = []
    for t in pool:
        if t == "editorial" and not photo_path:
            continue
        if t == "checklist" and not has_list:
            continue
        valid.append(t)
    if not valid:
        valid = ["center-hero"]
    fresh = [t for t in valid if t not in (avoid or set())]
    t = rng.choice(fresh or valid)
    if t == "center-hero":
        return render_light_statement(post_text, out_path, kicker, headline, accent)
    if t == "top-bar":
        return _lay_top_bar(post_text, out_path, kicker, headline, accent)
    if t == "corner":
        return _lay_corner(post_text, out_path, kicker, headline, accent)
    if t == "side-band":
        return _lay_side_band(post_text, out_path, kicker, headline, accent)
    if t == "frame":
        return _lay_frame(post_text, out_path, kicker, headline, accent)
    if t == "bold-color":
        return render_bold_color(post_text, out_path, kicker, headline, accent)
    if t == "checklist":
        return render_checklist(post_text, out_path, kicker, headline, accent)
    if t == "editorial":
        return render_editorial(post_text, out_path, kicker, headline, accent, photo_path)
    return render_light_statement(post_text, out_path, kicker, headline, accent)


def render_post_graphic(post_text, out_path, kicker="", headline="",
                        format_id="", photo_path=None, accent=None, seed=None,
                        avoid=None):
    """Pick a fitting template (rotating, avoiding recent ones) and render it.
    Returns the template name used (also stored as image_style)."""
    rng = random.Random(seed)
    if accent is None:
        accent = _accent_pick(rng)
    headline = (headline or "").strip()
    avoid = set(avoid or [])
    has_list = bool(_list_items(post_text))
    has_stat = _first_stat(post_text, headline) is not None

    # Bright accounts render in their own distinct per-design layouts.
    if _style() == "bright":
        return _render_designed(post_text, out_path, kicker, headline, accent,
                                format_id, photo_path, avoid, rng)

    pool = list(_FORMAT_POOL.get(format_id or "", []))
    if not pool:
        pool = list(_DEFAULT_PHOTO if photo_path else _DEFAULT_TEXT)
    valid = []
    for t in pool:
        if t in _PHOTO_TEMPLATES and not photo_path:
            continue
        if t == "checklist" and not has_list:
            continue
        if t == "stat" and not has_stat:
            continue
        valid.append(t)
    if not valid:
        valid = (["checklist"] if has_list else
                 ["stat"] if has_stat else ["statement"])

    # Bright accounts (most consumer/service brands) avoid the dark, navy
    # "premium tech" templates and stick to clean light/photo layouts.
    if _style() == "bright":
        BRIGHT_OK = {"light-statement", "checklist", "bold-color", "editorial"}
        vb = [t for t in valid if t in BRIGHT_OK]
        if not vb:
            vb = (["checklist"] if has_list else []) + ["light-statement", "bold-color"]
            if photo_path:
                vb.append("editorial")
        valid = vb

    fresh = [t for t in valid if t not in avoid]
    t = rng.choice(fresh or valid)

    if t == "statement":
        return render_hero(post_text, out_path, kicker, headline, accent, seed)
    if t == "light-statement":
        return render_light_statement(post_text, out_path, kicker, headline, accent, seed)
    if t == "bold-color":
        return render_bold_color(post_text, out_path, kicker, headline, accent, seed)
    if t == "stat":
        return render_stat(post_text, out_path, kicker, headline, accent, seed)
    if t == "checklist":
        return render_checklist(post_text, out_path, kicker, headline, accent, seed)
    if t == "two-block":
        return render_two_block(post_text, out_path, kicker, headline, accent, seed)
    if t == "quote":
        return render_quote(post_text, out_path, kicker, headline, accent, seed)
    if t == "editorial":
        return render_editorial(post_text, out_path, kicker, headline, accent, photo_path, seed)
    if t == "band":
        return render_band(post_text, out_path, kicker, headline, accent, photo_path, seed)
    render_landscape_card(post_text, out_path, kicker=kicker, photo_path=photo_path,
                          accent=accent, headline=headline, seed=seed, layout=t)
    return t


def render_landscape_card(
    post_text: str,
    out_path: str | Path,
    kicker: str = "",
    photo_path: str | Path | None = None,
    accent=None,
    headline: str = "",
    seed: int | None = None,
    layout: str | None = None,
) -> Path:
    """Landscape (1200x630) card. Two art-directed layouts, chosen at random:
    'overlay' (photo full-bleed, brand-graded, text on a left scrim) and
    'split' (editorial: navy text panel + brand-graded photo). Both grade the
    photo to the brand palette so the feed reads like a campaign."""
    rng = random.Random(seed)
    if accent is None:
        accent = _accent_pick(rng)
    paragraphs = [p for p in _clean_body_for_image(post_text).split("\n") if p.strip()]
    headline = (headline or "").strip()
    footer_y = _LH - 84
    if layout is None:
        layout = rng.choice(LANDSCAPE_LAYOUTS) if photo_path else "overlay"

    if layout == "split" and photo_path:
        panel_w = 556
        img = _premium_bg(accent, (seed or 0))
        photo = _brand_tone(
            ImageOps.fit(Image.open(photo_path).convert("RGB"),
                         (_LW - panel_w, _LH), method=Image.LANCZOS), accent
        ).convert("RGBA")
        img.alpha_composite(photo, (panel_w, 0))
        # faint arrow-mark watermark in the panel
        try:
            mk = _logo_mark_image()
            th = 380
            mk = mk.resize((int(mk.width * th / mk.height), th))
            a = mk.split()[3].point(lambda p: int(p * 0.06))
            mk.putalpha(a)
            img.alpha_composite(mk, (-70, _LH - mk.height + 50))
        except Exception:
            pass
        # soft navy fade from the panel into the photo, plus an accent seam
        fade = Image.new("RGBA", (170, _LH), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fade)
        for xx in range(170):
            a = int(205 * (1 - xx / 169) ** 1.5)
            fd.line([(xx, 0), (xx, _LH)], fill=(*BG_BOTTOM, a))
        img.alpha_composite(fade, (panel_w, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([panel_w - 4, 0, panel_w, _LH], fill=accent)
        _draw_text_block(draw, 56, panel_w - 112, 56, footer_y - 12,
                         kicker, headline, paragraphs, accent, head_max=46)
        _place_logo_full(img, 56, footer_y, 48)
    else:
        if photo_path:
            img = _brand_tone(
                ImageOps.fit(Image.open(photo_path).convert("RGB"),
                             (_LW, _LH), method=Image.LANCZOS), accent
            ).convert("RGBA")
            img.alpha_composite(_landscape_overlay())
            _grain(img, 7)
        else:
            img = _premium_bg(accent, (seed or 0))
        draw = ImageDraw.Draw(img)
        _draw_text_block(draw, 72, 660, 58, footer_y - 12,
                         kicker, headline, paragraphs, accent)
        _place_logo_full(img, 72, footer_y, 52)
        f_domain = _font("Rajdhani-SemiBold.ttf", 24)
        dom_w = draw.textlength(_domain(), font=f_domain)
        draw.text((_LW - 72 - dom_w, footer_y + 52 // 2 - 13), _domain(),
                  font=f_domain, fill=(216, 226, 238))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return out_path


def _layout_body(draw, paragraphs, text_w, f_body, f_strong, accent, lh, gap):
    """Lay out the body with hierarchy: numbered lists get an accent number and
    a hanging indent; "Label:" prefixes get their own accent line. Returns
    (items, total_height) where each item is ('line', segments) or ('gap', h).
    A segment is (text, font, fill, dx)."""
    items: list = []
    total = 0
    for pi, para in enumerate(paragraphs):
        m_list = _LIST_RE.match(para)
        m_label = _LABEL_RE.match(para)
        if m_list:
            num, rest = m_list.group(1), m_list.group(2)
            numstr = f"{num}."
            indent = int(draw.textlength(numstr + "  ", font=f_strong))
            for li, ln in enumerate(_wrap(draw, rest, f_body, text_w - indent)):
                segs = []
                if li == 0:
                    segs.append((numstr, f_strong, accent, 0))
                segs.append((ln, f_body, WHITE, indent))
                items.append(("line", segs))
                total += lh
        elif m_label:
            label, rest = m_label.group(1), m_label.group(2)
            items.append(("line", [(label.upper() + ":", f_strong, accent, 0)]))
            total += lh
            for ln in _wrap(draw, rest, f_body, text_w):
                items.append(("line", [(ln, f_body, WHITE, 0)]))
                total += lh
        else:
            for ln in _wrap(draw, para, f_body, text_w):
                items.append(("line", [(ln, f_body, WHITE, 0)]))
                total += lh
        if pi < len(paragraphs) - 1:
            items.append(("gap", gap))
            total += gap
    return items, total


FULLTEXT_VARIANTS = ["banner-top", "banner-bottom"]
# Square (1:1). Facebook shows square images edge-to-edge in the feed with no
# side bars (a 4:5 portrait gets a blurred side-fill on desktop), and it looks
# consistent in the Page photo grid.
_FW, _FH = 1080, 1080


def _portrait_gradient() -> Image.Image:
    img = Image.new("RGB", (_FW, _FH), BG_BOTTOM)
    d = ImageDraw.Draw(img)
    for y in range(_FH):
        t = (y / (_FH - 1)) ** 0.9
        d.line([(0, y), (_FW, y)], fill=tuple(
            round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        ))
    return img.convert("RGBA")


def _paste_banner(img: Image.Image, photo_path, top: int, height: int, fade: str) -> None:
    """Paste a photo band and fade it into the navy panel on the `fade` side
    ('down' = darken toward the bottom, 'up' = darken toward the top)."""
    photo = ImageOps.fit(
        Image.open(photo_path).convert("RGB"), (_FW, height), method=Image.LANCZOS
    ).convert("RGBA")
    grad = Image.new("RGBA", (_FW, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(height):
        f = (y / (height - 1)) if fade == "down" else (1 - y / (height - 1))
        gd.line([(0, y), (_FW, y)], fill=(*BG_TOP, int(255 * (f ** 1.6) * 0.95)))
    photo.alpha_composite(grad)
    img.alpha_composite(photo, (0, top))


def render_fulltext_card(
    post_text: str,
    out_path: str | Path,
    kicker: str = "",
    photo_path: str | Path | None = None,
    accent=None,
    variant: str | None = None,
    seed: int | None = None,
    headline: str = "",
) -> Path:
    """Render the FULL post copy onto a tall portrait card (1080x1350).

    Layout (`variant`) and accent color (green/blue) are randomized when not
    given, so consecutive posts vary visually. The body text is auto-sized to
    fit the available panel.
    """
    rng = random.Random(seed)
    if accent is None:
        accent = _accent_pick(rng)
    if variant is None:
        variant = rng.choice(FULLTEXT_VARIANTS) if photo_path else "branded"

    margin = 76
    banner_h = 320
    img = _portrait_gradient()

    if photo_path and variant == "banner-top":
        _paste_banner(img, photo_path, 0, banner_h, fade="down")
        text_top, text_bottom = banner_h + 52, _FH - 150
        footer_y = _FH - 100
    elif photo_path and variant == "banner-bottom":
        _paste_banner(img, photo_path, _FH - banner_h, banner_h, fade="up")
        footer_y = _FH - banner_h - 96
        text_top, text_bottom = 96, footer_y - 26
    else:  # branded (no photo) -- clean navy panel
        variant = "branded"
        text_top, text_bottom = 150, _FH - 150
        footer_y = _FH - 100

    draw = ImageDraw.Draw(img)
    headline = (headline or "").strip()
    paragraphs = [p for p in _clean_body_for_image(post_text).split("\n") if p.strip()]
    text_w = _FW - 2 * margin
    avail_h = text_bottom - text_top
    f_kicker = _font("Rajdhani-SemiBold.ttf", 30)
    kicker_h = 56

    # Headline: Rajdhani Bold, shrink so it fits in at most 2 lines.
    head_lines: list[str] = []
    f_head = None
    head_lh = 0
    if headline:
        for hs in range(56, 35, -3):
            f_head = _font("Rajdhani-Bold.ttf", hs)
            head_lines = _wrap(draw, headline.upper(), f_head, text_w)
            head_lh = int(hs * 1.05)
            if len(head_lines) <= 2:
                break
    head_block = (len(head_lines) * head_lh + 30) if head_lines else 0  # + divider

    # Body auto-fit in the remaining space, with hierarchy (lists/labels).
    body_avail = avail_h - kicker_h - head_block
    lh, gap, items = 22, 12, []
    for size in range(34, 19, -2):
        cand = _font("NunitoSans.ttf", size)
        strong = _font("Rajdhani-Bold.ttf", size + 3)
        clh, cgap = int(size * 1.42), int(size * 0.85)
        cand_items, total = _layout_body(draw, paragraphs, text_w, cand, strong, accent, clh, cgap)
        lh, gap, items = clh, cgap, cand_items
        if total <= body_avail:
            break

    # Kicker.
    y = text_top
    draw.rectangle([margin, y + 12, margin + 46, y + 16], fill=accent)
    _draw_tracked(draw, (margin + 64, y), kicker.upper(), f_kicker, accent, 5)
    y += kicker_h

    # Headline + accent divider.
    if head_lines and f_head is not None:
        for ln in head_lines:
            draw.text((margin, y), ln, font=f_head, fill=WHITE)
            y += head_lh
        draw.rectangle([margin, y + 8, margin + 72, y + 12], fill=accent)
        y += 30

    # Body with hierarchy. Hard stop so text never reaches the logo row.
    body_limit = footer_y - 14
    for kind, payload in items:
        if kind == "gap":
            y += payload
        else:
            if y + lh > body_limit:
                break
            for (seg_text, seg_font, seg_fill, dx) in payload:
                draw.text((margin + dx, y), seg_text, font=seg_font, fill=seg_fill)
            y += lh

    logo_h = 60
    _place_logo_full(img, margin, footer_y, logo_h)
    f_domain = _font("Rajdhani-SemiBold.ttf", 26)
    dom_w = draw.textlength(_domain(), font=f_domain)
    draw.text((_FW - margin - dom_w, footer_y + logo_h // 2 - 14), _domain(),
              font=f_domain, fill=MUTED)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    # Render one sample of every style into a single contact sheet for review.
    samples = [
        ("Phishing Red Flags Your Team Should Know", "Three quick tells that "
         "stop a breach before it starts.", "Threat of the Week", "Phishing"),
        ("U.S. Dedication. European Depth.", "Austin plus Germany means someone "
         "is always awake. Genuine 24/7 support.", "Follow the Sun", "European Depth"),
        ("Enterprise Security for Small Business", "Robust protection without "
         "the enterprise price tag. On purpose.", "Our Mission", "Small Business"),
        ("CJIS-Compliant Body Cam Storage", "Audit-ready, ransomware-resilient "
         "storage for public safety.", "Public Safety", "Body Cam"),
        ("Backups Are Not a Recovery Plan", "If you have never tested a restore, "
         "you do not have a backup.", "Backup & DR", "Recovery"),
    ]
    out_dir = _ASSETS / "style_previews"
    rows = []
    for name, (hl, sub, kick, high) in zip(STYLE_NAMES, samples):
        p, _ = render_card(hl, sub, out_dir / f"{name}.png", kicker=kick,
                           highlight=high, style=name)
        rows.append(Image.open(p))
        print("rendered", name)
    # Stack into one tall contact sheet.
    sheet = Image.new("RGB", (W, H * len(rows) + 20 * (len(rows) - 1)), (5, 8, 13))
    yy = 0
    for r in rows:
        sheet.paste(r, (0, yy))
        yy += H + 20
    sheet.save(_ASSETS / "style_previews" / "_contact_sheet.png")
    print("wrote contact sheet")
