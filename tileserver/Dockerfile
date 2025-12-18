# Latest titiler as of 2024-11-06
FROM ghcr.io/developmentseed/titiler@sha256:1436b4f43743c11da3661c90f7b59f1065578bf48fbdf198db2c7235d7293447

COPY ./app /app
RUN python -m pip install matplotlib aiocache[redis] pydantic-settings google-cloud-logging "gunicorn>=22"

ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONPATH=/app

# CWD is /app
CMD python /app/main.py
