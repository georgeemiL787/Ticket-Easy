# One-command demo of the whole stack (needs Docker). The service's own commands are in team_b/Makefile.
# make demo        start the demo (no AI model) and fill the dashboard with example conversations
# make demo-full   the same plus a local AI model (Ollama, qwen3:8b: the first start downloads about 5 GB)
# make down        stop it (your data is kept)   make logs   follow what it is doing

PORT ?= 8010
COMPOSE = docker compose

.PHONY: demo demo-full down logs reset

demo:
	$(COMPOSE) --profile light up -d --build --wait
	$(COMPOSE) --profile light exec team-b python -m team_b seed-demo
	@$(COMPOSE) --profile light exec team-b python -m team_b create-user --email admin@example.com --name Admin --role admin --generate-password --if-missing
	@$(MAKE) --no-print-directory urls

demo-full:
	$(COMPOSE) --profile full up -d --build --wait
	$(COMPOSE) --profile full exec team-b-full python -m team_b seed-demo
	@$(COMPOSE) --profile full exec team-b-full python -m team_b create-user --email admin@example.com --name Admin --role admin --generate-password --if-missing
	@$(MAKE) --no-print-directory urls

down:
	$(COMPOSE) --profile light --profile full down

logs:
	$(COMPOSE) --profile light --profile full logs -f --tail=100

# stop and delete the stored data too (the database and the downloaded model)
reset:
	$(COMPOSE) --profile light --profile full down -v

.PHONY: urls
urls:
	@echo ""
	@echo "Ticket-Easy is running:"
	@echo "  Customer chat   http://localhost:$(PORT)/chat?tenant_id=shop_001"
	@echo "  Support inbox   http://localhost:$(PORT)/inbox"
	@echo "  Manager dashboard http://localhost:$(PORT)/dashboard"
	@echo "  Health          http://localhost:$(PORT)/health"
	@echo "Sign in as admin@example.com. Its password was printed above the first time; lost it? run:"
	@echo "  docker compose exec <service> python -m team_b create-user --email admin@example.com --name Admin --role admin --generate-password --reset-password"
