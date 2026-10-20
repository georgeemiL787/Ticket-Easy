# Ticket-Easy tools service (turns a shop's API description into approved tools). Build from the repository root:
#   docker build -f services/tools/Dockerfile -t ticket-easy-tools .
# (docker compose does this for you: see docker-compose.yml, profile "live")

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/services/tools
WORKDIR /app/services/tools

# dependencies first, from the lock file, so a code change does not reinstall them
RUN pip install "uv>=0.11,<0.12"
COPY services/tools/pyproject.toml services/tools/uv.lock ./
RUN uv sync --locked --no-dev

COPY services/tools/tools_service tools_service

# a user without privileges, and a folder it may write to (the SQLite database)
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

EXPOSE 8101
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8101/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "tools_service.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8101"]
