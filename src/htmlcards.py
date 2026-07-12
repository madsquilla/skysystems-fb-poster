"""Build self-contained HTML/CSS post graphics, themed per brand + design.

Each account has a design system (font + shapes + motif) and brand colors; each
post picks one of many layouts. We assemble an HTML document and hand it to
htmlrender to screenshot at 1080x1080. HTML/CSS gives real typography and
layout, so cards look professionally designed rather than pixel-drawn.

Public API:
    render_card(item, out_path, photo_path=None, avoid=None) -> layout_name
"""

from __future__ import annotations

import base64
import colorsys
import html as _html
import logging
import random
import re
from pathlib import Path

import tenants

logger = logging.getLogger("plungepost.htmlcards")

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_FONT_DIR = _ASSETS / "fonts"


def _furl(name: str) -> str:
    return (_FONT_DIR / name).resolve().as_uri()


# --- colors ------------------------------------------------------------------
def _to_rgb(h):
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, IndexError):
        return (46, 204, 113)


def _hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c))) for c in rgb))


def _ui_accent(hexc):
    """Vivid, readable brand accent (pale/grey inputs get saturated/darkened)."""
    r, g, b = (x / 255 for x in _to_rgb(hexc))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.12:
        v = min(v, 0.34)
    else:
        s = max(s, 0.5)
        v = min(max(v, 0.42), 0.74)
    return _hex(tuple(int(x * 255) for x in colorsys.hsv_to_rgb(h, s, v)))


def _darken(hexc, f=0.72):
    return _hex(tuple(int(c * f) for c in _to_rgb(hexc)))


def _lighten(hexc, f=0.5):
    return _hex(tuple(int(c + (255 - c) * f) for c in _to_rgb(hexc)))


def _mix(hexc, other, f):
    a, b = _to_rgb(hexc), _to_rgb(other)
    return _hex(tuple(a[i] + (b[i] - a[i]) * f for i in range(3)))


def _luma(hexc):
    r, g, b = _to_rgb(hexc)
    return 0.299 * r + 0.587 * g + 0.114 * b


# --- design systems ----------------------------------------------------------
# family label -> (font file, css weight). Variable fonts accept any weight.
_FONTS = {
    "quicksand": ("Quicksand.ttf", 700),
    "baloo": ("Baloo2.ttf", 700),
    "fraunces": ("Fraunces.ttf", 600),
    "anton": ("Anton.ttf", 400),
    "grotesk": ("SpaceGrotesk.ttf", 600),
    "rajdhani": ("Rajdhani-Bold.ttf", 700),
    "nunito": ("NunitoSans.ttf", 400),
}

_DESIGNS = {
    "soft-rounded": dict(head="quicksand", hw=700, body="nunito", case="none",
                         radius=30, kicker="pill", motif="blobs", tracking="0",
                         mood="bright"),
    "friendly-round": dict(head="baloo", hw=700, body="nunito", case="none",
                           radius=34, kicker="pill", motif="blobs", tracking="0",
                           mood="bright"),
    "elegant-serif": dict(head="fraunces", hw=600, body="nunito", case="none",
                          radius=8, kicker="plain", motif="none", tracking=".16em",
                          mood="bright"),
    "bold-impact": dict(head="anton", hw=400, body="grotesk", case="upper",
                        radius=4, kicker="tab", motif="stripe", tracking=".01em",
                        mood="bright"),
    "modern-grotesk": dict(head="grotesk", hw=700, body="nunito", case="none",
                           radius=10, kicker="underline", motif="none",
                           tracking="0", mood="bright"),
    "tech-condensed": dict(head="rajdhani", hw=700, body="nunito", case="upper",
                           radius=3, kicker="tab", motif="grid", tracking=".04em",
                           mood="dark"),
}
_BRIGHT = ["soft-rounded", "friendly-round", "elegant-serif", "bold-impact",
           "modern-grotesk"]

# Layout pool per design (text + photo + list). Photo layouts are used only when
# a photo is available; checklist/stat only when the copy has a list/number.
_BASE = ["hero", "centered", "corner", "frame", "bold-color", "quote",
         "photo-full", "photo-top", "photo-side", "photo-card", "checklist", "stat"]
_POOLS = {
    "soft-rounded": ["hero", "photo-card", "centered", "photo-top"] + _BASE,
    "friendly-round": ["centered", "photo-card", "hero", "frame"] + _BASE,
    "elegant-serif": ["frame", "photo-side", "quote", "centered"] + _BASE,
    "bold-impact": ["bold-color", "photo-full", "corner", "hero"] + _BASE,
    "modern-grotesk": ["corner", "photo-side", "hero", "frame"] + _BASE,
    "tech-condensed": ["hero", "photo-full", "bold-color", "corner", "stat", "quote"],
}


def _design_id():
    try:
        acct = tenants.account()
    except Exception:
        acct = {}
    d = acct.get("design")
    if d in _DESIGNS:
        return d
    if tenants.style() == "dark":
        return "tech-condensed"
    slug = tenants.current()
    return _BRIGHT[sum(ord(c) for c in slug) % len(_BRIGHT)]


# --- assets ------------------------------------------------------------------
def _logo_data_uri():
    p = tenants.logo_full()
    if p.exists():
        b = p.read_bytes()
        return "data:image/png;base64," + base64.b64encode(b).decode()
    return None


def _photo_uri(photo_path):
    if not photo_path:
        return None
    p = Path(photo_path)
    if not p.exists():
        return None
    return p.resolve().as_uri()


# --- text helpers ------------------------------------------------------------
_LIST_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
_NUM_RE = re.compile(r"(\d[\d,]*\.?\d*\s?%|\$\d[\d,]*|\d+x|\d+\+|\d+(?:st|nd|rd|th)?)")
_SENT_RE = re.compile(r"(.{18,150}?[.!?])(?:\s|$)")


def _esc(s):
    return _html.escape((s or "").strip())


def _lead(text):
    t = " ".join((text or "").split())
    m = _SENT_RE.match(t)
    return (m.group(1) if m else t[:150]).strip()


def _list_items(text):
    out = []
    for ln in (text or "").splitlines():
        m = _LIST_RE.match(ln.strip())
        if m:
            out.append(m.group(2).strip())
    return out


def _first_stat(text, headline):
    for s in (headline or "", text or ""):
        m = _NUM_RE.search(s)
        if m:
            return m.group(1).strip()
    return None


# --- CSS ---------------------------------------------------------------------
def _fontfaces():
    faces = []
    for label, (fname, _w) in _FONTS.items():
        faces.append(
            "@font-face{font-family:'%s';src:url('%s') format('truetype');"
            "font-weight:100 1000;font-display:block;}" % (label, _furl(fname)))
    return "".join(faces)


def _theme(design, accent, accent2, mood):
    d = _DESIGNS[design]
    head_fam = d["head"]
    body_fam = d["body"]
    ink = "#16232f"
    paper = "#f5f7fa"
    sub = "#5a6b7a"
    if mood == "dark":
        ink = "#f3f7fc"
        paper = "#0d1826"
        sub = "#9fb2c6"
    vars_ = {
        "ACCENT": accent, "ACCENT2": accent2,
        "ACC_DK": _darken(accent, 0.72), "ACC_LT": _lighten(accent, 0.86),
        "INK": ink, "PAPER": paper, "SUB": sub,
        "HEAD": head_fam, "BODY": body_fam,
        "HW": str(d["hw"]), "RADIUS": str(d["radius"]) + "px",
        "TRACK": d["tracking"],
        "TT": "uppercase" if d["case"] == "upper" else "none",
    }
    css = _BASE_CSS
    for k, v in vars_.items():
        css = css.replace("{" + k + "}", v)
    return css, d


_BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:1080px;height:1080px;}
#card{width:1080px;height:1080px;background:{PAPER};color:{INK};position:relative;
  overflow:hidden;font-family:'{BODY}',sans-serif;-webkit-font-smoothing:antialiased;}
.pad{position:absolute;inset:0;padding:104px 96px 96px;display:flex;flex-direction:column;}
.center-v{justify-content:center;}
.head{font-family:'{HEAD}';font-weight:{HW};color:{INK};line-height:1.04;
  letter-spacing:-.005em;text-transform:{TT};}
.kicker{align-self:flex-start;font-family:'{HEAD}';font-weight:600;font-size:24px;
  letter-spacing:.14em;text-transform:uppercase;margin-bottom:26px;}
.k-pill{background:{ACCENT};color:#fff;padding:12px 24px;border-radius:999px;}
.k-tab{background:{ACCENT};color:#fff;padding:11px 20px;}
.k-plain{color:{ACCENT};}
.k-underline{color:{ACCENT};border-bottom:4px solid {ACCENT};padding-bottom:6px;}
.rule{width:96px;height:8px;background:{ACCENT};border-radius:4px;margin:30px 0 26px;}
.sub{font-size:30px;line-height:1.5;color:{SUB};max-width:80%;}
.footer{position:absolute;left:96px;right:96px;bottom:64px;display:flex;
  align-items:center;justify-content:space-between;gap:20px;}
.logo{height:78px;max-width:56%;object-fit:contain;object-position:left center;}
.logo-wm{font-family:'{HEAD}';font-weight:{HW};font-size:34px;color:{ACCENT};}
.dom{font-size:24px;color:{SUB};opacity:.85;white-space:nowrap;}
.blob{position:absolute;border-radius:50%;pointer-events:none;}
.stripe{position:absolute;top:0;bottom:0;width:340px;right:-40px;transform:skewX(-12deg);
  background:{ACCENT};opacity:.08;}
.photo{position:absolute;inset:0;background-size:cover;background-position:center;}
.scrim{position:absolute;inset:0;background:linear-gradient(to top,
  rgba(9,16,26,.92) 0%, rgba(9,16,26,.55) 32%, rgba(9,16,26,0) 62%);}
.on-photo{color:#fff;}
.on-photo .head{color:#fff;text-shadow:0 2px 18px rgba(0,0,0,.35);}
.on-photo .sub{color:#e7edf3;max-width:86%;}
.on-photo .dom{color:#dfe8f0;}
.on-photo .logo{filter:brightness(0) invert(1);}
.numlist{margin-top:14px;display:flex;flex-direction:column;gap:20px;}
.numrow{display:flex;align-items:flex-start;gap:22px;}
.numbadge{flex:0 0 auto;width:52px;height:52px;border-radius:50%;background:{ACCENT};
  color:#fff;font-family:'{HEAD}';font-weight:700;font-size:26px;display:flex;
  align-items:center;justify-content:center;}
.numtext{font-size:29px;line-height:1.35;color:{INK};padding-top:6px;}
.statnum{font-family:'{HEAD}';font-weight:{HW};color:{ACCENT};font-size:280px;
  line-height:.9;letter-spacing:-.02em;}
.card-panel{position:absolute;left:64px;right:64px;bottom:64px;background:{PAPER};
  border-radius:26px;padding:56px 60px 48px;box-shadow:0 30px 80px rgba(9,20,34,.28);}
.frame-bg{position:absolute;inset:0;background:{ACC_LT};}
.frame-card{position:absolute;inset:60px;border-radius:26px;background:{PAPER};
  border:2px solid {ACCENT};display:flex;flex-direction:column;justify-content:center;
  padding:0 76px;}
"""


# --- kicker/footer fragments -------------------------------------------------
def _kicker_html(d, kicker):
    cls = {"pill": "k-pill", "tab": "k-tab", "plain": "k-plain",
           "underline": "k-underline"}.get(d["kicker"], "k-pill")
    return f'<div class="kicker {cls}">{_esc(kicker)}</div>' if kicker else ""


def _footer_html(logo_uri, domain, wm_name):
    if logo_uri:
        left = f'<img class="logo" src="{logo_uri}">'
    else:
        left = f'<div class="logo-wm">{_esc(wm_name)}</div>'
    return f'<div class="footer">{left}<div class="dom">{_esc(domain)}</div></div>'


def _blobs(accent, accent2):
    return (f'<div class="blob" style="width:520px;height:520px;top:-150px;'
            f'right:-120px;background:{accent};opacity:.09;"></div>'
            f'<div class="blob" style="width:360px;height:360px;bottom:-140px;'
            f'left:-130px;background:{accent2};opacity:.08;"></div>')


def _motif_html(d, accent, accent2):
    if d["motif"] == "blobs":
        return _blobs(accent, accent2)
    if d["motif"] == "stripe":
        return '<div class="stripe"></div>'
    return ""


# --- layouts (return inner #card HTML) --------------------------------------
def _hero(ctx):
    return f"""<div class="pad center-v">
      {ctx['motif']}{_kicker_html(ctx['d'], ctx['kicker'])}
      <div class="head" style="font-size:{ctx['hsize']}px">{ctx['headline']}</div>
      <div class="rule"></div>
      <div class="sub">{ctx['lead']}</div>
      {ctx['footer']}</div>"""


def _centered(ctx):
    return f"""<div class="pad center-v" style="align-items:center;text-align:center;">
      {ctx['motif']}
      <div class="kicker {_kcls(ctx['d'])}">{_esc(ctx['kicker'])}</div>
      <div class="head" style="font-size:{ctx['hsize']}px">{ctx['headline']}</div>
      <div class="rule" style="margin-left:auto;margin-right:auto;"></div>
      <div class="sub" style="max-width:82%;">{ctx['lead']}</div>
      {ctx['footer']}</div>"""


def _corner(ctx):
    return f"""<div class="pad" style="justify-content:flex-end;padding-bottom:210px;">
      {ctx['motif']}
      <div style="position:absolute;top:104px;left:96px;">{_kicker_html(ctx['d'], ctx['kicker'])}</div>
      <div class="head" style="font-size:{ctx['hsize']}px">{ctx['headline']}</div>
      <div class="rule"></div>
      <div class="sub">{ctx['lead']}</div>
      {ctx['footer']}</div>"""


def _frame(ctx):
    return f"""<div class="frame-bg"></div>
      <div class="frame-card">
        {_kicker_html(ctx['d'], ctx['kicker'])}
        <div class="head" style="font-size:{min(ctx['hsize'],80)}px">{ctx['headline']}</div>
        <div class="rule"></div>
        <div class="sub" style="max-width:92%;">{ctx['lead']}</div>
      </div>
      <div class="footer">{ctx['footer_inner']}</div>"""


def _bold_color(ctx):
    a = ctx['accent']
    return f"""<div id="paint" style="position:absolute;inset:0;
        background:linear-gradient(150deg,{a},{_darken(a,0.72)});"></div>
      <div class="pad center-v on-photo" style="color:#fff;">
        <div class="kicker" style="color:#fff;letter-spacing:.14em;">{_esc(ctx['kicker'])}</div>
        <div class="head" style="font-size:{ctx['hsize']}px;color:#fff;">{ctx['headline']}</div>
        <div class="rule" style="background:#fff;"></div>
        <div class="sub" style="color:#eef4fa;">{ctx['lead']}</div>
        {ctx['footer']}</div>"""


def _quote(ctx):
    return f"""<div class="pad center-v">
      {ctx['motif']}
      <div class="head" style="font-size:200px;line-height:.6;color:{_lighten(ctx['accent'],0.5)};
        height:120px;">&ldquo;</div>
      <div class="head" style="font-size:{min(ctx['hsize'],78)}px;">{ctx['headline']}</div>
      <div class="rule"></div>
      <div class="kicker {_kcls(ctx['d'])}" style="margin-top:6px;">{_esc(ctx['kicker'])}</div>
      {ctx['footer']}</div>"""


def _stat(ctx):
    stat = ctx['stat'] or ""
    return f"""<div class="pad center-v">
      {ctx['motif']}{_kicker_html(ctx['d'], ctx['kicker'])}
      <div class="statnum">{_esc(stat)}</div>
      <div class="head" style="font-size:60px;margin-top:18px;">{ctx['headline']}</div>
      <div class="sub" style="margin-top:20px;">{ctx['lead']}</div>
      {ctx['footer']}</div>"""


def _checklist(ctx):
    rows = "".join(
        f'<div class="numrow"><div class="numbadge">{i}</div>'
        f'<div class="numtext">{_esc(it)}</div></div>'
        for i, it in enumerate(ctx['items'][:4], 1))
    return f"""<div class="pad center-v">
      {ctx['motif']}{_kicker_html(ctx['d'], ctx['kicker'])}
      <div class="head" style="font-size:56px;">{ctx['headline']}</div>
      <div class="numlist">{rows}</div>
      {ctx['footer']}</div>"""


def _photo_full(ctx):
    return f"""<div class="photo" style="background-image:url('{ctx['photo']}');"></div>
      <div class="scrim"></div>
      <div class="pad on-photo" style="justify-content:flex-end;padding-bottom:200px;">
        {_kicker_html(ctx['d'], ctx['kicker'])}
        <div class="head" style="font-size:{ctx['hsize']}px;">{ctx['headline']}</div>
        <div class="sub" style="margin-top:22px;">{ctx['lead']}</div>
      </div>
      <div class="footer on-photo-f">{ctx['footer_photo']}</div>"""


def _photo_top(ctx):
    return f"""<div class="photo" style="height:560px;background-image:url('{ctx['photo']}');"></div>
      <div class="pad center-v" style="top:560px;bottom:0;inset:auto;position:absolute;
        left:0;right:0;padding:56px 96px 96px;">
        {_kicker_html(ctx['d'], ctx['kicker'])}
        <div class="head" style="font-size:{min(ctx['hsize'],72)}px;">{ctx['headline']}</div>
        <div class="rule"></div>
        <div class="sub">{ctx['lead']}</div>
      </div>
      <div class="footer">{ctx['footer_inner']}</div>"""


def _photo_side(ctx):
    return f"""<div class="photo" style="width:540px;background-image:url('{ctx['photo']}');"></div>
      <div style="position:absolute;left:540px;right:0;top:0;bottom:0;padding:110px 70px;
        display:flex;flex-direction:column;justify-content:center;">
        {_kicker_html(ctx['d'], ctx['kicker'])}
        <div class="head" style="font-size:{min(ctx['hsize'],62)}px;">{ctx['headline']}</div>
        <div class="rule"></div>
        <div class="sub" style="max-width:100%;">{ctx['lead']}</div>
      </div>
      <div class="footer" style="left:600px;">{ctx['footer_inner']}</div>"""


def _photo_card(ctx):
    return f"""<div class="photo" style="background-image:url('{ctx['photo']}');"></div>
      <div class="card-panel">
        {_kicker_html(ctx['d'], ctx['kicker'])}
        <div class="head" style="font-size:{min(ctx['hsize'],58)}px;">{ctx['headline']}</div>
        <div class="rule"></div>
        <div class="sub" style="max-width:100%;">{ctx['lead']}</div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:36px;">
          {ctx['footer_inner']}
        </div>
      </div>"""


_LAYOUTS = {
    "hero": _hero, "centered": _centered, "corner": _corner, "frame": _frame,
    "bold-color": _bold_color, "quote": _quote, "stat": _stat,
    "checklist": _checklist, "photo-full": _photo_full, "photo-top": _photo_top,
    "photo-side": _photo_side, "photo-card": _photo_card,
}
_PHOTO_LAYOUTS = {"photo-full", "photo-top", "photo-side", "photo-card"}


def _kcls(d):
    return {"pill": "k-pill", "tab": "k-tab", "plain": "k-plain",
            "underline": "k-underline"}.get(d["kicker"], "k-pill")


def _hsize(headline_text):
    n = len(headline_text)
    if n <= 16:
        return 104
    if n <= 26:
        return 92
    if n <= 40:
        return 78
    return 66


# --- entry point -------------------------------------------------------------
def render_card(item, out_path, photo_path=None, avoid=None, seed=None):
    import htmlrender

    design = _design_id()
    d = _DESIGNS[design]
    acct = tenants.account()
    accent = _ui_accent(acct.get("accent") or "#2ecc71")
    accent2 = _ui_accent(acct.get("accent2") or "#2b6cc4")
    mood = d["mood"]
    domain = tenants.domain()
    logo_uri = _logo_data_uri()
    wm = (tenants.account().get("name") or "").strip()
    photo = _photo_uri(photo_path)

    headline = _esc(item.get("image_headline") or "")
    kicker = item.get("image_kicker") or ""
    lead = _esc(_lead(item.get("post_text", "")))
    items = _list_items(item.get("post_text", ""))
    stat = _first_stat(item.get("post_text", ""), item.get("image_headline", ""))

    rng = random.Random(seed)
    pool = []
    seen = set()
    for t in _POOLS.get(design, _BASE):
        if t in seen:
            continue
        seen.add(t)
        if t in _PHOTO_LAYOUTS and not photo:
            continue
        if t == "checklist" and not items:
            continue
        if t == "stat" and not stat:
            continue
        pool.append(t)
    if not pool:
        pool = ["hero"]
    fresh = [t for t in pool if t not in (avoid or set())]
    layout = rng.choice(fresh or pool)

    theme_css, d = _theme(design, accent, accent2, mood)
    footer = _footer_html(logo_uri, domain, wm)
    footer_inner = ((f'<img class="logo" src="{logo_uri}">' if logo_uri
                     else f'<div class="logo-wm">{_esc(wm)}</div>')
                    + f'<div class="dom">{_esc(domain)}</div>')
    footer_photo = (('<img class="logo" src="%s">' % logo_uri if logo_uri
                     else '<div class="logo-wm" style="color:#fff;">%s</div>' % _esc(wm))
                    + '<div class="dom">%s</div>' % _esc(domain))

    ctx = dict(d=d, kicker=kicker, headline=headline, lead=lead, items=items,
               stat=stat, accent=accent, accent2=accent2, photo=photo,
               footer=footer, footer_inner=footer_inner, footer_photo=footer_photo,
               motif=_motif_html(d, accent, accent2), hsize=_hsize(headline))

    inner = _LAYOUTS[layout](ctx)
    doc = ("<!doctype html><html><head><meta charset='utf-8'><style>"
           + _fontfaces() + theme_css + "</style></head><body><div id='card'>"
           + inner + "</div></body></html>")
    htmlrender.render_html_to_png(doc, out_path)
    logger.info("Rendered %s card (design=%s) -> %s", layout, design, out_path)
    return layout
