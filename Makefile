# SAHIIX AGI v2.5.0-omega — Makefile
# One-command operations for the entire ecosystem

.PHONY: help test unit integration start stop restart status deploy image clean logs

help: ## Show this help message
	@echo "SAHIIX AGI v2.5.0-omega — Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

unit: ## Run unit tests (44 tests)
	cd /home/sahiix/sahiix-agi && source venv/bin/activate && python -m pytest tests/ -q

integration: ## Run integration tests (16 tests)
	cd /home/sahiix/sahiix-agi && source venv/bin/activate && python test_integration.py

test: ## Run both unit and integration tests
	@echo "[1/2] Running unit tests..."
	$(MAKE) unit
	@echo "[2/2] Running integration tests..."
	$(MAKE) integration

start: ## Start web server and infrastructure
	@echo "Starting SAHIIX AGI v2.5..."
	bash /home/sahiix/bin/restart-sahiix all

stop: ## Stop web server and Docker containers
	@echo "Stopping SAHIIX AGI..."
	lsof -t -i :7777 | xargs -r kill -9 2>/dev/null || true
	lsof -t -i :9092 | xargs -r kill -9 2>/dev/null || true
	cd /home/sahiix/sahiix-agi && docker compose down 2>/dev/null || true
	@echo "Stopped."

restart: ## Full restart (web + infrastructure)
	$(MAKE) stop
	$(MAKE) start

status: ## Show full system status
	@echo "=== SAHIIX AGI v2.5.0-omega STATUS ==="
	@echo "Web: $$(curl -s --max-time 2 http://localhost:7777/api/health 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get(\"version\",\"DOWN\"))' 2>/dev/null)"
	@echo "Prometheus: $$(docker ps --format='{{.Status}}' --filter name=sahiix-prometheus 2>/dev/null || echo DOWN)"
	@echo "Grafana: $$(docker ps --format='{{.Status}}' --filter name=sahiix-grafana 2>/dev/null || echo DOWN)"
	@echo "Redis: $$(docker exec sahiix-redis redis-cli ping 2>/dev/null || echo DOWN)"
	@echo "Qdrant: $$(curl -s --max-time 2 http://localhost:6333/collections 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(\"UP\" if d.get(\"status\")==\"ok\" else \"DOWN\")' 2>/dev/null || echo DOWN)"
	@echo "Metrics Exporter: $$(curl -s --max-time 2 http://localhost:9092/metrics 2>/dev/null | head -1 | cut -c1-30)"
	@echo "Docker Image: $$(docker images sahiix-agi --format '{{.Tag}}' 2>/dev/null | head -1)"

deploy: ## Build Docker image and push (manual step for pushing)
	cd /home/sahiix/sahiix-agi && docker build -t sahiix-agi:v2.5.0-omega -t sahiix-agi:latest .
	@echo "Docker image built. To push: docker push sahiix-agi:latest"

image: ## Show Docker image info
	@echo "SAHIIX-AGI Images:"
	@docker images sahiix-agi --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

clean: ## Clean caches, logs, and temporary files
	find /tmp -name "*.py[co]" -delete 2>/dev/null
	find /tmp -name "kimi-webbridge*" -mmin +30 -delete 2>/dev/null
	find /tmp -name "*screenshot*.png" -mmin +30 -delete 2>/dev/null
	journalctl --vacuum-time=7d 2>/dev/null
	rm -f /home/sahiix/sahiix-agi/logs/*.log 2>/dev/null
	@echo "Cleanup complete."

logs: ## Show recent logs
	@echo "=== Web Server ==="
	tail -20 /tmp/sahiix-agi.log 2>/dev/null || echo "No web logs"
	@echo "=== Metrics ==="
	tail -20 /tmp/sahiix-metrics.log 2>/dev/null || echo "No metrics logs"
	@echo "=== Docker ==="
	docker logs --tail 10 sahiix-prometheus 2>/dev/null || echo "Prometheus logs unavailable"

commit: ## Git commit all changes with a message
	cd /home/sahiix/sahiix-agi && git add -A && git commit -m "wip: $(shell date +%Y-%m-%d-%H%M)"
