# Ticket-Easy knowledge service (policy search + safety/rule checker). Build from the repository root:
#   docker build -f services/knowledge/Dockerfile -t ticket-easy-team-a .
# (docker compose does this for you: see docker-compose.yml, profile "live")

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/services/knowledge/src
WORKDIR /app/services/knowledge

COPY services/knowledge/requirements.txt ./
RUN pip install -r requirements.txt

COPY services/knowledge/src src
COPY services/knowledge/data data
COPY services/knowledge/contracts contracts
COPY services/knowledge/docker-entrypoint.sh ./
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

# a user without privileges, and a folder it may write to (the search index and the SQLite data layer)
RUN useradd --create-home --uid 10001 app && mkdir -p var && chown app var
USER app

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"
CMD ["./docker-entrypoint.sh"]
