#!/bin/bash
# Lock-coordinated SHADOW `move` ablation A/B — run hourly by launchd
# (com.alexhyde.polymarket-moveshadow.plist). `bot.py move-shadow` itself defers
# to the AUTOPILOT/TRAINER .autopilot_lock (claims a `movesh` tag while it runs,
# releases when done), so this NEVER runs concurrently with the fleet.
# Measurement only — recomputes champion cv_skill with vs without the `move`
# feature family and appends to move_shadow.json. Deploys nothing.
cd /Users/you/polymarket-bot || exit 1
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    bot.py move-shadow >> move_shadow.cron.log 2>&1
