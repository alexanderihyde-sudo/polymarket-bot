#!/bin/bash
# External watchdog: keeps the paper bot alive unattended.
# - process gone                            -> restart immediately
# - process hung (4 bad health checks ~2min) -> kill + restart
# Process-existence first so it never races a deliberate restart;
# every 10th healthy check logs a one-line snapshot.
cd "$(dirname "$0")"
fails=0; beat=0
while true; do
  if ! pgrep -f "bot.py paper" >/dev/null; then
    echo "$(date -u +%FT%TZ) watchdog: process gone, restarting" >> watchdog.log
    nohup caffeinate -i python3 bot.py paper >> bot.log 2>&1 &
    fails=0; sleep 90
  else
    ok=$(curl -s --max-time 8 http://localhost:8765/api/health \
         | python3 -c "import json,sys; print(json.load(sys.stdin).get('ok'))" 2>/dev/null)
    if [ "$ok" = "True" ]; then
      fails=0; beat=$((beat+1))
      if [ $((beat % 10)) -eq 0 ]; then
        echo "$(date -u +%FT%TZ) watchdog: healthy" >> watchdog.log
      fi
    else
      fails=$((fails+1))
    fi
    if [ "$fails" -ge 4 ]; then
      echo "$(date -u +%FT%TZ) watchdog: hung, restarting" >> watchdog.log
      pkill -f "bot.py paper"; sleep 3
      nohup caffeinate -i python3 bot.py paper >> bot.log 2>&1 &
      fails=0; sleep 90
    fi
  fi
  tail -500 watchdog.log > watchdog.tmp 2>/dev/null && mv watchdog.tmp watchdog.log
  sleep 30
done
