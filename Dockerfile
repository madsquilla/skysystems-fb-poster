# SkySystems USA Facebook Auto-Poster
# Run-once-and-exit container. Triggered on a schedule (e.g. Unraid User Scripts).

FROM python:3.11-slim

# Keep Python output unbuffered so container logs appear in real time, and
# default to UTF-8 everywhere.
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Headless Chromium for the HTML/CSS post renderer (htmlrender/htmlcards).
# --with-deps installs the OS libraries Chromium needs on Debian slim.
RUN playwright install --with-deps chromium

# Application code, brand assets (fonts + logo), and seed data.
COPY src/ ./src/
COPY data/ ./data/
COPY template/ ./template/
COPY assets/ ./assets/

# Runs as root so it can write to a bind-mounted Unraid appdata folder
# (owned by nobody:users) without permission errors. This is a homelab,
# outbound-only batch/dashboard job, so root-in-container is acceptable here.

# The web dashboard (when run) listens here. The batch poster needs no ports.
EXPOSE 8080

# main.py reads ANTHROPIC_API_KEY, META_PAGE_ID, META_PAGE_ACCESS_TOKEN from env.
# The web dashboard overrides this entrypoint (see docker-compose.yml).
ENTRYPOINT ["python", "src/main.py"]

# Default mode generates and stages a post (does NOT publish). Override the
# command in your scheduler to pick a mode, e.g.:
#   --mode publish-approved
#   --mode generate-batch --count 7
#   --mode stage --dry-run
CMD ["--mode", "stage"]
