FROM python:3.12-slim-bookworm

WORKDIR /
ENV PYTHONPATH=/

COPY pyproject.toml poetry.lock /
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then \
        export PIP_CERT=/run/secrets/build_ca REQUESTS_CA_BUNDLE=/run/secrets/build_ca; \
    fi; \
    pip install --no-cache-dir poetry==2.2.1 \
    && poetry install --only main --no-interaction

COPY app /app
