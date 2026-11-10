FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    INVOICE_AGENT_STATE_DIR=/app/.state

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[ui,dev]"
COPY data ./data
COPY tests ./tests

RUN useradd --create-home app && mkdir -p /app/.state && chown -R app /app
USER app

EXPOSE 8501
ENTRYPOINT ["invoice-agent"]
CMD ["--help"]
