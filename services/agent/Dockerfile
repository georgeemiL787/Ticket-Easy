# Ticket-Easy agent service (agent + inbox + dashboard). Build from the repository root:
#   docker build -f services/agent/Dockerfile -t ticket-easy-agent .
# (docker compose does this for you: see docker-compose.yml)

# ---- stage 1: build the manager dashboard (React) into services/agent/web/dashboard ----
FROM node:22-slim AS dashboard
WORKDIR /repo
COPY apps/dashboard/package.json apps/dashboard/package-lock.json apps/dashboard/
RUN cd apps/dashboard && npm ci
# the build also checks the generated types against the service's JSON Schemas
COPY apps/dashboard apps/dashboard
COPY services/agent/contracts services/agent/contracts
COPY services/agent/web/shared services/agent/web/shared
RUN cd apps/dashboard && npm run build

# ---- stage 2: the service ----
FROM python:3.12-slim AS service
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/services/agent

# dependencies first, so a code change does not reinstall them
COPY services/agent/requirements.txt ./
RUN pip install -r requirements.txt

# the service itself, installed in place (editable) so it finds config/, data/ and fixtures/ next to the code
COPY services/agent/pyproject.toml ./
COPY services/agent/src src
COPY services/agent/config config
COPY services/agent/data data
COPY services/agent/fixtures fixtures
COPY services/agent/prompts prompts
COPY services/agent/contracts contracts
COPY services/agent/web web
RUN pip install --no-deps -e .
COPY --from=dashboard /repo/services/agent/web/dashboard web/dashboard

# a user without privileges, and a folder it may write to (the SQLite database)
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

ENV AGENT_STORE=sqlite AGENT_DB_PATH=/data/agent.sqlite3 AGENT_LOG_JSON=true
EXPOSE 8010
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "agent_service.api.app:app", "--host", "0.0.0.0", "--port", "8010"]
