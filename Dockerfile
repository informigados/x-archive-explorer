FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/scripts/docker-entrypoint.sh

RUN mkdir -p /app/instance /app/uploads && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${XAE_PORT:-5000} --workers ${XAE_GUNICORN_WORKERS:-2} --threads ${XAE_GUNICORN_THREADS:-2} --timeout ${XAE_GUNICORN_TIMEOUT:-60} run:app"]
