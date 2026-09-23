#!/usr/bin/env bash
# Gunluk uyari mesajini hicbir yere gondermeden ekrana basar.
#
#     ./scripts/demo_message.sh
#
# Toplantida "sistem sabah mentora bunu yaziyor" derken gosterilecek sey bu.
# --dry-run oldugu icin NOTIFY_CHANNELS dolu olsa bile kimseye mesaj gitmez.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose --env-file .env.docker -f docker-compose.yml)
if [ -d ../Eo-Churn-Dashboard-demo-Edu ]; then
  COMPOSE+=(-f docker-compose.dashboard.yml)
fi

"${COMPOSE[@]}" run --rm --no-deps api python scripts/send_daily_alerts.py --dry-run
