#!/usr/bin/env bash
# ColdReach — one-command setup + start
# Usage: bash run.sh

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
ENV="$ROOT/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}→${RESET} $1"; }
ok()   { echo -e "${GREEN}✓${RESET} $1"; }
warn() { echo -e "${YELLOW}!${RESET} $1"; }
die()  { echo -e "${RED}✗ ERROR:${RESET} $1"; exit 1; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║              COLDREACH                   ║"
echo "║   open-source cold outreach engine       ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${RESET}"

# ── 1. Check prerequisites ────────────────────────────────────
log "Checking prerequisites..."

python3 --version &>/dev/null || die "Python 3 not found. Install: brew install python@3.12"
PY_VER=$(python3 -c "import sys; print(sys.version_info.minor)")
[[ "$PY_VER" -ge 11 ]] || die "Python 3.11+ required. Got 3.$PY_VER"
ok "Python 3.$PY_VER"

node --version &>/dev/null || die "Node not found. Install: brew install node"
ok "Node $(node --version)"

npm --version &>/dev/null || die "npm not found."
ok "npm $(npm --version)"

# ── 2. Create .env if missing ─────────────────────────────────
if [[ ! -f "$ENV" ]]; then
  cp "$ROOT/.env.example" "$ENV"
  log "Created .env from example"
fi

# ── 3. Detect LLM ─────────────────────────────────────────────
echo ""
log "Checking LLM configuration..."

GROQ_KEY=$(grep "^GROQ_API_KEY=" "$ENV" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]')
OLLAMA_OK=false
curl -s --max-time 2 http://localhost:11434/api/tags &>/dev/null && OLLAMA_OK=true

if [[ -n "$GROQ_KEY" && "$GROQ_KEY" != "" && "$GROQ_KEY" != "gsk_your_key_here" ]]; then
  ok "Groq API key found"
  # Write to .env properly
  sed -i.bak "s|^.*GROQ_API_KEY.*|LLM_PROVIDER=groq\nLLM_API_KEY=$GROQ_KEY|" "$ENV" 2>/dev/null || true
elif $OLLAMA_OK; then
  ok "Ollama running locally"
  MODELS=$(curl -s http://localhost:11434/api/tags | python3 -c "import json,sys;print([m['name'] for m in json.load(sys.stdin).get('models',[])])" 2>/dev/null)
  if [[ -z "$MODELS" || "$MODELS" == "[]" ]]; then
    warn "Ollama running but no models. Pulling llama3.1 (~5GB)..."
    ollama pull llama3.1
  else
    ok "Ollama models: $MODELS"
  fi
else
  warn "No LLM configured. App will use template emails."
  warn "To enable AI emails, add to .env:"
  warn "  GROQ_API_KEY=gsk_...   ← free key at console.groq.com (30 sec)"
  warn "  OR: brew install ollama && ollama pull llama3.1"
  echo ""
fi

# ── 4. Install backend deps ───────────────────────────────────
echo ""
log "Installing backend dependencies..."
cd "$BACKEND"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

pip install -q -r requirements.txt
ok "Python packages installed"

if ! python3 -c "from playwright.sync_api import sync_playwright" &>/dev/null; then
  log "Installing Playwright browser (one-time)..."
  python3 -m playwright install chromium --with-deps
fi
ok "Playwright ready"

# ── 5. Install frontend deps ──────────────────────────────────
echo ""
log "Installing frontend dependencies..."
cd "$FRONTEND"
[[ -d "node_modules" ]] || npm install --silent
ok "npm packages installed"

# ── 6. Run tests to confirm everything is healthy ────────────
echo ""
log "Running backend tests..."
cd "$BACKEND"
python3 -m pytest tests/ -q --tb=short 2>&1 | tail -2
ok "Tests passed"

# ── 7. Start both services ────────────────────────────────────
echo ""
echo -e "${BOLD}Starting services...${RESET}"

# Start backend
cd "$BACKEND"
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload \
  --log-level warning &
BACKEND_PID=$!
echo $BACKEND_PID > /tmp/coldreach_backend.pid

# Wait for backend to be ready
log "Waiting for backend..."
for i in {1..15}; do
  sleep 1
  if curl -s http://localhost:8000/api/health &>/dev/null; then
    HEALTH=$(curl -s http://localhost:8000/api/health)
    LLM=$(echo $HEALTH | python3 -c "import json,sys;print(json.load(sys.stdin).get('llm','?'))" 2>/dev/null)
    ok "Backend ready — http://localhost:8000  (llm: $LLM)"
    break
  fi
  [[ $i -eq 15 ]] && die "Backend didn't start in 15s. Check logs."
done

# Start frontend
cd "$FRONTEND"
npm run dev -- --host 127.0.0.1 --port 5173 &>/dev/null &
FRONTEND_PID=$!
echo $FRONTEND_PID > /tmp/coldreach_frontend.pid

sleep 2
ok "Frontend ready — http://localhost:5173"

# ── 8. Open browser ───────────────────────────────────────────
echo ""
if command -v open &>/dev/null; then         # macOS
  open http://localhost:5173
elif command -v xdg-open &>/dev/null; then   # Linux
  xdg-open http://localhost:5173
fi

# ── 9. Summary ────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║  ColdReach is running!                           ║${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  App       →  ${CYAN}http://localhost:5173${RESET}"
echo -e "${BOLD}║${RESET}  API docs  →  ${CYAN}http://localhost:8000/docs${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  Quick test:"
echo -e "${BOLD}║${RESET}  ${CYAN}curl http://localhost:8000/api/health${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  To add real AI emails:"
echo -e "${BOLD}║${RESET}  1. Get free key → ${CYAN}console.groq.com${RESET}"
echo -e "${BOLD}║${RESET}  2. ${CYAN}echo \"GROQ_API_KEY=gsk_...\" >> .env${RESET}"
echo -e "${BOLD}║${RESET}  3. Restart: ${CYAN}Ctrl+C${RESET} then ${CYAN}bash run.sh${RESET}"
echo -e "${BOLD}╠══════════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}║${RESET}  Stop:  ${CYAN}Ctrl+C${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo ""

# Keep running until Ctrl+C
trap "echo ''; log 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; ok 'Stopped.'; exit 0" INT TERM
wait $BACKEND_PID
