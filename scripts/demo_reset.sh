#!/usr/bin/env bash
# Demoyu ilk günkü hâline döndürür: alert log'u temizler, tek bir günlük koşu bırakır.
#
#     ./scripts/demo_reset.sh
#
# Neden gerekiyor: `status` alanı (new / still_at_risk) bir ÖNCEKİ koşuya göre
# hesaplanıyor. İki toplantı üst üste yaparsan, ikinci müşteri "0 tanesi yeni"
# yazan bir mesaj ve hiç detay içermeyen bir liste görür. Bu script onu engeller.
set -euo pipefail
cd "$(dirname "$0")/.."

# .env.docker shell olarak source EDİLMİYOR - bkz. demo_up.sh'daki not.
env_get() {
  local value
  value="$(sed -n "s/^[[:space:]]*$1[[:space:]]*=//p" .env.docker | tail -n 1)" || true
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value:-${2-}}"
}

POSTGRES_USER="$(env_get POSTGRES_USER postgres)"
POSTGRES_DB="$(env_get POSTGRES_DB eo_churn)"

# Dashboard dosyasini da ekliyoruz: aksi halde compose dashboard servisini
# tanimadigi icin onu "orphan container" sanip her komutta sari uyari basiyor.
# Demo sirasinda musterinin ekraninda hata gibi gorunmesin.
COMPOSE=(docker compose --env-file .env.docker -f docker-compose.yml)
if [ -d ../Eo-Churn-Dashboard-demo-Edu ]; then
  COMPOSE+=(-f docker-compose.dashboard.yml)
fi

echo "-> alert log temizleniyor"
"${COMPOSE[@]}" exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'TRUNCATE alerts;'

echo "-> günlük veri yeniden yükleniyor"
"${COMPOSE[@]}" run --rm --no-deps api python scripts/load_daily_students.py

echo "-> tek bir günlük koşu yapılıyor"
"${COMPOSE[@]}" run --rm --no-deps api python -m pipeline.daily_pipeline

echo
echo "   Hazır. Listedeki herkes artık 'yeni' - mesaj da detaylı gelir."
echo "   Mesajı görmek için:"
echo "     ./scripts/demo_message.sh"
