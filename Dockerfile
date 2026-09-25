# Serve the churn API. Build:  docker build -t eo-churn .
# Run:   docker run -p 8000:8000 --env-file .env eo-churn
#
# The same image runs all three services in docker-compose.yml (api, init,
# scheduler): same model files, same config.py, same dependencies, different
# command. That is why everything below has to be true for a one-shot script and a
# long-running server at the same time.
#
# Pinned to an exact patch version and an exact Debian release, not `3.13-slim`
# (B-17). A floating tag means the base image can change under a pilot between two
# rebuilds - a new OpenSSL, a new libstdc++, a CatBoost wheel that suddenly behaves
# differently - and nothing in the repo would record that anything moved. A digest
# is stronger still and is the intended end state; to re-pin:
#
#     docker buildx imagetools inspect python:3.13.7-slim-bookworm
#     # then:  FROM python:3.13.7-slim-bookworm@sha256:<digest>
#
# Bump this line deliberately (and rebuild: `docker compose up -d --build`) rather
# than letting it drift - a pin that is never reviewed is an unpatched base image.
FROM python:3.13.7-slim-bookworm

# Unbuffered so `docker compose logs -f scheduler` shows a run as it happens
# instead of when the pipe fills; no .pyc because /app is root-owned and read-only
# to the runtime user below, so bytecode could not be written there anyway and
# Python should not try on every import.
#
# HOME explicitly: Docker's default is /root, which the runtime user below cannot
# write to. Nothing here needs a home directory today, but a dependency that decides
# to cache something in ~ must not turn that into a startup failure.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

WORKDIR /app

# The zone database. The scheduler (B-14) resolves RUN_AT in an IANA zone - the
# default is Europe/Istanbul, i.e. UTC+3 - through the standard library's zoneinfo,
# and the slim base image ships no /usr/share/zoneinfo. Without this the scheduler
# refuses to start rather than silently running the daily alert at 06:00 local.
# A system package on purpose: requirements.txt gains nothing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# The account everything runs as. A fixed uid because the scheduler's state lives on
# a named volume: ownership on a volume is a NUMBER, so a uid that changes between
# rebuilds would leave the volume unwritable. No login shell and no password - this
# user exists to own two directories and run uvicorn.
RUN useradd --create-home --uid 10001 --user-group --shell /usr/sbin/nologin app

# Install dependencies first so this layer is cached when only code changes.
# --no-cache-dir: a pip cache in a layer is tens of MB of wheels nothing reads again.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project. saved_models/ is included so the API starts with a working
# model; data/ is ignored WHOLESALE except the daily serving sample, so a real
# customer export dropped into data/ cannot end up in an image (see .dockerignore).
COPY . .

# The two directories the runtime user must write to, and nothing else:
#
#   /app/state  scheduler state (B-14) + the run-quality record (B-28). In compose
#               this is a named volume. Ownership is set HERE because Docker copies
#               the image directory's owner onto a NEW empty volume - which is the
#               only reason the volume ends up writable by `app`. A volume created
#               by a pre-B-17 (root) image keeps its old ownership and has to be
#               fixed once; scripts/demo_up.sh does that, and README documents it.
#   /app/data   only the DIRECTORY, so DATA_SOURCE=csv can create daily_alerts.csv
#               in it. The files inside stay root-owned and read-only: the app has
#               no business rewriting the daily input it was handed.
#
# /app itself stays root-owned. The process that serves student records cannot
# modify the code that serves them.
#
# /app/data is NOT created here on purpose: it exists only because .dockerignore
# re-includes data/daily_data.csv, so if that line is ever lost this `chown` fails
# the BUILD instead of letting the init container fail later on a missing file.
# The build context can arrive with restrictive modes: a file written by an editor,
# an umask of 077, or a file-sync tool lands as 0600, and COPY preserves that. The
# non-root user would then be unable to read the very code it runs
# ("PermissionError: '/app/config.py'"), which is a confusing way to learn about
# file modes at 09:00. `a+rX` makes files readable and directories traversable for
# everyone without granting write, so the code stays read-only for `app`.
RUN chmod -R a+rX /app

RUN mkdir -p /app/state \
    && chown app:app /app/state /app/data

USER app

EXPOSE 8000

# In the image, not only in compose (B-17). A customer running `docker run` or a
# k8s Deployment gets no liveness signal from a compose-only healthcheck, and this
# image's default command is the API, so the API's readiness is the right default.
#
# `status == "ok"`, not merely HTTP 200: /health answers 200 with
# {"status": "degraded"} when the model, the explainer, the calibrator or the meta
# failed to load, and a container that can never score must not report healthy
# (B-20). /health needs no API key, so this works with B-07's fail-closed auth.
#
# docker-compose.yml overrides this per service: the scheduler has its own
# (state-file based) check and `init` disables it, because neither serves HTTP.
# start-period covers the cold catboost + shap import.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import json,urllib.request,sys; sys.exit(0 if json.load(urllib.request.urlopen('http://localhost:8000/health', timeout=3)).get('status') == 'ok' else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
