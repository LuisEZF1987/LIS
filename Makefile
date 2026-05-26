.PHONY: help setup up down dev migrate seed test clean logs

help: ## Mostrar ayuda
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Wizard de instalacion interactivo
	@bash scripts/setup.sh

up: ## Iniciar todos los servicios
	docker compose up -d

down: ## Detener todos los servicios
	docker compose down

dev: ## Modo desarrollo (hot-reload)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

migrate: ## Ejecutar migraciones de base de datos
	@bash scripts/migrate.sh

seed: ## Cargar datos de demostración
	@bash scripts/seed-demo.sh

test: ## Ejecutar tests
	docker compose exec lis python -m pytest /app/tests/ -v 2>/dev/null || \
	python -m pytest tests/ -v

logs: ## Ver logs de todos los servicios
	docker compose logs -f

logs-lis: ## Ver logs del LIS
	docker compose logs -f lis

logs-billing: ## Ver logs de facturacion
	docker compose logs -f billing

ps: ## Estado de los servicios
	docker compose ps

restart: ## Reiniciar todos los servicios
	docker compose restart

clean: ## Eliminar volumenes y datos (DESTRUCTIVO)
	@echo "ADVERTENCIA: Esto eliminara todos los datos."
	@read -p "Continuar? (y/N): " confirm && [ "$$confirm" = "y" ] && \
	docker compose down -v || echo "Cancelado."
