#!/usr/bin/env bash
#
# Plutus — one-command setup and launch for macOS and Linux.
#
# Written for a machine with nothing on it. Every prerequisite is checked,
# installed if missing, and skipped if already present, so running this twice
# is safe and the second run is fast.
#
#   ./scripts/bootstrap.sh            install what's missing, then start
#   ./scripts/bootstrap.sh --no-start install only
#   ./scripts/bootstrap.sh --stop     stop everything this started
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT=8001
FRONTEND_PORT=5173
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

# --- output ---------------------------------------------------------------
# Colour only when attached to a terminal, so piping to a file stays readable.
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
  RED=$'\033[31m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; BLUE=""; RESET=""
fi

step()  { printf "\n%s==>%s %s%s%s\n" "$BLUE" "$RESET" "$BOLD" "$1" "$RESET"; }
ok()    { printf "  %s✓%s %s\n" "$GREEN" "$RESET" "$1"; }
info()  { printf "  %s·%s %s\n" "$DIM" "$RESET" "$1"; }
warn()  { printf "  %s!%s %s\n" "$YELLOW" "$RESET" "$1"; }
die()   { printf "\n  %s✗ %s%s\n\n" "$RED" "$1" "$RESET" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="macos" ;;
  Linux)  PLATFORM="linux" ;;
  *) die "Unsupported platform: $OS. Use scripts/bootstrap.ps1 on Windows." ;;
esac

# --- stop -----------------------------------------------------------------

stop_all() {
  step "Stopping Plutus"
  for name in backend frontend; do
    if [ -f "$RUN_DIR/$name.pid" ]; then
      pid="$(cat "$RUN_DIR/$name.pid")"
      if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; ok "stopped $name (pid $pid)"; fi
      rm -f "$RUN_DIR/$name.pid"
    fi
  done
  if have docker && docker info >/dev/null 2>&1; then
    # `stop`, not `down`: it clears the restart policy without deleting the
    # volumes your filings and vectors live in.
    docker compose stop >/dev/null 2>&1 && ok "stopped database and vector store"
  fi
  printf "\n  %sPlutus stopped.%s Your data is kept.\n\n" "$BOLD" "$RESET"
  exit 0
}

[ "${1:-}" = "--stop" ] && stop_all

# --- package manager ------------------------------------------------------

install_homebrew() {
  have brew && return 0
  [ "$PLATFORM" = "macos" ] || return 1

  warn "Homebrew is needed to install the prerequisites."
  info "It will ask for your password - that is Homebrew's own installer, not this script."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  # A fresh install is not on PATH until the shell is reloaded.
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$candidate" ] && eval "$($candidate shellenv)" && break
  done
  have brew || die "Homebrew installed but is not on PATH. Open a new terminal and re-run."
  ok "Homebrew installed"
}

# --- prerequisites --------------------------------------------------------

PYTHON=""

ensure_python() {
  # 3.11+ is required: the code uses `X | Y` unions and `match` freely.
  for candidate in python3.13 python3.12 python3.11 python3; do
    if have "$candidate" && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      ok "Python $($candidate -V 2>&1 | cut -d' ' -f2)"
      return
    fi
  done

  step "Installing Python 3.12"
  if [ "$PLATFORM" = "macos" ]; then
    install_homebrew
    brew install python@3.12
    PYTHON="$(brew --prefix)/bin/python3.12"
  else
    have apt-get && sudo apt-get update -qq && sudo apt-get install -y python3 python3-venv python3-pip
    PYTHON="python3"
  fi
  have "$PYTHON" || [ -x "$PYTHON" ] || die "Python install failed."
  ok "Python installed"
}

ensure_node() {
  if have node && [ "$(node -v | sed 's/v\([0-9]*\).*/\1/')" -ge 18 ] 2>/dev/null; then
    ok "Node $(node -v)"
    return
  fi
  step "Installing Node.js"
  if [ "$PLATFORM" = "macos" ]; then
    install_homebrew && brew install node
  else
    have apt-get && sudo apt-get install -y nodejs npm
  fi
  have node || die "Node install failed."
  ok "Node $(node -v)"
}

ensure_docker() {
  if ! have docker; then
    step "Installing Docker Desktop"
    if [ "$PLATFORM" = "macos" ]; then
      install_homebrew && brew install --cask docker
      ok "Docker Desktop installed"
    else
      die "Install Docker Engine for your distribution, then re-run: https://docs.docker.com/engine/install/"
    fi
  fi

  if docker info >/dev/null 2>&1; then
    ok "Docker is running"
    return
  fi

  step "Starting Docker"
  [ "$PLATFORM" = "macos" ] && open -a Docker 2>/dev/null || true
  info "waiting for the Docker engine (this can take a minute on first run)…"
  for _ in $(seq 1 120); do
    docker info >/dev/null 2>&1 && { ok "Docker is running"; return; }
    sleep 2
  done
  die "Docker did not start. Open Docker Desktop manually, wait for it to be ready, then re-run."
}

# --- configuration --------------------------------------------------------

ensure_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    ok "created .env from .env.example"
  fi

  if grep -qE '^LLM_API_KEY=.+' .env; then
    ok "LLM API key configured"
    return
  fi

  warn "No LLM API key set yet."
  cat <<'EOF'

  Plutus answers questions with a hosted model. Get a free key from either:

    Google AI Studio   https://aistudio.google.com/apikey     (generous free tier)
    Groq               https://console.groq.com               (fast, smaller free tier)

EOF
  printf "  Paste your API key (or press Enter to skip and add it later): "
  read -r key || true
  if [ -n "${key:-}" ]; then
    # Default to Gemini, which is what most people will have just signed up for.
    if [[ "$key" == gsk_* ]]; then
      base="https://api.groq.com/openai/v1"; model="openai/gpt-oss-120b"
    else
      base="https://generativelanguage.googleapis.com/v1beta/openai/"; model="gemini-3.1-flash-lite"
    fi
    python3 - "$key" "$base" "$model" <<'PY'
import re, sys, pathlib
key, base, model = sys.argv[1:4]
p = pathlib.Path(".env"); t = p.read_text()
for name, value in (("LLM_API_KEY", key), ("LLM_BASE_URL", base), ("LLM_MODEL", model)):
    t = re.sub(rf"(?m)^{name}=.*$", f"{name}={value}", t) if re.search(rf"(?m)^{name}=", t) else t + f"\n{name}={value}\n"
p.write_text(t)
PY
    ok "API key saved to .env"
  else
    warn "Skipped. Add LLM_API_KEY to .env before asking questions."
  fi
}

# --- install --------------------------------------------------------------

setup_backend() {
  step "Backend"
  if [ ! -d backend/.venv ]; then
    "$PYTHON" -m venv backend/.venv
    ok "created virtual environment"
  else
    info "virtual environment already present"
  fi
  VENV_PY="$ROOT/backend/.venv/bin/python"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  info "installing Python packages (a few minutes on first run)…"
  "$VENV_PY" -m pip install --quiet -r backend/requirements.txt
  ok "Python packages installed"
}

# Acceleration is offered, never assumed: the runtimes are ~1GB downloads and
# the right one depends on the vendor. Apple silicon is the exception - it is
# already accelerated by the default wheel, so there is nothing to suggest.
suggest_acceleration() {
  local py="$ROOT/backend/.venv/bin/python"
  # Already accelerated? Then say so and stop.
  local active
  active=$("$py" -c "
import onnxruntime as ort
accel=[p for p in ort.get_available_providers() if p!='CPUExecutionProvider' and p!='AzureExecutionProvider']
print(accel[0] if accel else '')" 2>/dev/null)
  if [ -n "$active" ]; then
    ok "hardware acceleration available: $active"
    return
  fi

  if [ "$PLATFORM" = "macos" ]; then
    # Intel Macs have no supported accelerator; Apple silicon would have
    # reported CoreML above.
    [ "$(uname -m)" = "arm64" ] && info "Apple silicon: CoreML is in the default runtime" 
    return
  fi

  if have nvidia-smi; then
    warn "An NVIDIA GPU is present but the CPU runtime is installed."
    info "Much faster indexing:  pip install onnxruntime-gpu"
  elif have rocm-smi || [ -d /opt/rocm ]; then
    warn "An AMD GPU is present but the CPU runtime is installed."
    info "See backend/requirements-accelerate.txt for the ROCm install line."
  fi
}

setup_frontend() {
  step "Frontend"
  if [ -d frontend/node_modules ]; then
    info "npm packages already present"
  else
    info "installing npm packages…"
    (cd frontend && npm install --silent)
  fi
  ok "npm packages installed"
}

# --- run ------------------------------------------------------------------

wait_for() {
  local url="$1" label="$2" tries="${3:-120}"
  for _ in $(seq 1 "$tries"); do
    curl -fsS -m 2 "$url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

start_services() {
  step "Data services"
  docker compose up -d >/dev/null
  info "waiting for Postgres…"
  for _ in $(seq 1 60); do
    docker compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null | grep -q "postgres.*healthy" && break
    sleep 2
  done
  ok "Postgres and Qdrant are up"

  step "Starting Plutus"
  VENV_PY="$ROOT/backend/.venv/bin/python"
  (cd backend && nohup "$VENV_PY" -m uvicorn app.main:app --port "$BACKEND_PORT" --host 127.0.0.1 \
     >"$RUN_DIR/backend.log" 2>&1 & echo $! >"$RUN_DIR/backend.pid")
  info "backend starting (it downloads ~150MB of models on the first run)…"
  wait_for "http://127.0.0.1:$BACKEND_PORT/health" backend 180 \
    || die "Backend did not come up. See $RUN_DIR/backend.log"
  ok "backend ready on http://localhost:$BACKEND_PORT"

  (cd frontend && nohup npm run dev -- --port "$FRONTEND_PORT" --strictPort \
     >"$RUN_DIR/frontend.log" 2>&1 & echo $! >"$RUN_DIR/frontend.pid")
  wait_for "http://localhost:$FRONTEND_PORT" frontend 60 \
    || die "Frontend did not come up. See $RUN_DIR/frontend.log"
  ok "frontend ready"
}

announce() {
  cat <<EOF

  ${GREEN}${BOLD}────────────────────────────────────────────────${RESET}
  ${GREEN}${BOLD}  Plutus is up and ready to use${RESET}
  ${GREEN}${BOLD}────────────────────────────────────────────────${RESET}

     Open   ${BOLD}http://localhost:$FRONTEND_PORT${RESET}

     Add a filing (PDF, HTM or HTML), then ask about it.
     Every answer cites the page it came from - or says it
     could not find one.

     Logs    $RUN_DIR/{backend,frontend}.log
     Stop    ./scripts/bootstrap.sh --stop

EOF
  [ "$PLATFORM" = "macos" ] && open "http://localhost:$FRONTEND_PORT" 2>/dev/null || true
}

# --- main -----------------------------------------------------------------

printf "\n  %sPlutus%s — setup for %s\n" "$BOLD" "$RESET" "$PLATFORM"

step "Checking prerequisites"
ensure_python
ensure_node
ensure_docker

ensure_env
setup_backend
suggest_acceleration
setup_frontend

if [ "${1:-}" = "--no-start" ]; then
  printf "\n  %sSetup complete.%s Start it with ./scripts/bootstrap.sh\n\n" "$BOLD" "$RESET"
  exit 0
fi

start_services
announce
