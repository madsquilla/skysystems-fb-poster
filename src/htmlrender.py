"""Render an HTML document to a PNG using headless Chromium (Playwright).

This is the rendering backend for the post graphics: we build a self-contained
HTML/CSS document (see htmlcards.py) and screenshot it at 1080x1080. HTML/CSS
gives professional typography and layout that pixel-drawing cannot match.

The browser is launched per render for simplicity/robustness. Fonts are loaded
via @font-face (file:// URLs) and we wait for document.fonts.ready before the
screenshot so text never renders in a fallback face.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("plungepost.htmlrender")

_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--force-color-profile=srgb"]


def render_html_to_png(html: str, out_path: str | Path, size: int = 1080) -> Path:
    """Render `html` to a `size`x`size` PNG at out_path. Raises on failure.

    The HTML is written to a temp .html file and opened via file:// so that
    local @font-face fonts and background images actually load (Chromium blocks
    file:// sub-resources when a page is loaded via set_content/about:blank).
    """
    from playwright.sync_api import sync_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = out_path.with_suffix(".render.html")
    tmp_html.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=_LAUNCH_ARGS)
            try:
                page = browser.new_page(
                    viewport={"width": size, "height": size}, device_scale_factor=2)
                page.goto(tmp_html.resolve().as_uri(), wait_until="load")
                try:
                    page.evaluate("async () => { await document.fonts.ready; }")
                except Exception:
                    pass
                el = page.query_selector("#card") or page
                el.screenshot(path=str(out_path))
            finally:
                browser.close()
    finally:
        try:
            tmp_html.unlink()
        except OSError:
            pass
    return out_path
