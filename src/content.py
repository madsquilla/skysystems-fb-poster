"""Brand facts, theme bank loading, and system-prompt assembly.

This module is the single source of truth for SkySystems USA brand voice and
the rotating content themes. generate.py imports BRAND and build_system_prompt()
from here, and loads themes via load_themes().
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Resolve data/ relative to the repo root (one level up from src/).
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_THEMES_PATH = _DATA_DIR / "themes.json"

CANONICAL_URL = os.environ.get("CANONICAL_URL", "https://skyusa.us")

# ---------------------------------------------------------------------------
# Brand facts -- treated as the verbatim source of truth from the build brief.
# ---------------------------------------------------------------------------
BRAND: dict[str, Any] = {
    "company": "SkySystems USA Corporation",
    "headline": (
        "Your Managed IT & Cybersecurity Partner in Austin, Texas."
    ),
    "positioning": (
        "Secure, fully managed IT, cybersecurity, and cloud for Austin-area "
        "businesses and government, backed by 24/7 support."
    ),
    "location": "Austin, Texas (US headquarters).",
    "founder": "Bjoern Steinbrink",
    "website": CANONICAL_URL,
    "tagline": (
        "We Brought Enterprise IT Security to Small Business. On Purpose."
    ),
    "differentiator": (
        "An Austin-based partner that brings enterprise-grade security, "
        "management, and 24/7 support to small and mid-sized businesses and "
        "government, made accessible and affordable. Senior-level expertise and "
        "fast, human response, without the enterprise price tag."
    ),
    "stats": [
        "13+ years in business",
        "70+ professionals",
        "7 datacenters",
        "24/7 support",
    ],
    "timeline": (
        "Founded 2013, grew to 70+ professionals by 2023, growing in Texas "
        "in 2025."
    ),
    "mission": (
        "Bring enterprise-grade IT security and management to small and "
        "mid-sized businesses and government. Make robust protection "
        "accessible and understandable, without the enterprise price tag."
    ),
    "three_promises": [
        "24/7 Mission-Critical Support",
        "Ironclad Data Security",
        "Strategic Infrastructure",
    ],
    "service_pillars": [
        "Cybersecurity & Compliance",
        "Managed IT & Helpdesk (a dedicated IT department, real people answer)",
        "Microsoft 365 & Microsoft Azure",
        "Cloud Hosting & AWS",
        "Backup & Disaster Recovery (Veeam)",
        "Network Security & WatchGuard Firewalls",
        "Networking & WiFi",
        "Business Phones / VoIP (3CX)",
        "AI Implementation (governed, business-ready AI)",
    ],
    "verticals": [
        "Public Safety & Municipalities (CJIS-compliant networks, dispatch/CAD "
        "uptime, ransomware defense, audit-ready documentation)",
        "Ministries & Non-Profits (donor/member data security, broadcast-quality "
        "streaming, volunteer access control, lean budgets)",
        "Mega Churches & Multi-Campus (broadcast-grade production, multi-site "
        "infrastructure, AI-ready)",
        "Financial Services (regulator-grade security, exam-ready compliance, "
        "wire/invoice fraud defense, governed AI)",
        "Professional Services (law, accounting, consulting; privileged data, "
        "billable-system uptime, safe AI)",
        "Retail (PCI-aligned multi-site networks, POS uptime)",
        "Smaller Cities & Towns (CJIS, ransomware defense, public-budget pricing)",
        "Technology, Media & Telecom (cloud, DevOps, high-throughput infra, AI)",
    ],
    "compliance": ["NIST", "HIPAA", "CJIS", "SEC", "PCI"],
    "signature_stat": (
        "~60% of small businesses close within 6 months of a cyberattack "
        "(use sparingly, not every post)."
    ),
}

# ---------------------------------------------------------------------------
# Voice rules -- enforced via the system prompt.
# ---------------------------------------------------------------------------
VOICE_RULES = [
    "Never mention Germany, Europe, 'European', transatlantic, overseas or "
    "offshore teams, or 'follow the sun' support. Present SkySystems strictly as "
    "an Austin, Texas company with a US team.",
    "Professional, plain-English, reassuring. Never fear-monger for its own sake.",
    "Educational first, sales second. Build credibility, do not hard-sell.",
    "Sound human-written. No buzzword soup, no 'unlock the power of synergy'.",
    "Never use em dashes (the long dash). Use commas, periods, or colons instead.",
    "Short paragraphs, 1 to 3 short sentences each. Easy to read on mobile.",
    "Light, tasteful emoji use is OK (0 to 2 per post), not every line.",
    "End most posts with a soft CTA, not a pushy one. Point to "
    + CANONICAL_URL
    + " or invite a conversation.",
    "Include 2 to 4 relevant hashtags max (e.g. #Cybersecurity #AustinTX "
    "#ManagedIT #SmallBusiness). Do not overdo it.",
    "Do not stack every credibility stat into one post. Rotate, use accurately.",
]


# ---------------------------------------------------------------------------
# Post formats -- the *structure* of a post, chosen at random (and de-duped)
# per post so the feed never falls into one repeated rhythm.
# ---------------------------------------------------------------------------
POST_FORMATS = [
    {"id": "listicle", "instruction": (
        "Structure as a short numbered list of 2 to 4 tight, specific points, "
        "with a one-line intro before the list and a one-line takeaway after.")},
    {"id": "myth-reality", "instruction": (
        "Structure as Myth vs Reality: state a common misconception in one "
        "line, then correct it plainly and reassuringly.")},
    {"id": "scenario", "instruction": (
        "Open with a tiny real-world scenario (two or three sentences of story, "
        "e.g. a Monday morning at a small business), then draw out the lesson.")},
    {"id": "question-led", "instruction": (
        "Open with one direct question to the reader. Answer it simply in plain "
        "language, then close.")},
    {"id": "stat-led", "instruction": (
        "Lead with one specific, accurate number or statistic, explain why it "
        "matters, then reassure. Do not stack multiple stats.")},
    {"id": "how-to", "instruction": (
        "Give a short, practical how-to in exactly 3 plain steps the reader "
        "could act on this week.")},
    {"id": "one-bold-idea", "instruction": (
        "Make one bold, clear statement up front, then back it up in 2 to 3 "
        "short sentences. Keep the whole thing punchy and brief.")},
    {"id": "human-angle", "instruction": (
        "Tell it from a human, behind-the-scenes angle: what our team actually "
        "does, or a relatable frustration a business owner feels. Warm, candid, "
        "not salesy.")},
    {"id": "quick-tip", "instruction": (
        "Share one single, specific, immediately useful tip. Short and to the "
        "point, no filler.")},
    {"id": "comparison", "instruction": (
        "Frame as a simple before/after or this-vs-that comparison to make the "
        "point concrete.")},
]

POST_LENGTHS = [
    {"id": "short", "instruction": (
        "Short and scannable: 2 to 3 short sentences that still make a real, "
        "informative point. Roughly 40 to 65 words.")},
    {"id": "medium", "instruction": (
        "A bit fuller: 3 to 4 short sentences. Roughly 65 to 90 words. Stay "
        "tight, no filler.")},
    {"id": "list", "instruction": (
        "A short numbered list of 3 brief points with a one-line intro. Keep "
        "each point to a single line if you can. Roughly 55 to 85 words.")},
]


def load_themes() -> list[dict[str, Any]]:
    """Load the rotating theme bank from data/themes.json."""
    with open(_THEMES_PATH, encoding="utf-8") as fh:
        themes = json.load(fh)
    if not isinstance(themes, list) or not themes:
        raise ValueError(f"themes.json is empty or malformed at {_THEMES_PATH}")
    return themes


def _bullet(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def build_system_prompt() -> str:
    """Assemble the system prompt that enforces brand voice and output format."""
    return f"""You write short, professional social media posts for the
official Facebook Page of {BRAND['company']}, a managed IT and cybersecurity
provider (an MSP) based in {BRAND['location']}

BRAND POSITIONING
- Headline: {BRAND['headline']}
- {BRAND['positioning']}
- Founder: {BRAND['founder']}
- Website (use this exact URL for links): {BRAND['website']}
- Tagline: {BRAND['tagline']}
- Signature differentiator: {BRAND['differentiator']}

MISSION
{BRAND['mission']}

CREDIBILITY (rotate, use accurately, do NOT stack all of these in one post)
{_bullet(BRAND['stats'])}
Timeline: {BRAND['timeline']}

THREE PROMISES (a good recurring framing)
{_bullet(BRAND['three_promises'])}

SERVICE PILLARS (rotate across these)
{_bullet(BRAND['service_pillars'])}

TARGET VERTICALS
{_bullet(BRAND['verticals'])}

COMPLIANCE FRAMEWORKS you may reference accurately: {', '.join(BRAND['compliance'])}.
Signature stat to use sparingly: {BRAND['signature_stat']}

VOICE RULES (follow strictly)
{_bullet(VOICE_RULES)}

WRITING QUALITY (aim higher than generic)
- The post_text is shown IN FULL on the image graphic, so keep it informative
  but scannable. Make a real, useful point, but cut filler, throat-clearing,
  and obvious statements. Tight and substantive, not padded.
- Open with a specific, concrete hook, not a vague generality.
- Prefer real specifics (a scenario, a number, a named framework) over fluff.
- Reference REAL SkySystems services and industries accurately (see the service
  pillars and target verticals above). Name concrete offerings when relevant
  (e.g. Veeam backup, WatchGuard firewalls, 3CX phones, Microsoft 365, CJIS
  compliance, governed AI). Never invent services we do not offer.
- Vary sentence and paragraph structure between posts so they do not all feel
  templated. No two posts should open the same way.
- You will be given a specific FORMAT and LENGTH for THIS post. Follow them.
  They change deliberately from post to post so the feed reads like a human
  wrote it, never like a template or AI filler.
- Vary your use of emojis (often use none) and change up the hashtag set.
- Every claim should sound like a knowledgeable human wrote it, not a brochure.

THE TWO PIECES OF TEXT
Each post is a branded graphic (with a relevant background photo) plus a short
caption shown above it. You write both:

1. post_text -> the MESSAGE shown IN FULL on the image graphic. Informative but
   scannable. You MAY end with a short VERBAL soft CTA (e.g. "We are in Austin
   and happy to help."). Do NOT put any URL or hashtags in post_text. The image
   is not clickable, so a link here is wasted. Emojis are fine in moderation.

2. caption -> the short text shown ABOVE the image in the Facebook post. This is
   where the clickable link and hashtags belong. Format it as:
   one short sentence that complements the message (do not just repeat it),
   then the EXACT page link you are given for this post (it deep-links to the
   specific service or industry the post is about, e.g.
   {BRAND['website']}/solutions/cybersecurity),
   then 2 to 4 relevant hashtags.
   Keep the caption brief. Use the provided link verbatim; do not shorten it to
   the homepage.

Plus three helper fields for the graphic:
- image_headline: a short, bold 3 to 7 word title shown large at the top of the
  graphic. It is the hook a scroller reads first. Punchy and specific, and
  different from the opening sentence of post_text.
- image_kicker: a 2 to 4 word Title Case label above the headline
  (e.g. "Threat of the Week", "Follow the Sun", "Public Safety", "Backup & DR").
- image_query: a 2 to 4 word stock-photo search that is concrete, visual, and
  professional. Good: "server room", "cybersecurity lock", "austin texas skyline",
  "factory automation", "police dispatch center", "team meeting office". Avoid
  abstract or text-like queries.

FORMATTING THE post_text (this is rendered on the graphic, so structure helps)
- When you give steps or a list, write each item on its own line starting with
  "1. ", "2. ", "3. " so it renders as a clean numbered list.
- When you use a label like a myth/reality or before/after frame, start the line
  with the label and a colon (e.g. "Myth: ...", "Reality: ...", "Before: ...").
- Separate distinct thoughts into short paragraphs (a blank line between them).

OUTPUT FORMAT
Return ONLY a single JSON object, no prose before or after, no markdown code
fences. The object must have exactly these keys:
{{
  "post_text": "the message shown ON the image (no URL, no hashtags)",
  "caption": "short lead sentence, then the link, then 2 to 4 hashtags",
  "theme": "the theme id you were asked to write for",
  "image_headline": "short bold 3 to 7 word title",
  "image_kicker": "2 to 4 word Title Case label",
  "image_query": "2 to 4 word concrete photo search"
}}"""
