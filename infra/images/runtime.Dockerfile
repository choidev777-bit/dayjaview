# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm
ARG POSTGRES_IMAGE=postgres:16.15-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.14

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS dependencies
ARG TARGETARCH
RUN test "${TARGETARCH}" = "arm64"
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy
WORKDIR /workspace
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS app-runtime
ARG TARGETARCH
LABEL org.opencontainers.image.title="DAYJAVIEW runtime" \
      org.opencontainers.image.description="API and worker runtime (local/CI fixture, OCI production live)" \
      io.dayjaview.target-platform="linux/arm64"
RUN test "${TARGETARCH}" = "arm64" \
    && groupadd --gid 10001 dayjaview \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin dayjaview
ENV PATH="/workspace/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace
WORKDIR /workspace
COPY --from=dependencies --chown=10001:10001 /workspace/.venv /workspace/.venv
COPY --chown=10001:10001 apps ./apps
COPY --chown=10001:10001 packages ./packages
COPY --chown=10001:10001 contracts ./contracts
COPY --chown=10001:10001 infra/operations/local_stack.py ./infra/operations/local_stack.py
COPY --chown=10001:10001 infra/operations/live_stack.py ./infra/operations/live_stack.py
COPY --chown=10001:10001 tests/infostock/fixtures ./tests/infostock/fixtures
COPY --chown=10001:10001 tests/market-gateway/fixtures ./tests/market-gateway/fixtures
COPY --chown=10001:10001 tests/reference-data/fixtures ./tests/reference-data/fixtures
USER 10001:10001

FROM app-runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=10s --start-period=10s --retries=12 \
    CMD ["python", "infra/operations/local_stack.py", "probe", "--url", "http://127.0.0.1:8000/api/health"]
CMD ["python", "infra/operations/local_stack.py", "api"]

FROM app-runtime AS worker
CMD ["python", "infra/operations/local_stack.py", "worker-help"]

FROM ${POSTGRES_IMAGE} AS migrations
ARG TARGETARCH
LABEL org.opencontainers.image.title="DAYJAVIEW migrations" \
      org.opencontainers.image.description="Checksum-verified PostgreSQL migration runner (fixture/production)" \
      io.dayjaview.target-platform="linux/arm64"
RUN test "${TARGETARCH}" = "arm64"
COPY --chown=999:999 infra/migrations /migrations
COPY --chown=999:999 infra/deployment/migration-order.sha256 /migration-order.sha256
COPY --chown=999:999 --chmod=0555 infra/operations/local_migrate.sh /usr/local/bin/local-migrate
RUN sed -i 's/\r$//' /usr/local/bin/local-migrate
USER 999:999
ENTRYPOINT ["/usr/local/bin/local-migrate"]
