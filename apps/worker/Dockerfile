# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS dependencies
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /workspace/apps/api
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime
ARG BUILD_VERSION=compose-local
LABEL org.opencontainers.image.title="CustomerCare AI Worker" \
      org.opencontainers.image.source="https://github.com/nbnb9/customer-care-ai" \
      org.opencontainers.image.version="${BUILD_VERSION}"
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 customer-care \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin customer-care
WORKDIR /workspace/apps/api
COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=10001:10001 apps/api/app ./app
USER 10001:10001
ENTRYPOINT ["python", "-m", "app.container_entrypoint"]
CMD ["python", "-m", "app.modules.knowledge.worker", "--poll-seconds", "2"]
