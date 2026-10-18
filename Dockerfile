FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY lastmile lastmile
COPY packs packs
COPY data data
COPY web web
RUN useradd --create-home app && chown -R app /app
USER app
# Cloud Run sets $PORT. Defaults are the safe demo settings: fake model, CAP status Exercise only.
ENV PORT=8080 LASTMILE_LLM=fake
EXPOSE 8080
# Cloud Run terminates TLS in front of the container and is its only ingress: trust its X-Forwarded-* headers so
# the app sees https (origin check, Secure cookie) and the real client address (per-person rate limits).
CMD ["sh", "-c", "exec uvicorn lastmile.api:app_factory --factory --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=*"]
