# Serve the churn API. Build:  docker build -t eo-churn .
# Run:   docker run -p 8000:8000 --env-file .env eo-churn
FROM python:3.13-slim

WORKDIR /app

# The zone database. The scheduler (B-14) resolves RUN_AT in an IANA zone - the
# default is Europe/Istanbul, i.e. UTC+3 - through the standard library's zoneinfo,
# and the slim base image ships no /usr/share/zoneinfo. Without this the scheduler
# refuses to start rather than silently running the daily alert at 06:00 local.
# A system package on purpose: requirements.txt gains nothing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so this layer is cached when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project (data/ and saved_models/ are included so the API starts
# with a working model; see .dockerignore for what is left out).
COPY . .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
