# Ticket-Easy Team B (agent + inbox + dashboard). Build from the repository root:
#   docker build -f team_b/Dockerfile -t ticket-easy-team-b .
# (docker compose does this for you: see docker-compose.yml)

# ---- stage 1: build the manager dashboard (React) into team_b/web/dashboard ----
FROM node:22-slim AS dashboard
WORKDIR /repo
COPY dashboard/package.json dashboard/package-lock.json dashboard/
RUN cd dashboard && npm ci
# the build also checks the generated types against the service's JSON Schemas
COPY dashboard dashboard
COPY team_b/contracts team_b/contracts
RUN cd dashboard && npm run build

# ---- stage 2: the service ----
FROM python:3.12-slim AS service
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/team_b

# dependencies first, so a code change does not reinstall them
COPY team_b/requirements.txt ./
RUN pip install -r requirements.txt

# the service itself, installed in place (editable) so it finds config/, data/ and fixtures/ next to the code
COPY team_b/pyproject.toml ./
COPY team_b/src src
COPY team_b/config config
COPY team_b/data data
COPY team_b/fixtures fixtures
COPY team_b/prompts prompts
COPY team_b/contracts contracts
COPY team_b/web web
RUN pip install --no-deps -e .
COPY --from=dashboard /repo/team_b/web/dashboard web/dashboard

# a user without privileges, and a folder it may write to (the SQLite database)
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

ENV TEAM_B_STORE=sqlite TEAM_B_DB_PATH=/data/team_b.sqlite3 TEAM_B_LOG_JSON=true
EXPOSE 8010
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "team_b.api.app:app", "--host", "0.0.0.0", "--port", "8010"]
