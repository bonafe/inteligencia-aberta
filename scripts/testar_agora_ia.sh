#!/usr/bin/env bash
# Runs the browser test suite headless and exits non-zero on any failure.
# Needs: python3 and Chrome/Chromium (override with CHROME=/path/to/browser).
#
# Unlike the UltimaJS runner this does not use --virtual-time-budget: the suite touches IndexedDB, whose
# real I/O virtual time does not wait for. Instead it polls the page title, which tests.js sets to
# "PASS n/n passed" or "FAIL k/n passed | test — message || test — message".
set -u
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
# A lista do que o service worker guarda precisa estar em dia (senão um arquivo novo some offline)
python3 "$RAIZ/scripts/gerar_precache.py" --check --raiz "$RAIZ/services/portal/static" --saida "$RAIZ/services/portal/static/agora-ia/precache.json" \
    --prefixo /static/ --pastas agora agora-ia --extras --sem-pagina-raiz || exit 1

cd "$RAIZ/services/portal/static"

CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
[ -n "$CHROME" ] || { echo "Chrome/Chromium not found (set CHROME=...)" >&2; exit 2; }

PORT="${PORT:-8798}"
DEBUG_PORT="${DEBUG_PORT:-9335}"
PROFILE=$(mktemp -d)
python3 -m http.server "$PORT" >/dev/null 2>&1 &
SERVER=$!
"$CHROME" --headless=new --no-sandbox --disable-gpu --user-data-dir="$PROFILE" \
    --remote-debugging-port="$DEBUG_PORT" "http://localhost:$PORT/agora-ia/tests/" >/dev/null 2>&1 &
BROWSER=$!
trap 'kill $SERVER $BROWSER 2>/dev/null; rm -rf "$PROFILE"' EXIT

TITLE=""
for _ in $(seq 1 120); do
    TITLE=$(curl -s "http://localhost:$DEBUG_PORT/json" 2>/dev/null | python3 -c '
import sys, json
try:
    for t in json.load(sys.stdin):
        if t.get("url", "").endswith("/agora-ia/tests/") and t["title"].split(" ")[0] in ("PASS", "FAIL"):
            print(t["title"]); break
except Exception:
    pass')
    [ -n "$TITLE" ] && break
    sleep 0.5
done

[ -n "$TITLE" ] || { echo "Timed out: the suite never finished" >&2; exit 1; }
echo "$TITLE" | sed 's/ | /\n  failing: /;s/ || /\n  failing: /g'
[ "${TITLE%% *}" = "PASS" ]
