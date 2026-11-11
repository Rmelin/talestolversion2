#!/usr/bin/env bash
# Starter talerstolens display i Chromium-kiosk, når backend svarer.
# Kører som den grafiske bruger (se talerstol-kiosk.service).
set -u

URL="${TALERSTOL_URL:-http://localhost:8000/display}"
HEALTH="${TALERSTOL_HEALTH:-http://localhost:8000/api/health}"
ROTATE="${TALERSTOL_ROTATE:-}"          # fx 90 eller 270 for portræt (Wayland/wlr-randr)
OUTPUT="${TALERSTOL_OUTPUT:-HDMI-A-1}"

# Vent på at containeren er klar (Docker starter selv ved boot).
for _ in $(seq 1 180); do
  curl -fsS -m 2 "$HEALTH" >/dev/null 2>&1 && break
  sleep 1
done

if [ -n "$ROTATE" ] && command -v wlr-randr >/dev/null 2>&1; then
  wlr-randr --output "$OUTPUT" --transform "$ROTATE" || true
fi

BROWSER="$(command -v chromium || command -v chromium-browser)"
PROFILE="${XDG_RUNTIME_DIR:-/tmp}/talerstol-chromium"
rm -rf "$PROFILE"

exec "$BROWSER" \
  --kiosk "$URL" \
  --user-data-dir="$PROFILE" \
  --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --disable-features=Translate,TranslateUI \
  --check-for-update-interval=31536000 \
  --overscroll-history-navigation=0 --disable-pinch \
  --autoplay-policy=no-user-gesture-required
