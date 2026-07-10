"""Generate a post via the Anthropic API and parse it into a queue item.

Public API:
    generate_post(theme=None) -> item dict
    generate_batch(n) -> list[item dict]

A theme is chosen to avoid anything posted or queued within the dedup window.
The model is asked to return strict JSON; we parse it defensively (stripping
code fences) and never return a half-formed or empty post.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from typing import Any

import anthropic

from content import (
    BRAND,
    POST_FORMATS,
    POST_LENGTHS,
    build_system_prompt,
    load_themes,
)
from store import recent_topics

logger = logging.getLogger("skysystems.generate")

DEFAULT_MODEL = "claude-opus-4-8"
# Adaptive-thinking models can spend tokens on internal reasoning, so give the
# response room; the JSON output itself is small.
MAX_TOKENS = 2048
# Only used for models that still accept sampling params (e.g. Haiku/Sonnet).
TEMPERATURE = 1.0


def _uses_adaptive_thinking(model: str) -> bool:
    """Opus 4.7/4.8 and Fable/Mythos reject sampling params and use adaptive
    thinking + the effort parameter instead of temperature."""
    return any(tag in model for tag in ("opus-4-8", "opus-4-7", "fable-5", "mythos-5"))


class GenerationError(RuntimeError):
    """Raised when we cannot produce a valid, non-empty post."""


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)


def choose_theme(
    themes: list[dict[str, Any]],
    recent: list[dict[str, str]],
) -> dict[str, Any]:
    """Pick a theme whose id was not used recently; fall back gracefully."""
    recent_ids = {r.get("theme", "") for r in recent}
    fresh = [t for t in themes if t["id"] not in recent_ids]
    pool = fresh if fresh else themes
    if not fresh:
        logger.warning(
            "Every theme has been used within the dedup window; "
            "reusing the oldest-feeling option."
        )
    return random.choice(pool)


def choose_format(recent: list[dict[str, str]]) -> dict[str, Any]:
    """Pick a post format not used in the last few posts (avoids same rhythm)."""
    recent_fmts = [r.get("format", "") for r in recent if r.get("format")]
    # Only the most recent handful matter for "don't repeat the structure".
    avoid = set(recent_fmts[-5:])
    fresh = [f for f in POST_FORMATS if f["id"] not in avoid]
    return random.choice(fresh if fresh else POST_FORMATS)


def _build_user_prompt(
    theme: dict[str, Any],
    recent: list[dict[str, str]],
    fmt: dict[str, Any],
    length: dict[str, Any],
) -> str:
    vertical = theme.get("vertical")
    lines = [
        f"Write one Facebook post for theme id '{theme['id']}'.",
        f"Theme: {theme['description']}",
        f"Angle: {theme['angle']}",
    ]
    if vertical:
        lines.append(f"Target vertical for this post: {vertical}")
    lines.append(f"FORMAT for this post (follow it): {fmt['instruction']}")
    lines.append(f"LENGTH for this post: {length['instruction']}")
    link = theme.get("link") or BRAND["website"]
    lines.append(
        f"CAPTION LINK for this post (put this EXACT URL in the caption, do not "
        f"change it to the homepage): {link}"
    )

    if recent:
        recent_hooks = [
            r["post_text"][:140].replace("\n", " ").strip()
            for r in recent
            if r.get("post_text")
        ][:10]
        if recent_hooks:
            lines.append(
                "\nDo NOT repeat the theme, hook, or opening of any recent post "
                "below. Make this one feel fresh and distinct:"
            )
            lines.extend(f"- {hook}" for hook in recent_hooks)

    lines.append(
        "\nReturn ONLY the JSON object described in your instructions. "
        "No markdown, no code fences, no commentary."
    )
    return "\n".join(lines)


def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` style fences if the model added them."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line and a trailing fence line if present.
        stripped = re.sub(r"^```[a-zA-Z0-9]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _parse_response(raw_text: str) -> dict[str, Any]:
    """Parse the model output into a dict, defensively."""
    cleaned = _strip_code_fences(raw_text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Last resort: grab the first {...} block.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise GenerationError(
                f"Model did not return JSON. Raw output:\n{raw_text[:500]}"
            )
        data = json.loads(match.group(0))

    post_text = (data.get("post_text") or "").strip()
    if not post_text:
        raise GenerationError("Model returned an empty post_text.")

    def _s(key: str) -> str:
        return (data.get(key) or "").strip()

    # Caption carries the clickable link + hashtags; fall back to a sensible
    # default if the model omitted it.
    caption = _s("caption")
    if not caption:
        caption = f"{BRAND['website']}\n\n#Cybersecurity #ManagedIT #AustinTX"

    return {
        "post_text": post_text,
        "caption": caption,
        "theme": _s("theme"),
        "image_headline": _s("image_headline"),
        "image_kicker": _s("image_kicker") or "Austin, Texas",
        "image_query": _s("image_query"),
    }


def _make_item(parsed: dict[str, Any], theme_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    item_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{theme_id}"
    return {
        "id": item_id,
        "generated_at": now.isoformat(),
        "theme": parsed["theme"] or theme_id,
        "post_text": parsed["post_text"],
        "caption": parsed["caption"],
        "image_headline": parsed["image_headline"],
        "image_kicker": parsed["image_kicker"],
        "image_query": parsed["image_query"],
        "link": BRAND["website"],
        "status": "pending",
    }


def generate_post(
    theme: dict[str, Any] | None = None,
    fmt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a single post and return it as a queue item (status=pending).

    Raises GenerationError on empty/unparseable output, and lets the underlying
    anthropic.* exceptions propagate so main.py can exit non-zero.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise GenerationError(
            "ANTHROPIC_API_KEY is not set. Provide it via environment/.env."
        )

    themes = load_themes()
    recent = recent_topics()
    if theme is None:
        theme = choose_theme(themes, recent)
    if fmt is None:
        fmt = choose_format(recent)
    length = random.choice(POST_LENGTHS)

    logger.info(
        "Generating post for theme '%s' (format=%s, length=%s)",
        theme["id"], fmt["id"], length["id"],
    )
    parsed = _call_model(_build_user_prompt(theme, recent, fmt, length))
    item = _make_item(parsed, theme["id"])
    item["format"] = fmt["id"]
    item["length"] = length["id"]
    item["link"] = theme.get("link") or BRAND["website"]
    logger.info("Generated post id=%s (%d chars)", item["id"], len(item["post_text"]))
    return item


def get_format(fmt_id: str) -> dict[str, Any] | None:
    return next((f for f in POST_FORMATS if f["id"] == fmt_id), None)


def _build_custom_prompt(
    topic: str, recent: list[dict[str, str]], fmt: dict[str, Any], length: dict[str, Any]
) -> str:
    lines = [
        "The business owner has a SPECIFIC post they want written. Write it in "
        "the SkySystems brand voice and follow all the rules. Here is exactly "
        "what they want this post to be about:",
        f'"""{topic}"""',
        "",
        "If it is a promotion, holiday greeting, event, or announcement, write it "
        "warmly and professionally, and make any offer or date they mention clear "
        "and accurate. Do not invent details (prices, dates, discounts) they did "
        "not give you.",
        f"FORMAT for this post (follow it): {fmt['instruction']}",
        f"LENGTH for this post: {length['instruction']}",
        f"The clickable link {BRAND['website']} and hashtags go in the caption.",
        "",
        "Return ONLY the JSON object described in your instructions.",
    ]
    return "\n".join(lines)


def generate_custom(
    topic: str,
    fmt: dict[str, Any] | None = None,
    length: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a post from a free-text instruction (holiday, sale, event...)."""
    if not (topic or "").strip():
        raise GenerationError("A custom topic is required.")
    recent = recent_topics()
    if fmt is None:
        fmt = choose_format(recent)
    if length is None:
        length = random.choice(POST_LENGTHS)

    logger.info("Generating CUSTOM post (format=%s): %s", fmt["id"], topic[:70])
    parsed = _call_model(_build_custom_prompt(topic, recent, fmt, length))
    item = _make_item(parsed, "custom")
    item["format"] = fmt["id"]
    item["length"] = length["id"]
    item["custom_topic"] = topic.strip()
    logger.info("Generated custom post id=%s", item["id"])
    return item


def _call_model(user_prompt: str) -> dict[str, Any]:
    """Shared Anthropic call: send the prompt, return the parsed JSON item dict."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise GenerationError(
            "ANTHROPIC_API_KEY is not set. Provide it via environment/.env."
        )
    model = _model()
    client = anthropic.Anthropic(api_key=api_key)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": build_system_prompt(),
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if _uses_adaptive_thinking(model):
        # No temperature on these models; steer depth with effort instead.
        request["thinking"] = {"type": "adaptive"}
        request["output_config"] = {"effort": "medium"}
    else:
        request["temperature"] = TEMPERATURE

    response = client.messages.create(**request)
    if response.stop_reason == "refusal":
        raise GenerationError("Model refused the request; no post produced.")
    text_parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
    raw_text = "\n".join(text_parts).strip()
    if not raw_text:
        raise GenerationError("Model returned no text content.")
    return _parse_response(raw_text)


def generate_batch(n: int) -> list[dict[str, Any]]:
    """Generate n posts, each aware of the ones generated earlier in the batch.

    We append each new item's theme to an in-memory 'recent' view by reading
    fresh from disk is not enough within one process, so we vary the theme
    selection locally to keep the batch internally diverse.
    """
    if n < 1:
        raise ValueError("Batch size must be >= 1")

    themes = load_themes()
    items: list[dict[str, Any]] = []
    used_theme_ids: set[str] = set()
    used_formats: list[str] = []

    for i in range(n):
        recent = recent_topics()
        # Fold in themes/formats already chosen in THIS batch to avoid repeats.
        recent_t = recent + [{"theme": t} for t in used_theme_ids]
        recent_f = recent + [{"format": f} for f in used_formats]
        theme = choose_theme(themes, recent_t)
        fmt = choose_format(recent_f)
        logger.info(
            "Batch item %d/%d -> theme '%s', format '%s'",
            i + 1, n, theme["id"], fmt["id"],
        )
        item = generate_post(theme=theme, fmt=fmt)
        items.append(item)
        used_theme_ids.add(theme["id"])
        used_formats.append(fmt["id"])

    return items
