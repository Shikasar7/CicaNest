#!/bin/sh
# Restart the QQ containers once a week (com.nest.qq-restart.plist) to give back memory QQ gathers over
# days. Each bot account drops for about 15 seconds and logs back in by itself. Only containers that exist
# and are running are restarted, one after another, so the accounts are never all down at once.
set -u
DOCKER=/usr/local/bin/docker
LOG="$(cd "$(dirname "$0")/../.." && pwd)/data/logs/qq-restart.log"  # nest's own logs folder
# Every NapCat container (the compose file's three and any added on the console's Bot page), and SnowLuma
# if it is back.
CONTAINERS="$("$DOCKER" ps --format '{{.Names}}' | grep -E '^napcat-' | sort) snowluma"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }
mem() { "$DOCKER" stats --no-stream --format "{{.MemUsage}}" "$1" 2>&1; }
running() { [ "$("$DOCKER" inspect -f "{{.State.Running}}" "$1" 2>/dev/null)" = "true" ]; }

for name in $CONTAINERS; do
  running "$name" || continue
  echo "$(stamp) $name before: $(mem "$name")" >> "$LOG"
  if "$DOCKER" restart "$name" >> "$LOG" 2>&1; then
    sleep 90
    echo "$(stamp) $name after: $(mem "$name")" >> "$LOG"
  else
    echo "$(stamp) $name restart failed" >> "$LOG"
  fi
done
