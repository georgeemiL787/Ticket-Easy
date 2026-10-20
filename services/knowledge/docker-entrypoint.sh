#!/bin/sh
# Build the search index of each tenant once (it lives in the var/ volume), then serve.
# Embeddings come from Ollama (OLLAMA_URL). If it cannot be reached the index is keyword-only, and says so.
set -e
for tenant in shop_001 noon_eg; do
  if [ ! -f "var/index/$tenant/meta.json" ]; then
    echo "building the index of $tenant"
    python -m team_a ingest --tenant "$tenant" || {
      echo "embeddings unavailable: building a keyword-only index of $tenant"
      python -m team_a ingest --tenant "$tenant" --no-embeddings
    }
  fi
done
exec uvicorn team_a.service:app --host 0.0.0.0 --port 8001
