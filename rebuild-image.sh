#!/bin/bash
# Full update: pull latest code, REBUILD the Docker image (installs the headless
# Chromium the new HTML/CSS post renderer needs), and recreate the container.
#
# Use this when dependencies or the Dockerfile change (e.g. adding Playwright).
# For plain code changes, `bash update.sh` (pull + restart) is enough.
#
#     bash rebuild-image.sh
set -e
cd "$(dirname "$0")"

echo "Pulling latest code..."
curl -sL https://github.com/madsquilla/plungepost/archive/refs/heads/master.tar.gz \
  | tar xz --strip-components=1

echo "Rebuilding image (installs Chromium; first build takes several minutes)..."
docker build -t plungepost:latest .

echo "Recreating the dashboard container..."
bash recreate-dashboard.sh

echo "Done. HTML/CSS renderer is live."
