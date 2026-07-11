"""PlungePost -- the web review dashboard.

Browser UI to generate (random or custom/holiday posts), review, approve,
schedule, and publish posts -- plus a built-in scheduler that auto-publishes
scheduled posts and an optional daily auto-pilot.

Run locally:
    python src/webapp.py            # http://localhost:8080

Security: this dashboard can publish to your Facebook Page and has NO login.
Keep it on localhost or a trusted LAN. Do not expose it to the internet.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template_string, request,
    send_from_directory, session, url_for,
)

import cards
import content
import generate as gen
import onboard
import publish as pub
import store
import tenants

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ASSETS_DIR = _REPO_ROOT / "assets"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("plungepost.web")

if load_dotenv is not None:
    load_dotenv(_REPO_ROOT / ".env")

# Make scheduling use the configured local time zone (TZ env, e.g.
# America/Chicago). tzset() exists on Unix only; Windows uses system time.
if hasattr(time, "tzset"):
    time.tzset()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "plungepost-local-dashboard")

# Guards every queue read-modify-write so the web routes and the background
# scheduler never corrupt the JSON files by writing at the same time.
_LOCK = threading.RLock()


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_OPTIONS = list(zip(DAYS, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]))


# --- multi-tenant bootstrapping + per-request context ----------------------

def _migrate_legacy_account() -> None:
    """First-run migration: if there are no accounts yet, fold the existing
    single-account setup (data/, assets/ logo, content.DEFAULT_BRAND, themes,
    env FB creds) into tenants/skysystems/ so nothing is lost."""
    if tenants.list_tenants():
        return
    logger.info("No accounts found; migrating existing setup into tenants/skysystems/")
    legacy_data = _REPO_ROOT / "data"
    themes_file = _REPO_ROOT / "data" / "themes.json"
    themes = []
    if themes_file.exists():
        import json
        try:
            themes = json.loads(themes_file.read_text(encoding="utf-8"))
        except Exception:
            themes = []
    if not themes:
        # Fall back to a bundled themes.json at the repo root if present.
        alt = _REPO_ROOT / "themes.json"
        if alt.exists():
            import json
            try:
                themes = json.loads(alt.read_text(encoding="utf-8"))
            except Exception:
                themes = []
    logo_bytes = None
    lf = _ASSETS_DIR / "logo_full.png"
    if lf.exists():
        logo_bytes = lf.read_bytes()
    mark_bytes = None
    lm = _ASSETS_DIR / "logo_mark.png"
    if lm.exists():
        mark_bytes = lm.read_bytes()
    slug = tenants.create_tenant(
        "skysystems", "SkySystems USA", "https://skyusa.us",
        content.DEFAULT_BRAND, themes,
        fb_page_id=os.environ.get("META_PAGE_ID", ""),
        fb_token=os.environ.get("META_PAGE_ACCESS_TOKEN", ""),
        accent="#2ecc71", accent2="#2b6cc4",
        logo_bytes=logo_bytes, mark_bytes=mark_bytes,
    )
    # Move any existing queues/cards into the new tenant's data dir.
    tdata = tenants.data_dir(slug)
    if legacy_data.exists():
        import shutil
        for q in ("pending.json", "approved.json", "history.json", "settings.json"):
            src = legacy_data / q
            if src.exists():
                shutil.copy2(src, tdata / q)
        src_cards = legacy_data / "cards"
        if src_cards.exists():
            dst_cards = tenants.cards_dir(slug)
            dst_cards.mkdir(parents=True, exist_ok=True)
            for f in src_cards.glob("*.png"):
                shutil.copy2(f, dst_cards / f.name)
    logger.info("Migration complete -> tenants/%s", slug)


def _current_slug() -> str:
    """The account slug for this request (session, else the first account)."""
    slug = session.get("acct")
    if slug and tenants.exists(slug):
        return slug
    return tenants.current()


@app.before_request
def _bind_tenant():
    """Bind the current account for this request/thread before any route runs."""
    tenants.set_current(_current_slug())


def _meta_ready() -> bool:
    """Whether the CURRENT account can publish (has a Page id + token)."""
    page_id, token = tenants.fb_creds()
    if page_id and token:
        return True
    return bool(os.environ.get("META_PAGE_ID") and os.environ.get("META_PAGE_ACCESS_TOKEN"))


def _pop(items, item_id):
    found, rest = None, []
    for it in items:
        if it.get("id") == item_id and found is None:
            found = it
        else:
            rest.append(it)
    return found, rest


def _delete_card(item) -> None:
    cp = item.get("card_path")
    if cp:
        p = Path(cp)
        if not p.is_absolute():
            p = _REPO_ROOT / cp
        try:
            p.unlink()
        except OSError:
            pass


def _next_slots(settings, start, count, taken):
    """Return up to `count` future schedule times (ISO, minute precision) on the
    selected auto-pilot days/times, skipping ones already in `taken`. Each post
    lands on its own corresponding day/time slot."""
    days = settings.get("auto_pilot_days") or []
    times = sorted(t for t in (settings.get("auto_pilot_times") or []) if t)
    out = []
    if not days or not times:
        return out
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(370):
        if DAYS[day.weekday()] in days:
            for t in times:
                try:
                    hh, mm = (int(x) for x in t.split(":"))
                except ValueError:
                    continue
                dt = day.replace(hour=hh, minute=mm)
                if dt <= start:
                    continue
                key = dt.isoformat(timespec="minutes")
                if key in taken:
                    continue
                taken.add(key)
                out.append(key)
                if len(out) >= count:
                    return out
        day += timedelta(days=1)
    return out


# Background-generation progress (shown live in the dashboard overlay).
_GEN = {"active": False, "message": "Idle", "done": 0, "total": 0, "error": False}


def _run_generation(kind, count, topic=None, fmt_id=None):
    """Generate posts in a background thread, updating _GEN so the UI can show
    live progress. One post at a time so dedup + progress both work."""
    try:
        for i in range(count):
            with _LOCK:
                _GEN.update(message=f"Writing post {i + 1} of {count}", total=count)
            if kind == "custom":
                item = gen.generate_custom(
                    topic, fmt=(gen.get_format(fmt_id) if fmt_id else None))
            else:
                item = gen.generate_post()
            with _LOCK:
                _GEN["message"] = f"Designing the graphic ({i + 1} of {count})"
            cards.build_card(item)
            with _LOCK:
                store.append_pending(item)
                _GEN["done"] = i + 1
        with _LOCK:
            _GEN["message"] = "Done"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background generation failed")
        m = str(exc)
        low = m.lower()
        if "credit balance" in low:
            m = "Anthropic API is out of credits. Add credits at console.anthropic.com."
        elif "authentication" in low or "x-api-key" in low or "401" in low:
            m = "Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
        else:
            m = "Generation failed: " + m[:120]
        with _LOCK:
            _GEN.update(error=True, message=m)
    finally:
        with _LOCK:
            _GEN["active"] = False


def _start_generation(kind, count, topic=None, fmt_id=None):
    """Atomically claim the generator; returns False if one is already running."""
    with _LOCK:
        if _GEN["active"]:
            return False
        _GEN.update(active=True, done=0, total=count, message="Starting", error=False)
    threading.Thread(target=_run_generation,
                     args=(kind, count, topic, fmt_id), daemon=True).start()
    return True


def _do_publish(item) -> str:
    """Publish one item and append it to history. Returns the FB post id."""
    post_id = pub.publish_post(item)
    item["status"] = "posted"
    item["posted_at"] = datetime.now(timezone.utc).isoformat()
    item["facebook_post_id"] = post_id
    store.append_history(item)
    return post_id


# --- background scheduler --------------------------------------------------

def _scheduler_tick_one() -> None:
    """One pass for the CURRENT account: publish any due scheduled posts."""
    page_id, token = tenants.fb_creds()
    if not (page_id and token) and not (
        os.environ.get("META_PAGE_ID") and os.environ.get("META_PAGE_ACCESS_TOKEN")
    ):
        return
    now = datetime.now()  # local time (set TZ env on the container)
    with _LOCK:
        approved = store.read_approved()
        remaining, changed = [], False
        for it in approved:
            sched = it.get("scheduled_at")
            due = False
            if sched:
                try:
                    due = datetime.fromisoformat(sched) <= now
                except ValueError:
                    due = False
            if due:
                try:
                    pid = _do_publish(it)
                    logger.info("Scheduler published %s (fb=%s)", it["id"], pid)
                    changed = True
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.error("Scheduled publish failed for %s: %s", it["id"], exc)
            remaining.append(it)
        if changed:
            store.write_approved(remaining)


def _scheduler_tick() -> None:
    """One pass over EVERY account so scheduled posts publish for all tenants."""
    for t in tenants.list_tenants():
        tenants.set_current(t["slug"])
        try:
            _scheduler_tick_one()
        except Exception as exc:  # noqa: BLE001
            logger.error("Scheduler failed for account %s: %s", t["slug"], exc)


def _scheduler_loop() -> None:
    logger.info("Background scheduler started.")
    while True:
        try:
            _scheduler_tick()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduler tick error: %s", exc)
        time.sleep(60)


# --- routes ----------------------------------------------------------------

def _back():
    """Redirect back to the page the action came from."""
    return redirect(request.referrer or url_for("overview"))


def _render(page, title):
    approved = store.read_approved()
    scheduled = sorted(
        [a for a in approved if a.get("scheduled_at")],
        key=lambda i: i.get("scheduled_at", ""),
    )
    ready = [a for a in approved if not a.get("scheduled_at")]
    history = list(reversed(store.read_history()))
    cur_slug = tenants.current()
    return render_template_string(
        TEMPLATE,
        page=page,
        page_title=title,
        pending=store.read_pending(),
        scheduled=scheduled,
        ready=ready,
        history=history[:40],
        posted_total=len(history),
        settings=store.read_settings(),
        formats=gen.POST_FORMATS,
        day_options=DAY_OPTIONS,
        meta_ready=_meta_ready(),
        now_local=datetime.now().strftime("%Y-%m-%dT%H:%M"),
        accounts=tenants.list_tenants(),
        current_slug=cur_slug,
        current_name=tenants.account(cur_slug).get("name", cur_slug),
    )


@app.route("/")
def overview():
    return _render("overview", "Overview")


@app.route("/review")
def review():
    return _render("review", "In Review")


@app.route("/approved")
def approved_page():
    return _render("approved", "Ready to Publish")


@app.route("/scheduled")
def scheduled_page():
    return _render("scheduled", "Scheduled")


@app.route("/published")
def published():
    return _render("published", "Published")


@app.route("/card/<path:name>")
def card(name):
    if not name.endswith(".png") or "/" in name or "\\" in name:
        abort(404)
    return send_from_directory(tenants.cards_dir(), name)


@app.route("/brand/<path:name>")
def brand(name):
    """Serve the current account's logo (falling back to the packaged asset)."""
    if not name.endswith(".png") or "/" in name or "\\" in name:
        abort(404)
    tenant_logo = tenants.tenant_dir() / name
    if tenant_logo.exists():
        return send_from_directory(tenants.tenant_dir(), name)
    return send_from_directory(_ASSETS_DIR, name)


@app.route("/generate", methods=["POST"])
def generate():
    count = max(1, min(int(request.form.get("count", 1)), 50))
    if not _start_generation("random", count):
        flash("Already generating -- hang tight.", "err")
    return _back()


@app.route("/generate-custom", methods=["POST"])
def generate_custom():
    topic = (request.form.get("topic") or "").strip()
    fmt_id = request.form.get("format") or ""
    if not topic:
        flash("Type what the post should be about first.", "err")
        return _back()
    if not _start_generation("custom", 1, topic, fmt_id):
        flash("Already generating -- hang tight.", "err")
    return _back()


@app.route("/gen-status")
def gen_status():
    with _LOCK:
        return jsonify(dict(_GEN))


@app.route("/approve/<item_id>", methods=["POST"])
def approve(item_id):
    with _LOCK:
        item, rest = _pop(store.read_pending(), item_id)
        if item:
            approved = store.read_approved()
            item["status"] = "approved"
            s = store.read_settings()
            if s.get("auto_pilot_enabled"):
                taken = {a["scheduled_at"] for a in approved if a.get("scheduled_at")}
                slots = _next_slots(s, datetime.now(), 1, taken)
                if slots:
                    item["scheduled_at"] = slots[0]
            store.write_pending(rest)
            store.write_approved(approved + [item])
            flash("Approved and scheduled." if item.get("scheduled_at") else "Approved.", "ok")
    return _back()


@app.route("/unapprove/<item_id>", methods=["POST"])
def unapprove(item_id):
    with _LOCK:
        item, rest = _pop(store.read_approved(), item_id)
        if item:
            item.pop("scheduled_at", None)
            item["status"] = "pending"
            store.write_approved(rest)
            store.write_pending(store.read_pending() + [item])
            flash("Moved back to review.", "ok")
    return _back()


@app.route("/discard/<item_id>", methods=["POST"])
def discard(item_id):
    with _LOCK:
        item, rest = _pop(store.read_pending(), item_id)
        if item:
            store.write_pending(rest)
        else:
            item, rest = _pop(store.read_approved(), item_id)
            if item:
                store.write_approved(rest)
        if item:
            _delete_card(item)
            flash("Discarded.", "ok")
    return _back()


@app.route("/approve-all", methods=["POST"])
def approve_all():
    with _LOCK:
        pending = store.read_pending()
        approved = store.read_approved()
        s = store.read_settings()
        slots = []
        if s.get("auto_pilot_enabled"):
            taken = {a["scheduled_at"] for a in approved if a.get("scheduled_at")}
            slots = _next_slots(s, datetime.now(), len(pending), taken)
        for i, it in enumerate(pending):
            it["status"] = "approved"
            if i < len(slots):
                it["scheduled_at"] = slots[i]
        store.write_approved(approved + pending)
        store.write_pending([])
        msg = f"Approved {len(pending)} post(s)."
        if slots:
            msg += f" Auto-scheduled {len(slots)} across your days/times."
        flash(msg, "ok")
    return _back()


@app.route("/clear-pending", methods=["POST"])
def clear_pending():
    with _LOCK:
        items = store.read_pending()
        for it in items:
            _delete_card(it)
        store.write_pending([])
        flash(f"Cleared {len(items)} post(s) from review.", "ok")
    return _back()


@app.route("/clear-scheduled", methods=["POST"])
def clear_scheduled():
    with _LOCK:
        approved = store.read_approved()
        scheduled = [a for a in approved if a.get("scheduled_at")]
        for it in scheduled:
            _delete_card(it)
        store.write_approved([a for a in approved if not a.get("scheduled_at")])
        flash(f"Cleared {len(scheduled)} scheduled post(s).", "ok")
    return _back()


@app.route("/schedule-all", methods=["POST"])
def schedule_all():
    """Assign schedule slots to every currently-ready post across the auto-pilot
    days/times (skipping slots already taken)."""
    with _LOCK:
        approved = store.read_approved()
        ready = [a for a in approved if not a.get("scheduled_at")]
        s = store.read_settings()
        taken = {a["scheduled_at"] for a in approved if a.get("scheduled_at")}
        slots = _next_slots(s, datetime.now(), len(ready), taken)
        if not slots:
            flash("Set auto-pilot days and times on Overview first.", "err")
            return _back()
        for it, slot in zip(ready, slots):
            it["scheduled_at"] = slot
        store.write_approved(approved)
        flash(f"Scheduled {min(len(slots), len(ready))} post(s) across your days/times.", "ok")
    return _back()


@app.route("/clear-approved", methods=["POST"])
def clear_approved():
    with _LOCK:
        approved = store.read_approved()
        ready = [a for a in approved if not a.get("scheduled_at")]
        for it in ready:
            _delete_card(it)
        store.write_approved([a for a in approved if a.get("scheduled_at")])
        flash(f"Cleared {len(ready)} ready post(s).", "ok")
    return _back()


@app.route("/schedule/<item_id>", methods=["POST"])
def schedule(item_id):
    when = (request.form.get("when") or "").strip()
    try:
        dt = datetime.fromisoformat(when)
    except ValueError:
        flash("Pick a valid date and time.", "err")
        return _back()
    with _LOCK:
        approved = store.read_approved()
        for it in approved:
            if it.get("id") == item_id:
                it["scheduled_at"] = dt.isoformat(timespec="minutes")
                store.write_approved(approved)
                flash(f"Scheduled for {dt.strftime('%a %b %d, %I:%M %p')}.", "ok")
                break
    return _back()


@app.route("/unschedule/<item_id>", methods=["POST"])
def unschedule(item_id):
    with _LOCK:
        approved = store.read_approved()
        for it in approved:
            if it.get("id") == item_id:
                it.pop("scheduled_at", None)
                store.write_approved(approved)
                flash("Schedule cleared.", "ok")
                break
    return _back()


@app.route("/publish/<item_id>", methods=["POST"])
def publish_one(item_id):
    with _LOCK:
        item, rest = _pop(store.read_approved(), item_id)
        if not item:
            flash("That post is no longer approved.", "err")
            return _back()
        try:
            pid = _do_publish(item)
            store.write_approved(rest)
            flash(f"Published! Facebook post id {pid}", "ok")
        except pub.TokenExpiredError as exc:
            flash(f"Token problem: {exc}", "err")
        except pub.PublishError as exc:
            flash(f"Publish failed: {exc}", "err")
    return _back()


@app.route("/settings", methods=["POST"])
def settings_save():
    s = store.read_settings()
    s["auto_pilot_enabled"] = request.form.get("auto_pilot_enabled") == "on"
    s["auto_pilot_days"] = [d for d in DAYS if request.form.get("day_" + d) == "on"]
    times = []
    for i in (1, 2, 3):
        t = (request.form.get(f"time_{i}") or "").strip()
        if t:
            times.append(t)
    s["auto_pilot_times"] = sorted(set(times)) or ["09:00"]
    store.write_settings(s)
    flash("Auto-pilot settings saved.", "ok")
    return _back()


# --- account switching + onboarding ----------------------------------------

@app.route("/switch/<slug>", methods=["POST", "GET"])
def switch_account(slug):
    if tenants.exists(slug):
        session["acct"] = slug
        tenants.set_current(slug)
        flash(f"Switched to {tenants.account(slug).get('name', slug)}.", "ok")
    else:
        flash("That account no longer exists.", "err")
    return redirect(url_for("overview"))


# Background onboarding progress (mirrors _GEN so the UI can show a live overlay).
_ONB = {"active": False, "message": "Idle", "error": False, "slug": ""}


def _run_onboard(name, website, fb_page_id, fb_token, accent, accent2):
    try:
        with _LOCK:
            _ONB.update(message="Reading the website...", error=False, slug="")
        slug = onboard.build_account(
            name=name, website=website,
            fb_page_id=fb_page_id, fb_token=fb_token,
            accent=accent, accent2=accent2,
            progress=lambda m: _ONB.update(message=m),
        )
        with _LOCK:
            _ONB.update(message="Done", slug=slug)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Onboarding failed")
        m = str(exc)
        low = m.lower()
        if "credit balance" in low:
            m = "Anthropic API is out of credits. Add credits at console.anthropic.com."
        elif "authentication" in low or "x-api-key" in low or "401" in low:
            m = "Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env."
        else:
            m = "Onboarding failed: " + m[:160]
        with _LOCK:
            _ONB.update(error=True, message=m)
    finally:
        with _LOCK:
            _ONB["active"] = False


@app.route("/accounts/new", methods=["POST"])
def account_new():
    name = (request.form.get("name") or "").strip()
    website = (request.form.get("website") or "").strip()
    fb_page_id = (request.form.get("fb_page_id") or "").strip()
    fb_token = (request.form.get("fb_token") or "").strip()
    accent = (request.form.get("accent") or "#2ecc71").strip()
    accent2 = (request.form.get("accent2") or "#2b6cc4").strip()
    if not name or not website:
        flash("Business name and website are both required.", "err")
        return _back()
    with _LOCK:
        if _ONB["active"]:
            flash("Already building an account -- hang tight.", "err")
            return _back()
        _ONB.update(active=True, message="Starting", error=False, slug="")
    threading.Thread(
        target=_run_onboard,
        args=(name, website, fb_page_id, fb_token, accent, accent2),
        daemon=True,
    ).start()
    return _back()


@app.route("/onboard-status")
def onboard_status():
    with _LOCK:
        data = dict(_ONB)
    # When a new account finishes building, switch the session to it.
    if data.get("slug") and not data.get("active"):
        session["acct"] = data["slug"]
    return jsonify(data)


TEMPLATE = r"""
{% macro render_post(p, kind, meta_ready, now_local) %}
<article class="post">
  {% if p.card_path %}
  <a class="thumb" href="{{ url_for('card', name=p.id + '.png') }}" target="_blank" title="Open full size">
    <img src="{{ url_for('card', name=p.id + '.png') }}" loading="lazy" alt="post graphic">
    <span class="zoom">Full size</span>
  </a>{% endif %}
  <div class="pbody">
    <div class="chips">
      <span class="chip green">{{ p.image_kicker }}</span>
      {% if p.format %}<span class="chip blue">{{ p.format }}</span>{% endif %}
      {% if p.custom_topic %}<span class="chip amber">Custom</span>{% endif %}
      {% if kind == "scheduled" %}<span class="chip amber solid">{{ p.scheduled_at.replace("T"," ") }}</span>{% endif %}
    </div>
    <p class="cap">{{ p.caption }}</p>
    {% if kind != "history" %}<p class="ptext">{{ p.post_text }}</p>{% endif %}
    <div class="actions">
      {% if kind == "pending" %}
        <form method="post" action="{{ url_for('approve', item_id=p.id) }}"><button class="btn primary">Approve</button></form>
        <form method="post" action="{{ url_for('discard', item_id=p.id) }}" onsubmit="return confirm('Discard this post?')"><button class="btn danger">Discard</button></form>
      {% elif kind == "approved" %}
        <form method="post" action="{{ url_for('publish_one', item_id=p.id) }}"><button class="btn blue" {{ 'disabled' if not meta_ready }}>Publish now</button></form>
        <form method="post" action="{{ url_for('schedule', item_id=p.id) }}" class="sched"><input type="datetime-local" name="when" value="{{ now_local }}"><button class="btn outline">Schedule</button></form>
        <form method="post" action="{{ url_for('unapprove', item_id=p.id) }}"><button class="btn outline">Back to review</button></form>
        <form method="post" action="{{ url_for('discard', item_id=p.id) }}" onsubmit="return confirm('Discard this post?')"><button class="btn danger">Discard</button></form>
      {% elif kind == "scheduled" %}
        <form method="post" action="{{ url_for('publish_one', item_id=p.id) }}"><button class="btn blue" {{ 'disabled' if not meta_ready }}>Publish now</button></form>
        <form method="post" action="{{ url_for('unschedule', item_id=p.id) }}"><button class="btn outline">Cancel schedule</button></form>
        <form method="post" action="{{ url_for('discard', item_id=p.id) }}" onsubmit="return confirm('Discard this post?')"><button class="btn danger">Discard</button></form>
      {% elif kind == "history" %}
        {% if p.facebook_post_id %}<a class="btn outline" target="_blank" href="https://www.facebook.com/{{ p.facebook_post_id }}">View on Facebook &nearr;</a>{% endif %}
      {% endif %}
    </div>
  </div>
</article>
{% endmacro %}
<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PlungePost</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#f4f6f9; --side:#0e2740; --side2:#0b2036; --card:#ffffff; --soft:#f6f8fb;
    --bd:#e4e9ef; --bd2:#d3dbe4; --text:#1c2733; --mut:#5e6c7b; --mut2:#8493a2;
    --green:#2faa46; --green-h:#36bd50; --blue:#2b6cc4; --amber:#c2871c; --red:#d6455f;
    --sh:0 1px 2px rgba(16,24,40,.06), 0 4px 14px rgba(16,24,40,.05);
    --r:12px;
  }
  *{box-sizing:border-box;}
  html,body{margin:0;height:100%;}
  body{background:var(--bg);color:var(--text);
    font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
    font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;}
  a{color:inherit;text-decoration:none;}
  .layout{display:grid;grid-template-columns:248px 1fr;min-height:100vh;}

  /* sidebar (branded navy) */
  .sidebar{background:var(--side);display:flex;flex-direction:column;
    padding:22px 16px;position:sticky;top:0;height:100vh;}
  .side-logo{padding:6px 8px 20px;}
  .side-logo img{width:100%;max-width:182px;display:block;}
  .side-nav{display:flex;flex-direction:column;gap:2px;}
  .side-nav a{display:flex;align-items:center;justify-content:space-between;
    padding:10px 12px;border-radius:9px;color:#aebccc;font-weight:500;transition:.14s;}
  .side-nav a:hover{background:rgba(255,255,255,.07);color:#fff;}
  .side-nav a.active{background:rgba(47,170,70,.18);color:#fff;box-shadow:inset 3px 0 0 var(--green);}
  .side-nav a .n{background:rgba(255,255,255,.12);color:#d3dde7;border-radius:99px;
    font-size:11px;font-weight:600;padding:1px 8px;min-width:20px;text-align:center;}
  .side-foot{margin-top:auto;padding:12px 10px 4px;}
  .pill{display:inline-flex;align-items:center;gap:7px;padding:7px 11px;border-radius:99px;
    font-size:12px;font-weight:600;border:1px solid transparent;}
  .pill .dot{width:8px;height:8px;border-radius:50%;}
  .pill.on{color:#9ce8b2;border-color:rgba(47,170,70,.45);background:rgba(47,170,70,.16);}
  .pill.on .dot{background:#4cd674;box-shadow:0 0 8px rgba(76,214,116,.6);}
  .pill.off{color:#ecc887;border-color:rgba(194,135,28,.45);background:rgba(194,135,28,.16);}
  .pill.off .dot{background:#e6b15c;}

  /* main */
  .main{min-width:0;}
  .topbar{padding:26px 40px 0;}
  .topbar h1{margin:0;font-size:23px;font-weight:800;letter-spacing:-.01em;}
  .topbar .sub{margin:3px 0 0;color:var(--mut);font-size:13px;}
  .content{padding:22px 40px 80px;max-width:1180px;}

  .flash{padding:12px 16px;border-radius:10px;margin-bottom:12px;font-size:13.5px;font-weight:500;}
  .flash.ok{background:#e7f6ec;border:1px solid #c4e7cf;color:#1f7a37;}
  .flash.err{background:#fdecee;border:1px solid #f5c2cb;color:#b3243c;}
  .warn{background:#fdf4e3;border:1px solid #f0dcae;color:#8a6516;padding:12px 16px;border-radius:10px;margin-bottom:14px;font-size:13.5px;}

  /* stat cards */
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:6px 0 8px;}
  .stat{background:var(--card);border:1px solid var(--bd);border-radius:var(--r);
    padding:18px 20px;box-shadow:var(--sh);}
  .stat .num{font-size:30px;font-weight:800;letter-spacing:-.02em;line-height:1;color:var(--text);}
  .stat .lbl{color:var(--mut);font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;
    margin-top:9px;font-weight:600;}
  .stat.g .num{color:var(--green);} .stat.b .num{color:var(--blue);} .stat.a .num{color:var(--amber);}
  a.stat{display:block;transition:.14s;} a.stat:hover{border-color:#c2cdd9;box-shadow:0 2px 6px rgba(16,24,40,.08);}

  /* section heads */
  .sec{display:flex;align-items:center;gap:10px;margin:38px 0 16px;}
  .sec h2{margin:0;font-size:16px;font-weight:700;letter-spacing:-.01em;}
  .sec .count{background:#eef2f6;color:var(--mut);border-radius:99px;padding:2px 10px;
    font-size:12px;font-weight:600;}
  .sec .ln{flex:1;height:1px;background:var(--bd);}

  /* create panels */
  .create{display:grid;grid-template-columns:1.6fr 1fr;gap:16px;}
  .panel{background:var(--card);border:1px solid var(--bd);border-radius:var(--r);
    padding:22px;box-shadow:var(--sh);}
  .panel.full{grid-column:1/-1;}
  .panel h3{margin:0 0 4px;font-size:15px;font-weight:700;}
  .panel .hint{color:var(--mut);font-size:12.5px;margin:0 0 14px;}
  label{display:block;font-size:11.5px;color:var(--mut);margin:12px 0 6px;
    text-transform:uppercase;letter-spacing:.07em;font-weight:600;}
  input,select,textarea{width:100%;background:#fff;border:1px solid var(--bd2);
    color:var(--text);padding:11px 13px;border-radius:9px;font-size:14px;font-family:inherit;
    transition:.14s;}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--green);
    box-shadow:0 0 0 3px rgba(47,170,70,.15);}
  textarea{min-height:96px;resize:vertical;line-height:1.55;}
  .inline{display:flex;gap:10px;align-items:flex-end;}
  .inline>div{flex:1;}

  /* buttons */
  .btn{cursor:pointer;border:1px solid transparent;border-radius:9px;padding:10px 17px;
    font-weight:600;font-size:13px;font-family:inherit;transition:.14s;
    display:inline-flex;align-items:center;gap:6px;white-space:nowrap;}
  .btn.primary{background:var(--green);color:#fff;box-shadow:0 1px 2px rgba(47,170,70,.3);}
  .btn.primary:hover{background:var(--green-h);}
  .btn.blue{background:var(--blue);color:#fff;}
  .btn.blue:hover{background:#3a7ddb;}
  .btn.outline{background:#fff;border-color:var(--bd2);color:var(--text);}
  .btn.outline:hover{border-color:#b8c3cf;background:var(--soft);}
  .btn.danger{background:#fff;border-color:#f0c4cc;color:#c23a52;}
  .btn.danger:hover{background:#fdecee;border-color:#e7a8b3;}
  .btn:disabled{opacity:.5;cursor:not-allowed;box-shadow:none;}
  .btn.sm{padding:6px 12px;font-size:12px;}
  .sec form{margin:0;}

  /* toggle + days */
  .toggle{display:flex;align-items:center;gap:9px;margin-top:6px;}
  .toggle input{width:auto;}
  .toggle label{margin:0;text-transform:none;letter-spacing:0;color:var(--text);
    font-size:13.5px;font-weight:500;}
  .days{display:flex;gap:7px;flex-wrap:wrap;margin-top:4px;}
  .daybox{display:flex;align-items:center;gap:6px;background:var(--soft);
    border:1px solid var(--bd2);border-radius:9px;padding:8px 12px;font-size:13px;
    color:var(--text);text-transform:none;letter-spacing:0;margin:0;cursor:pointer;
    font-weight:500;transition:.14s;}
  .daybox:hover{border-color:#b8c3cf;background:#fff;}
  .daybox input{width:auto;}

  /* post card */
  .post{display:flex;gap:24px;background:var(--card);border:1px solid var(--bd);
    border-radius:var(--r);padding:20px;margin-bottom:16px;box-shadow:var(--sh);transition:.14s;}
  .post:hover{box-shadow:0 2px 4px rgba(16,24,40,.07), 0 8px 22px rgba(16,24,40,.08);}
  .thumb{position:relative;flex:0 0 auto;width:460px;max-width:46%;display:block;border-radius:11px;overflow:hidden;border:1px solid var(--bd);}
  .thumb img{width:100%;height:auto;display:block;transition:.18s;}
  .thumb:hover img{transform:scale(1.015);}
  .thumb .zoom{position:absolute;right:9px;bottom:9px;background:rgba(16,28,40,.82);
    color:#fff;font-size:11px;font-weight:600;padding:4px 9px;border-radius:7px;
    opacity:0;transition:.16s;backdrop-filter:blur(3px);}
  .thumb:hover .zoom{opacity:1;}
  .pbody{flex:1;min-width:0;}
  .chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:11px;}
  .chip{font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:600;
    border-radius:99px;padding:3px 11px;border:1px solid;}
  .chip.green{color:#1f8a3a;border-color:#c4e7cf;background:#e7f6ec;}
  .chip.blue{color:#2b6cc4;border-color:#cadcf3;background:#e8f0fb;}
  .chip.amber{color:#97681a;border-color:#eddcb6;background:#fbf1dd;}
  .chip.amber.solid{background:#f6e6c4;}
  .cap{color:var(--mut);font-size:13px;white-space:pre-wrap;margin:0 0 10px;line-height:1.5;}
  .ptext{font-size:14.5px;white-space:pre-wrap;line-height:1.62;margin:0;color:#26323f;}
  .actions{margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
  .actions form{display:inline-flex;gap:7px;align-items:center;margin:0;}
  .actions .sched input[type=datetime-local]{width:auto;padding:8px 10px;font-size:12.5px;}
  .empty{color:var(--mut);padding:14px 16px;background:#fff;border:1px dashed var(--bd2);
    border-radius:11px;font-size:13.5px;}

  .gen-overlay{display:none;position:fixed;inset:0;z-index:100;background:rgba(10,16,24,.6);
    align-items:center;justify-content:center;padding:20px;}
  .gen-card{background:#0f1b2a;border:1px solid #2a3a4d;border-radius:16px;padding:34px 40px;
    width:min(420px,92vw);text-align:center;box-shadow:0 24px 70px rgba(0,0,0,.5);}
  .gen-spin{width:44px;height:44px;margin:0 auto 18px;border-radius:50%;
    border:4px solid rgba(255,255,255,.14);border-top-color:#2faa46;animation:gspin .8s linear infinite;}
  @keyframes gspin{to{transform:rotate(360deg);}}
  .gen-title{color:#fff;font-weight:700;font-size:18px;}
  .gen-msg{color:#9fb0c0;font-size:14px;margin:6px 0 18px;min-height:18px;}
  .gen-track{height:9px;background:rgba(255,255,255,.12);border-radius:99px;overflow:hidden;}
  .gen-bar{height:100%;width:6%;background:linear-gradient(90deg,#2faa46,#4cd674);border-radius:99px;transition:width .45s ease;}
  .acct-switch{display:flex;gap:6px;padding:0 4px 4px;align-items:center;}
  .acct-switch select{flex:1;min-width:0;padding:8px 10px;font-size:13px;border-radius:9px;
    background:rgba(255,255,255,.06);color:#e8eef5;border:1px solid rgba(255,255,255,.14);}
  .acct-add{flex:0 0 auto;padding:8px 11px;font-size:12.5px;font-weight:600;cursor:pointer;
    border-radius:9px;background:rgba(47,170,70,.16);color:#7fe39a;border:1px solid rgba(47,170,70,.4);}
  .acct-add:hover{background:rgba(47,170,70,.26);}
  .acct-card{text-align:left;}
  .acct-card .gen-title{text-align:left;}
  .acct-hint{color:#9fb0c0;font-size:13px;line-height:1.5;margin:6px 0 16px;}
  .acct-card input{width:100%;margin-bottom:10px;padding:10px 12px;font-size:13.5px;border-radius:9px;
    background:#0b1521;color:#e8eef5;border:1px solid #2a3a4d;}
  .acct-card input[type=color]{width:44px;height:34px;padding:2px;margin:0;cursor:pointer;}
  .acct-row{display:flex;align-items:center;gap:10px;margin-bottom:10px;}
  .acct-row label{color:#9fb0c0;font-size:12.5px;margin:0;text-transform:none;letter-spacing:0;}
  .acct-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:6px;}
  @media(max-width:1000px){
    .layout{grid-template-columns:1fr;}
    .sidebar{position:sticky;top:0;z-index:30;height:auto;flex-direction:column;
      gap:10px;padding:12px 14px 8px;border-right:none;
      border-bottom:1px solid rgba(255,255,255,.08);}
    .side-logo{padding:0;} .side-logo img{max-width:148px;}
    .side-nav{flex-direction:row;flex-wrap:nowrap;overflow-x:auto;gap:6px;
      padding-bottom:2px;-webkit-overflow-scrolling:touch;}
    .side-nav::-webkit-scrollbar{display:none;}
    .side-nav a{white-space:nowrap;padding:9px 13px;}
    .side-nav a.active{box-shadow:inset 0 -3px 0 var(--green);}
    .side-foot{display:none;}
    .topbar{padding:18px 20px 0;}
    .content{padding:16px 20px 72px;}
    .stats{grid-template-columns:repeat(2,1fr);} .create{grid-template-columns:1fr;}
    .post{flex-direction:column;gap:16px;} .thumb{width:100%;max-width:100%;}
  }
  @media(max-width:560px){
    .topbar{padding:16px 16px 0;} .topbar h1{font-size:20px;}
    .content{padding:13px 16px 64px;}
    .stats{gap:10px;} .stat{padding:14px 15px;} .stat .num{font-size:25px;}
    .panel{padding:18px 16px;} .post{padding:16px;}
    .sec{flex-wrap:wrap;gap:8px;} .sec .ln{display:none;} .sec h2{font-size:15px;}
    .inline{flex-wrap:wrap;} .inline>div{flex:1 1 92px;} .inline button{width:100%;}
    .actions{gap:8px;}
    .actions form, .actions>a.btn{flex:1 1 150px;} .actions .btn{width:100%;}
    .actions .sched{flex:1 1 100%;flex-wrap:wrap;}
    .actions .sched input[type=datetime-local]{width:100%;}
    .btn{padding:11px 14px;}
  }
</style></head><body>
<div id="genOverlay" class="gen-overlay">
  <div class="gen-card">
    <div class="gen-spin"></div>
    <div class="gen-title">Creating your post</div>
    <div id="genMsg" class="gen-msg">Starting</div>
    <div class="gen-track"><div id="genBar" class="gen-bar"></div></div>
    <button id="genClose" class="btn outline" style="display:none;margin-top:18px;" onclick="document.getElementById('genOverlay').style.display='none';">Dismiss</button>
  </div>
</div>
<div id="acctOverlay" class="gen-overlay">
  <div class="gen-card acct-card">
    <div class="gen-title" style="margin-bottom:4px;">Add an account</div>
    <p class="acct-hint">Enter the business name and website. We read the site and auto-build the brand voice, services, content themes, and deep links. You can review everything after.</p>
    <form id="acctForm" method="post" action="{{ url_for('account_new') }}" onsubmit="return startOnboard();">
      <input name="name" placeholder="Business name (e.g. SparkleClean Co.)" required>
      <input name="website" placeholder="Website (e.g. https://sparkleclean.com)" required>
      <div class="acct-row">
        <label>Brand colors</label>
        <input type="color" name="accent" value="#2ecc71" title="Primary accent">
        <input type="color" name="accent2" value="#2b6cc4" title="Secondary accent">
      </div>
      <input name="fb_page_id" placeholder="Facebook Page ID (optional)">
      <input name="fb_token" placeholder="Facebook Page access token (optional)">
      <div class="acct-actions">
        <button type="button" class="btn outline" onclick="document.getElementById('acctOverlay').style.display='none';">Cancel</button>
        <button type="submit" class="btn primary">Build account</button>
      </div>
    </form>
    <div id="onbProg" style="display:none;text-align:center;margin-top:8px;">
      <div class="gen-spin"></div>
      <div id="onbMsg" class="gen-msg">Reading the website...</div>
      <button id="onbClose" class="btn outline" style="display:none;" onclick="document.getElementById('acctOverlay').style.display='none';">Dismiss</button>
    </div>
  </div>
</div>
<div class="layout">
  <aside class="sidebar">
    <div class="side-logo"><img src="{{ url_for('brand', name='logo_full.png') }}?v={{ current_slug }}" alt="{{ current_name }}"></div>
    <div class="acct-switch">
      <select id="acctSelect" onchange="switchAcct(this.value)">
        {% for a in accounts %}<option value="{{ a.slug }}" {{ 'selected' if a.slug==current_slug }}>{{ a.name }}</option>{% endfor %}
      </select>
      <button type="button" class="acct-add" title="Add account" onclick="document.getElementById('acctOverlay').style.display='flex';">+ Add</button>
    </div>
    <nav class="side-nav">
      <a href="{{ url_for('overview') }}" class="{{ 'active' if page=='overview' }}">Overview</a>
      <a href="{{ url_for('review') }}" class="{{ 'active' if page=='review' }}">In Review <span class="n">{{ pending|length }}</span></a>
      <a href="{{ url_for('approved_page') }}" class="{{ 'active' if page=='approved' }}">Ready <span class="n">{{ ready|length }}</span></a>
      <a href="{{ url_for('scheduled_page') }}" class="{{ 'active' if page=='scheduled' }}">Scheduled <span class="n">{{ scheduled|length }}</span></a>
      <a href="{{ url_for('published') }}" class="{{ 'active' if page=='published' }}">Published <span class="n">{{ posted_total }}</span></a>
    </nav>
    <div class="side-foot">
      {% if meta_ready %}<span class="pill on"><span class="dot"></span>Connected</span>
      {% else %}<span class="pill off"><span class="dot"></span>Publishing off</span>{% endif %}
    </div>
  </aside>

  <main class="main">
    <div class="topbar">
      <h1>{{ page_title }}</h1>
      <p class="sub">{{ current_name }} &middot; PlungePost</p>
    </div>
    <div class="content">
      {% with msgs = get_flashed_messages(with_categories=true) %}
        {% for cat,m in msgs %}<div class="flash {{cat}}">{{m}}</div>{% endfor %}
      {% endwith %}
      {% if not meta_ready %}<div class="warn">Facebook keys not set (META_PAGE_ID / META_PAGE_ACCESS_TOKEN). You can generate, review, and schedule, but publishing is disabled.</div>{% endif %}

      {% if page == 'overview' %}
      <section class="stats">
        <a class="stat" href="{{ url_for('review') }}"><div class="num">{{ pending|length }}</div><div class="lbl">In Review</div></a>
        <a class="stat g" href="{{ url_for('approved_page') }}"><div class="num">{{ ready|length }}</div><div class="lbl">Ready</div></a>
        <a class="stat a" href="{{ url_for('scheduled_page') }}"><div class="num">{{ scheduled|length }}</div><div class="lbl">Scheduled</div></a>
        <a class="stat b" href="{{ url_for('published') }}"><div class="num">{{ posted_total }}</div><div class="lbl">Published</div></a>
      </section>

      <div class="sec"><h2>Create</h2><span class="ln"></span></div>
      <div class="create">
        <div class="panel">
          <h3>Custom post</h3>
          <p class="hint">Holidays, promotions, events, announcements. Describe it and we write an on-brand post + graphic.</p>
          <form class="genform" method="post" action="{{ url_for('generate_custom') }}">
            <textarea name="topic" placeholder="e.g. Memorial Day: honoring those who served. Office closed Monday.  —or—  Summer offer: free security assessment for new Austin clients booked in June."></textarea>
            <div class="inline" style="margin-top:12px;">
              <div>
                <label>Style (optional)</label>
                <select name="format">
                  <option value="">Auto (let it pick)</option>
                  {% for f in formats %}<option value="{{f.id}}">{{f.id}}</option>{% endfor %}
                </select>
              </div>
              <button class="btn primary" type="submit">Create post</button>
            </div>
          </form>
        </div>
        <div class="panel">
          <h3>From theme rotation</h3>
          <p class="hint">Generate on-brand posts across the content themes.</p>
          <form class="genform" method="post" action="{{ url_for('generate') }}">
            <label>How many?</label>
            <div class="inline">
              <div><input type="number" name="count" value="1" min="1" max="50"></div>
              <button class="btn primary" type="submit">Generate</button>
            </div>
          </form>
        </div>
        <div class="panel full">
          <h3>Auto-pilot schedule</h3>
          <p class="hint">When on, approving posts auto-schedules them across these days and times &mdash; one post per slot, each on its own day. They appear on the Scheduled tab and publish automatically at their time. (Turn off to approve into Ready without scheduling.)</p>
          <form method="post" action="{{ url_for('settings_save') }}">
            <div class="toggle">
              <input type="checkbox" id="ap" name="auto_pilot_enabled" {{ 'checked' if settings.auto_pilot_enabled }}>
              <label for="ap">Enable auto-pilot</label>
            </div>
            <label>Post on these days</label>
            <div class="days">
              {% for d, lbl in day_options %}
              <label class="daybox"><input type="checkbox" name="day_{{ d }}" {{ 'checked' if d in settings.auto_pilot_days }}> {{ lbl }}</label>
              {% endfor %}
            </div>
            <label>At these times (one post per slot &middot; leave blank to skip)</label>
            <div class="inline">
              <div><input type="time" name="time_1" value="{{ settings.auto_pilot_times[0] if settings.auto_pilot_times|length > 0 else '' }}"></div>
              <div><input type="time" name="time_2" value="{{ settings.auto_pilot_times[1] if settings.auto_pilot_times|length > 1 else '' }}"></div>
              <div><input type="time" name="time_3" value="{{ settings.auto_pilot_times[2] if settings.auto_pilot_times|length > 2 else '' }}"></div>
              <button class="btn outline" type="submit">Save schedule</button>
            </div>
          </form>
        </div>
      </div>
      {% endif %}

      {% if page == 'review' %}
      <div class="sec"><h2>In review</h2><span class="count">{{ pending|length }}</span><span class="ln"></span>
        {% if pending %}<form method="post" action="{{ url_for('approve_all') }}" onsubmit="return confirm('Approve all {{ pending|length }} post(s)?')"><button class="btn primary sm">Approve all</button></form>
        <form method="post" action="{{ url_for('clear_pending') }}" onsubmit="return confirm('Delete ALL posts in review? This cannot be undone.')"><button class="btn danger sm">Delete all</button></form>{% endif %}</div>
      {% if not pending %}<div class="empty">Nothing in review. Go to <a href="{{ url_for('overview') }}" style="color:var(--green);">Overview</a> to create posts.</div>{% endif %}
      {% for p in pending %}{{ render_post(p, "pending", meta_ready, now_local) }}{% endfor %}
      {% endif %}

      {% if page == 'approved' %}
      <div class="sec"><h2>Ready to publish</h2><span class="count">{{ ready|length }}</span><span class="ln"></span>
        {% if ready %}<form method="post" action="{{ url_for('schedule_all') }}" onsubmit="return confirm('Schedule all {{ ready|length }} ready post(s) across your auto-pilot days/times?')"><button class="btn primary sm">Schedule all</button></form>
        <form method="post" action="{{ url_for('clear_approved') }}" onsubmit="return confirm('Delete ALL ready posts? This cannot be undone.')"><button class="btn danger sm">Delete all</button></form>{% endif %}</div>
      {% if not ready %}<div class="empty">Nothing approved yet. Approve posts from <a href="{{ url_for('review') }}" style="color:var(--green);">In Review</a>.</div>{% endif %}
      {% for p in ready %}{{ render_post(p, "approved", meta_ready, now_local) }}{% endfor %}
      {% endif %}

      {% if page == 'scheduled' %}
      <div class="sec"><h2>Scheduled</h2><span class="count">{{ scheduled|length }}</span><span class="ln"></span>
        {% if scheduled %}<form method="post" action="{{ url_for('clear_scheduled') }}" onsubmit="return confirm('Delete ALL scheduled posts? This cannot be undone.')"><button class="btn danger sm">Delete all</button></form>{% endif %}</div>
      {% if not scheduled %}<div class="empty">No scheduled posts. Schedule one from <a href="{{ url_for('approved_page') }}" style="color:var(--green);">Ready to Publish</a>.</div>{% endif %}
      {% for p in scheduled %}{{ render_post(p, "scheduled", meta_ready, now_local) }}{% endfor %}
      {% endif %}

      {% if page == 'published' %}
      <div class="sec"><h2>Published</h2><span class="count">{{ posted_total }}</span><span class="ln"></span></div>
      {% if not history %}<div class="empty">No posts published yet.</div>{% endif %}
      {% for p in history %}{{ render_post(p, "history", meta_ready, now_local) }}{% endfor %}
      {% endif %}
    </div>
  </main>
</div>
<script>
(function(){
  var ov=document.getElementById('genOverlay'),bar=document.getElementById('genBar'),msg=document.getElementById('genMsg');
  var was=false;
  function poll(){
    fetch('{{ url_for("gen_status") }}',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
      if(s.active){
        was=true; ov.style.display='flex';
        var lbl=s.message||'Working'; if(s.total>1){lbl+=' · '+s.done+' of '+s.total+' done';}
        msg.textContent=lbl;
        var pct=s.total?Math.round(s.done/s.total*100):8; if(!pct)pct=8;
        bar.style.width=Math.max(6,pct)+'%';
        setTimeout(poll,700);
      } else if(was){
        if(s.error){
          msg.textContent=s.message; msg.style.color='#ff9aa8';
          bar.style.background='#d6455f'; bar.style.width='100%';
          document.getElementById('genClose').style.display='inline-flex';
          return;
        }
        msg.textContent='Done'; bar.style.width='100%';
        setTimeout(function(){window.location.href='{{ url_for("review") }}';},350);
      } else {
        ov.style.display='none'; setTimeout(poll,2000);
      }
    }).catch(function(){setTimeout(poll,2500);});
  }
  document.querySelectorAll('form.genform').forEach(function(f){
    f.addEventListener('submit',function(){ov.style.display='flex';msg.textContent='Starting';bar.style.width='6%';});
  });
  poll();
})();
function switchAcct(slug){
  var f=document.createElement('form');f.method='post';f.action='/switch/'+slug;
  document.body.appendChild(f);f.submit();
}
function startOnboard(){
  var form=document.getElementById('acctForm');
  document.getElementById('onbProg').style.display='block';
  var data=new FormData(form);
  fetch(form.action,{method:'POST',body:data}).then(function(){pollOnboard();});
  return false;
}
function pollOnboard(){
  var msg=document.getElementById('onbMsg');
  fetch('/onboard-status').then(function(r){return r.json();}).then(function(s){
    if(s.active){msg.textContent=s.message;msg.style.color='#9fb0c0';setTimeout(pollOnboard,900);}
    else if(s.error){msg.textContent=s.message;msg.style.color='#ff9aa8';
      document.getElementById('onbClose').style.display='inline-flex';}
    else if(s.slug){msg.textContent='Done! Loading '+s.slug+'...';
      setTimeout(function(){window.location.href='{{ url_for("overview") }}';},600);}
    else {setTimeout(pollOnboard,900);}
  }).catch(function(){setTimeout(pollOnboard,1500);});
}
</script>
</body></html>
"""


def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()


# Run the one-time migration at import so it happens under any entrypoint
# (python src/webapp.py, gunicorn, etc.), before the first request is served.
try:
    _migrate_legacy_account()
except Exception:  # noqa: BLE001
    logger.exception("Legacy-account migration failed (continuing).")


if __name__ == "__main__":
    _start_scheduler()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
