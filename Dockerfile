# Serve the churn API. Build:  docker build -t eo-churn .
# Run:   docker run -p 8000:8000 --env-file .env eo-churn
FROM python:3.13-slim

WORKDIR /app

# Install dependencies first so this layer is cached when only code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project (data/ and saved_models/ are included so the API starts
# with a working model; see .dockerignore for what is left out).
COPY . .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
