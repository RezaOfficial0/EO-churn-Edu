#!/usr/bin/env bash
# Demoyu temiz bir hale döndürür: alert log'u temizler, dünü taklit eden bir koşu
# ve bugünün gerçek koşusunu bırakır.
#
#     ./scripts/demo_reset.sh               # varsayılan: dün + bugün (trend okları görünür)
#     ./scripts/demo_reset.sh --no-history  # sadece bugün (herkes "yeni")
#
# Neden gerekiyor: `status` alanı (new / still_at_risk) bir ÖNCEKİ koşuya göre
# hesaplanıyor. İki toplantı üst üste yaparsan, ikinci müşteri "0 tanesi yeni"
# yazan bir mesaj ve hiç detay içermeyen bir liste görür. Bu script onu engeller.
#
# Neden "dün" de ekleniyor: mesajın iki bölümü sadece bir önceki koşu varken
# oluşuyor - "önceki koşuda da uyarı verilmişti" özeti ve yanındaki oklar
# ("%56 ↑ (önceki %42)"). Tek koşuyla ikisi de hiç görünmez. Dün koşusu
# scripts/seed_demo_history.py ile, bugünün skorlarından SENTETİK olarak üretilir;
# sadece demo verisi için var, müşteri verisinde asla çalıştırılmaz.
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_HISTORY=1
for arg in "$@"; do
  case "$arg" in
    --no-history) WITH_HISTORY=0 ;;
    *) echo "bilinmeyen parametre: $arg (kullanım: $0 [--no-history])" >&2; exit 2 ;;
  esac
done

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

if [ "$WITH_HISTORY" = 1 ]; then
  echo "-> dünün koşusu (sentetik, sadece demo) kaydediliyor"
  "${COMPOSE[@]}" run --rm --no-deps api python scripts/seed_demo_history.py
fi

echo "-> bugünün günlük koşusu yapılıyor"
"${COMPOSE[@]}" run --rm --no-deps api python -m pipeline.daily_pipeline

echo
if [ "$WITH_HISTORY" = 1 ]; then
  echo "   Hazır. Mesajda hem yeni öğrenciler (detaylı) hem de dünden devam"
  echo "   edenler (trend oklarıyla) görünecek."
else
  echo "   Hazır. Listedeki herkes 'yeni' - mesaj detaylı gelir, trend oku yok."
fi
echo "   Mesajı görmek için:"
echo "     ./scripts/demo_message.sh"
