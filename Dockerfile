# FastAPI backend. Render (or any host) sets PORT; locally it defaults to 8000.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies and the app package (pyproject.toml + src/app), installed into site-packages.
COPY pyproject.toml ./
COPY src ./src
RUN pip install . \
    && useradd --create-home --uid 10001 appuser

USER appuser
EXPOSE 8000

# Configuration comes from environment variables (PE_MONGO_URI, PE_JWT_SECRET, FRONTEND_URL, ...), not a .env file.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
