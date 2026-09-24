#!/usr/bin/env bash
# Demoyu tek komutla ayağa kaldırır.
#
#     ./scripts/demo_up.sh
#
# Yaptığı: eksikse .env.docker'ı örnekten üretir, dashboard klasörü yan yanaysa
# onu da yığına ekler, imajları kurar, servisleri başlatır ve API sağlıklı
# raporlayana kadar bekler. Bitince açılacak adresleri yazar.
#
# Tekrar tekrar çalıştırılabilir: init adımlarının üçü de idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env.docker ]; then
  cp .env.docker.example .env.docker
  echo "   .env.docker yoktu, örnekten oluşturuldu."
  echo "   Telegram/e-posta kullanacaksan içini doldur, sonra tekrar çalıştır."
fi

# .env.docker'ı ASLA shell olarak source etmiyoruz. Dosyada tırnaksız bir boşluk
# ya da özel karakter (ör. NOTIFY_TITLE=EO-Churn — Uyarı) olduğunda shell onu
# komut sanıp patlıyor. Aşağıdaki iki yol da güvenli:
#   - compose'a `--env-file` veriyoruz; dosyayı kendisi (shell'siz) parse ediyor.
#   - script'in kendi ihtiyacı olan birkaç değeri tek tek, düz metin okuyoruz.
env_get() {
  # env_get ANAHTAR [varsayilan]
  local value
  value="$(sed -n "s/^[[:space:]]*$1[[:space:]]*=//p" .env.docker | tail -n 1)" || true
  # baş/son boşluk ve varsa çevreleyen tırnakları at
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value:-${2-}}"
}

API_PORT="$(env_get API_PORT 8000)"
DASHBOARD_PORT="$(env_get DASHBOARD_PORT 5173)"
DB_PORT="$(env_get DB_PORT 5432)"
POSTGRES_USER="$(env_get POSTGRES_USER postgres)"
# Sadece ekrana yazmak için: zamanlayıcı servisinin saatini demo sonunda
# söylüyoruz ki "günlük koşuyu kim tetikliyor" sorusu açıkta kalmasın (B-14).
RUN_AT="$(env_get RUN_AT 09:00)"
SCHEDULER_TIMEZONE="$(env_get SCHEDULER_TIMEZONE Europe/Istanbul)"

FILES=(--env-file .env.docker -f docker-compose.yml)
if [ -d ../Eo-Churn-Dashboard-demo-Edu ]; then
  FILES+=(-f docker-compose.dashboard.yml)
  echo "-> dashboard klasörü bulundu, o da başlatılacak"
else
  echo "-> dashboard klasörü yok, sadece backend başlatılıyor"
fi

echo "-> imajlar kuruluyor ve servisler başlatılıyor (ilk sefer birkaç dakika sürer)"
docker compose "${FILES[@]}" up -d --build

echo -n "-> API hazır olması bekleniyor "
for _ in $(seq 1 60); do
  # "degraded" de 200 döner (B-20): durumu okumadan beklemek, hiç skorlayamayan
  # bir API'yi "hazır" ilan etmek olur.
  if curl -fsS "http://localhost:${API_PORT}/health" 2>/dev/null | grep -q '"status":"ok"'; then
    echo " hazır."
    echo
    echo "   API       http://localhost:${API_PORT}/docs"
    [ -d ../Eo-Churn-Dashboard-demo-Edu ] && echo "   Dashboard http://localhost:${DASHBOARD_PORT}"
    echo "   Postgres  localhost:${DB_PORT} (kullanıcı ${POSTGRES_USER})"
    echo "   Zamanlayıcı: günlük koşu ${RUN_AT} ${SCHEDULER_TIMEZONE}"
    echo "                log:  docker compose --env-file .env.docker logs -f scheduler"
    echo
    echo "   Günlük listeyi sıfırlamak için:  ./scripts/demo_reset.sh"
    exit 0
  fi
  echo -n "."
  sleep 3
done

echo
echo "HATA: API 3 dakikada hazır olmadı. Log:"
docker compose "${FILES[@]}" logs --tail 40 api init
exit 1
