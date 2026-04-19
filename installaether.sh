#!/usr/bin/env bash
# One-shot install: clone Aether (unless already present), then run `make demo`.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/krishmula/aether/main/installaether.sh | bash
#
# Or after downloading:
#   bash installaether.sh
#
# Environment (optional):
#   AETHER_REPO_URL   Git remote (default: https://github.com/krishmula/aether.git)
#   AETHER_REF        Branch or tag to clone (default: main)
#   AETHER_DIR        Install path (default: ./aether under current working directory)
#   AETHER_SHALLOW    Set to 0 for full clone (default: 1)

set -euo pipefail

REPO_URL="${AETHER_REPO_URL:-https://github.com/krishmula/aether.git}"
REF="${AETHER_REF:-main}"
INSTALL_DIR="${AETHER_DIR:-./aether}"
SHALLOW="${AETHER_SHALLOW:-1}"

COMPOSE_WRAP_DIR=""

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "${COMPOSE_WRAP_DIR}" && -d "${COMPOSE_WRAP_DIR}" ]]; then
    rm -rf "${COMPOSE_WRAP_DIR}"
  fi
}
trap cleanup EXIT

abs_path() {
  local path="$1"
  if [[ "${path}" != /* ]]; then
    path="$(pwd)/${path#./}"
  fi
  printf '%s' "${path}"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

docker_ok() {
  docker info >/dev/null 2>&1 || die "Docker is installed but the daemon is not reachable. Start Docker and retry."
}

# Makefile invokes `docker-compose`. Many machines only have Compose v2 (`docker compose`).
ensure_docker_compose_on_path() {
  if command -v docker-compose >/dev/null 2>&1; then
    return 0
  fi
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_WRAP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aether-compose-wrapper.XXXXXX")"
    cat >"${COMPOSE_WRAP_DIR}/docker-compose" <<'EOF'
#!/usr/bin/env sh
exec docker compose "$@"
EOF
    chmod +x "${COMPOSE_WRAP_DIR}/docker-compose"
    export PATH="${COMPOSE_WRAP_DIR}:${PATH}"
    return 0
  fi
  die "Need Docker Compose: install the 'docker compose' plugin or the standalone 'docker-compose' binary."
}

usage() {
  cat <<'EOF'
installaether.sh — clone Aether and start the Docker demo (make demo).

Options:
  -h              Show this help
  -r URL          Git repository URL (default: see AETHER_REPO_URL)
  -b REF          Branch or tag to check out (default: main / AETHER_REF)
  -d DIR          Install directory (default: ./aether / AETHER_DIR)
  -f              Full git clone (default: shallow clone)

Environment overrides: AETHER_REPO_URL, AETHER_REF, AETHER_DIR, AETHER_SHALLOW
EOF
}

FULL_CLONE=0
while getopts "hr:b:d:f" opt; do
  case "$opt" in
    h) usage; exit 0 ;;
    r) REPO_URL="$OPTARG" ;;
    b) REF="$OPTARG" ;;
    d) INSTALL_DIR="$OPTARG" ;;
    f) FULL_CLONE=1 ;;
    *) usage; exit 2 ;;
  esac
done

need_cmd git
need_cmd docker
need_cmd make
need_cmd curl
need_cmd python3
need_cmd lsof
docker_ok
ensure_docker_compose_on_path

INSTALL_DIR="$(abs_path "${INSTALL_DIR}")"

is_aether_repo() {
  [[ -d "$1/.git" && -f "$1/Makefile" && -f "$1/docker-compose.yml" ]]
}

if is_aether_repo "$INSTALL_DIR"; then
  printf 'Using existing repo at %s\n' "$INSTALL_DIR"
  cd "$INSTALL_DIR"
  git fetch --tags origin 2>/dev/null || git fetch origin
  git checkout "$REF"
  # best-effort update when shallow / branch
  git pull --ff-only origin "$REF" 2>/dev/null || true
else
  if [[ -e "$INSTALL_DIR" ]]; then
    die "Path already exists and is not an Aether git checkout: $INSTALL_DIR (remove it or set AETHER_DIR / -d)"
  fi
  mkdir -p "$(dirname "$INSTALL_DIR")"
  clone_args=(clone --config advice.detachedHead=false)
  if [[ "$FULL_CLONE" -eq 0 && "$SHALLOW" != "0" ]]; then
    clone_args+=(--depth 1 --branch "$REF")
  fi
  clone_args+=("$REPO_URL" "$INSTALL_DIR")
  git "${clone_args[@]}"
  cd "$INSTALL_DIR"
  if [[ "$FULL_CLONE" -eq 1 || "$SHALLOW" == "0" ]]; then
    git checkout "$REF"
  fi
fi

printf '\nStarting demo (docker build + compose + seed)…\n\n'
make demo

printf '\nDone. Open:\n'
printf '  Dashboard:      http://localhost:3000\n'
printf '  Grafana:        http://localhost:3001\n'
printf '  Orchestrator:   http://localhost:9000/docs\n'
printf '\nStop and remove containers from this directory: make clean\n'
