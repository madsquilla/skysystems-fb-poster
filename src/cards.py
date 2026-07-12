"""Shared card-building: fetch a topic photo and render the branded landscape
card for an item. Used by both the CLI (main.py) and the web dashboard
(webapp.py)."""

from __future__ import annotations

import logging
from pathlib import Path

import images
import imagecard
import store
import tenants

logger = logging.getLogger("plungepost.cards")

_REPO_ROOT = Path(__file__).resolve().parent.parent


def build_card(item: dict) -> dict:
    """Render the post's branded card. Sets item['card_path'] + ['image_style'].

    Never raises: a post must still go out even if imagery fails (it falls back
    to a text-only card, or no card at all on hard failure).
    """
    cards_dir = tenants.cards_dir()
    cards_dir.mkdir(parents=True, exist_ok=True)
    kicker = item.get("image_kicker") or ""
    query = item.get("image_query") or item.get("theme") or "professional business"
    out = cards_dir / f"{item['id']}.png"
    try:
        src_photo = cards_dir / f"_src_{item['id']}.jpg"
        photo = images.fetch_stock_photo(query, src_photo)
        # Avoid the few most-recently-used designs so the feed keeps rotating.
        try:
            recent = store.read_pending()[-3:] + list(reversed(store.read_history()))[:3]
            avoid = {r.get("image_style") for r in recent if r.get("image_style")}
        except Exception:
            avoid = set()
        # Renderer choice by account style:
        #  - 'dark' (SkySystems / tech-condensed brands) keeps the original,
        #    dialed-in Pillow "premium" renderer -- do not change what works.
        #  - 'bright' (consumer/service brands) uses the new HTML/CSS engine.
        # HTML failures fall back to Pillow so a post is never blocked.
        use_html = tenants.style() != "dark"
        rendered = False
        if use_html:
            try:
                import htmlcards
                item["image_style"] = htmlcards.render_card(
                    item, out, photo_path=photo, avoid=avoid)
                rendered = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("HTML renderer unavailable (%s); using Pillow.", exc)
        if not rendered:
            item["image_style"] = imagecard.render_post_graphic(
                item["post_text"], out, kicker=kicker,
                headline=item.get("image_headline", ""),
                format_id=item.get("format", ""), photo_path=photo, avoid=avoid,
            )
        if photo is not None:
            try:
                Path(photo).unlink()
            except OSError:
                pass
        item["card_path"] = str(out.relative_to(_REPO_ROOT)).replace("\\", "/")
        item["card_account"] = tenants.current()
        logger.info("Built card (%s) -> %s", item["image_style"], item["card_path"])
    except Exception as exc:  # noqa: BLE001 -- imagery must never block a post
        logger.warning("Card rendering failed (%s); post will be text-only.", exc)
        item["card_path"] = ""
        item["image_style"] = "none"
    return item
