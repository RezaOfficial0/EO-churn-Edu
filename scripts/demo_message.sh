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

# Yığın ayakta değilken bu script'in yapabileceği bir şey yok: compose "service
# db is not running" der, sonraki adım da psycopg2 traceback'i basar ve bu,
# ürünü ilk kez kuran birine "bozuk" gibi görünür. Tek satırla söylüyoruz.
if ! docker compose "${COMPOSE[@]:2}" ps --status running --quiet 2>/dev/null | grep -q .; then
  echo "hata: demo servisleri ayakta değil. Önce: ./scripts/demo_up.sh" >&2
  exit 1
fi

"${COMPOSE[@]}" run --rm --no-deps api python scripts/send_daily_alerts.py --dry-run
