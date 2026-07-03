.PHONY: help setup start dev-backend dev-frontend test clean up down logs

help:           ## Show all commands
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── First-time setup ──────────────────────────────────────────────────────────
setup:          ## Install everything (run once after cloning)
	@echo "→ Installing backend..."
	cd backend && pip install -r requirements.txt
	cd backend && python -m playwright install chromium
	@echo "→ Installing frontend..."
	cd frontend && npm install
	@echo "→ Creating .env..."
	@[ -f .env ] || cp .env.example .env
	@echo ""
	@echo "✓ Done. Now set your LLM:"
	@echo "  Option A (Groq, free): add GROQ_API_KEY=gsk_... to .env"
	@echo "  Option B (Ollama):     brew install ollama && ollama pull llama3.1"
	@echo ""
	@echo "Then run:  make start"

# ── Start both services ───────────────────────────────────────────────────────
start:          ## Start backend + frontend (two tabs)
	@echo "Starting ColdReach..."
	@echo ""
	@echo "  Open two terminal tabs and run:"
	@echo "  Tab 1:  make dev-backend"
	@echo "  Tab 2:  make dev-frontend"
	@echo ""
	@echo "  Backend  → http://localhost:8000"
	@echo "  Frontend → http://localhost:5173"
	@echo "  API docs → http://localhost:8000/docs"

dev-backend:    ## Start FastAPI with hot reload
	cd backend && uvicorn app.main:app --reload --port 8000

dev-frontend:   ## Start Vite dev server
	cd frontend && npm run dev

# ── Docker (all-in-one) ───────────────────────────────────────────────────────
up:             ## Start full stack via Docker Compose
	docker compose up -d
	@echo "→ http://localhost:5173"

down:           ## Stop all containers
	docker compose down

logs:           ## Tail container logs
	docker compose logs -f

# ── Quality ───────────────────────────────────────────────────────────────────
test:           ## Run test suite
	cd backend && python -m pytest tests/ -v

# ── Misc ──────────────────────────────────────────────────────────────────────
clean:          ## Remove build artefacts + local database (keeps .secret_key)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete
	rm -f backend/coldreach.db backend/coldreach.db-shm backend/coldreach.db-wal
	rm -f backend/data/coldreach.db backend/data/coldreach.db-shm backend/data/coldreach.db-wal
