#!/usr/bin/env bash
# İmaj hijyeni kontrolü (B-17). Kurulmuş bir imaja karşı çalışır, ağ gerektirmez:
#
#     docker compose --env-file .env.docker build
#     ./scripts/check_image.sh                    # varsayılan imaj: eo-churn-api
#     ./scripts/check_image.sh eo-churn:test
#
# Neden bir script: ".dockerignore'a satır ekledim" ile "o dosya gerçekten imajda
# yok" arasındaki farkı sadece kurulmuş imaja bakmak kapatıyor. Bir `COPY` satırının
# yeri, bir glob'un yanlış yazımı ya da başka bir build context, listedeki her
# maddeyi sessizce geçersiz kılabilir - ve bir imaj elden çıktıktan sonra geri
# dönüşü yok.
#
# Kontroller: sır yok, öğrenci verisi yok, root değil, HEALTHCHECK var, base imaj
# sabitlenmiş.
set -uo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-eo-churn-api}"
failed=0

ok()   { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; failed=$((failed + 1)); }
note() { printf '        %s\n' "$1"; }

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "HATA: '$IMAGE' imajı yok. Önce kur:" >&2
  echo "  docker compose --env-file .env.docker build" >&2
  echo "  (ya da imaj adını parametre olarak ver: $0 <imaj>)" >&2
  exit 2
fi

echo "İmaj: $IMAGE"
echo

# --- 1. Sırlar --------------------------------------------------------------
# `docker history | grep -i env` ham haliyle Dockerfile'daki PYTHONUNBUFFERED
# satırına da takılıyor; aradığımız şey bir .env dosyası ya da satır içi bir
# kimlik bilgisi. Bilinen iki güvenli ENV adını ayıklayıp kalanına bakıyoruz.
history_env="$(docker history --no-trunc --format '{{.CreatedBy}}' "$IMAGE" \
  | grep -i 'env' \
  | grep -viE 'PYTHONDONTWRITEBYTECODE|PYTHONUNBUFFERED|PYTHON_(VERSION|SHA256)|GPG_KEY|LANG|PATH=' || true)"
if [ -z "$history_env" ]; then
  ok "docker history: ENV/secret izi yok (PYTHON* satırları beklenen)"
else
  bad "docker history içinde beklenmeyen env satırı var:"
  printf '%s\n' "$history_env" | sed 's/^/        /'
fi

# Dosya sistemi tarafı: hiçbir .env imaja girmemiş olmalı (örnek dosyalar dahil -
# .env.docker.example'ın kendisi sır taşımıyor ama glob'un çalıştığının kanıtı o).
env_files="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  'find / -xdev -name ".env*" -not -path "*/site-packages/*" 2>/dev/null' || true)"
if [ -z "$env_files" ]; then
  ok "imajda hiçbir .env dosyası yok"
else
  bad "imajda .env dosyası var:"
  printf '%s\n' "$env_files" | sed 's/^/        /'
fi

# --- 2. Öğrenci verisi ------------------------------------------------------
# data/ allow-list: sadece günlük servis örneği girmeli. Gerçek müşteri exportu
# geldiği gün buraya düşen dosyanın imaja girmemesi bu satırın tek amacı.
data_files="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  'ls -1 /app/data 2>/dev/null | grep -v "^daily_alerts.csv$"' || true)"
if [ "$data_files" = "daily_data.csv" ]; then
  ok "/app/data sadece daily_data.csv içeriyor"
else
  bad "/app/data beklenenden farklı (sadece daily_data.csv olmalı):"
  printf '%s\n' "${data_files:-<boş>}" | sed 's/^/        /'
  note "boşsa .dockerignore'daki '!data/daily_data.csv' satırı çalışmıyor -"
  note "o dosya olmadan init container'ı günlük veriyi yükleyemez."
fi

for unwanted in /app/tests /app/RnD /app/notebooks /app/.git; do
  if docker run --rm --entrypoint sh "$IMAGE" -c "[ -e '$unwanted' ]" 2>/dev/null; then
    bad "$unwanted imajda (olmamalı)"
  else
    ok "$unwanted imajda değil"
  fi
done

# --- 3. Non-root ------------------------------------------------------------
user="$(docker image inspect -f '{{.Config.User}}' "$IMAGE")"
if [ -n "$user" ] && [ "$user" != "root" ] && [ "$user" != "0" ]; then
  ok "USER=$user (root değil)"
else
  bad "USER boş ya da root - container root olarak koşuyor"
fi

runtime_uid="$(docker run --rm --entrypoint sh "$IMAGE" -c 'id -u' 2>/dev/null || echo "?")"
if [ "$runtime_uid" != "0" ] && [ "$runtime_uid" != "?" ]; then
  ok "çalışma anında uid=$runtime_uid"
else
  bad "çalışma anında uid=$runtime_uid"
fi

# Yazması GEREKEN iki dizin: /app/state (zamanlayıcı durumu + karantina sayacı) ve
# /app/data (DATA_SOURCE=csv alert log'u). Bunlar yazılamazsa non-root geçişi
# demoyu bozar - kontrolün burada olmasının sebebi tam olarak bu.
for writable in /app/state /app/data; do
  if docker run --rm --entrypoint sh "$IMAGE" -c "[ -w '$writable' ]" 2>/dev/null; then
    ok "$writable yazılabilir"
  else
    bad "$writable yazılamıyor - zamanlayıcı/alert log çalışmaz"
  fi
done

# Kodun kendisi yazılamaz olmalı: öğrenci kaydı servis eden süreç onu servis eden
# kodu değiştirememeli.
if docker run --rm --entrypoint sh "$IMAGE" -c '[ -w /app/config.py ]' 2>/dev/null; then
  bad "/app/config.py çalışma kullanıcısı tarafından yazılabilir"
else
  ok "/app kodu çalışma kullanıcısına salt okunur"
fi

# --- 4. HEALTHCHECK ---------------------------------------------------------
if [ "$(docker image inspect -f '{{if .Config.Healthcheck}}yes{{end}}' "$IMAGE")" = "yes" ]; then
  ok "imajda HEALTHCHECK var (compose'a bağlı değil)"
else
  bad "imajda HEALTHCHECK yok - docker run / k8s liveness sinyali alamaz"
fi

# --- 5. Sabitlenmiş base ----------------------------------------------------
base="$(grep -m1 '^FROM ' Dockerfile)"
if printf '%s' "$base" | grep -qE '@sha256:[0-9a-f]{64}'; then
  ok "base imaj digest ile sabit: $base"
elif printf '%s' "$base" | grep -qE 'python:3\.[0-9]+\.[0-9]+-slim-[a-z]+'; then
  ok "base imaj tam sürüm + dağıtım ile sabit: $base"
  note "digest daha güçlü: docker buildx imagetools inspect <tag>"
else
  bad "base imaj sabitlenmemiş: $base"
fi

echo
if [ "$failed" -eq 0 ]; then
  echo "Hepsi geçti."
else
  echo "$failed kontrol başarısız."
fi
exit $((failed > 0))
