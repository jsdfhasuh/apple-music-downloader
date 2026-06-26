#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/auto-update-downloader-production.sh [run|cron-line|help]

Modes:
  run        Pull clean downloader source, build candidate image, smoke test, deploy production.
  cron-line  Print a daily crontab line for automatic production updates.
  help       Show this help.

Environment:
  ORIGIN_DIR       Clean upstream downloader clone. Default: /downloads/docker/apple_music_download/origin
  ORIGIN_REMOTE    Remote name inside ORIGIN_DIR. Default: origin
  ORIGIN_BRANCH    Branch inside ORIGIN_DIR. Default: main
  IMAGE_TAG        Production image tag. Default: apple-music-downloader:test
  CANDIDATE_TAG    Candidate image tag. Default: apple-music-downloader:candidate
  ROLLBACK_TAG     Rollback image tag. Default: apple-music-downloader:rollback
  CONTAINER_NAME   Downloader container name. Default: applemusic_download
  NETWORK_NAME     Docker network. Default: br0
  CONTAINER_IP     Downloader fixed IP. Default: 192.168.100.93
  WEBAPP_URL       Webapp base URL for idle check. Default: http://192.168.100.94:5000
  LOCAL_PATCH_DIR  Local downloader patches applied before build. Default: <repo>/scripts/patches
  LOCK_DIR         Runtime lock directory. Default: <state-dir>/downloader-auto-update.lock
  SKIP_WEBAPP_IDLE_CHECK=1  Skip queued/running task check before production switch.
  FORCE=1          Rebuild/deploy even when ORIGIN_DIR HEAD was already deployed.
  HOST_REPO_ROOT   Host path used by cron-line. Default: /downloads/docker/apple_music_download/apple-music-downloader

The script intentionally builds from the clean downloader source and uses a
temporary resident-container Dockerfile, so upstream ENTRYPOINT changes do not
break the webapp's docker exec integration.
EOF
}

mode="${1:-run}"
if [[ "$mode" == "help" || "$mode" == "-h" || "$mode" == "--help" ]]; then
  usage
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
default_host_repo_root="/downloads/docker/apple_music_download/apple-music-downloader"
host_repo_root="${HOST_REPO_ROOT:-$default_host_repo_root}"
origin_dir="${ORIGIN_DIR:-/downloads/docker/apple_music_download/origin}"
origin_remote="${ORIGIN_REMOTE:-origin}"
origin_branch="${ORIGIN_BRANCH:-main}"
image_tag="${IMAGE_TAG:-apple-music-downloader:test}"
candidate_tag="${CANDIDATE_TAG:-apple-music-downloader:candidate}"
rollback_tag="${ROLLBACK_TAG:-apple-music-downloader:rollback}"
container_name="${CONTAINER_NAME:-applemusic_download}"
network_name="${NETWORK_NAME:-br0}"
container_ip="${CONTAINER_IP:-192.168.100.93}"
webapp_url="${WEBAPP_URL:-http://192.168.100.94:5000}"
state_dir="${STATE_DIR:-$repo_root/data/updater}"
log_dir="${LOG_DIR:-$repo_root/data/logs}"
log_file="${LOG_FILE:-$log_dir/downloader-auto-update.log}"
last_deployed_file="$state_dir/downloader-origin-last-deployed.sha"
local_patch_dir="${LOCAL_PATCH_DIR:-$repo_root/scripts/patches}"
lock_dir="${LOCK_DIR:-$state_dir/downloader-auto-update.lock}"
config_mount="${CONFIG_MOUNT:-/mnt/user/docker/apple_music_download/apple-music-downloader/docker_data/config.yaml:/app/config.yaml:rw}"
downloads_mount="${DOWNLOADS_MOUNT:-/mnt/user/music:/downloads:rw}"

mkdir -p "$state_dir" "$log_dir"

cleanup_dirs=()
cleanup() {
  local path
  for path in "${cleanup_dirs[@]}"; do
    if [[ -n "$path" ]]; then
      rm -rf "$path"
    fi
  done
}
trap cleanup EXIT

log() {
  local timestamp
  timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
  printf '[%s] %s\n' "$timestamp" "$*" | tee -a "$log_file"
}

docker_local() {
  env -u DOCKER_HOST docker "$@"
}

acquire_lock() {
  if ! mkdir "$lock_dir" 2>/dev/null; then
    log "Another downloader auto-update process is already running: $lock_dir"
    exit 0
  fi
  cleanup_dirs+=("$lock_dir")
}

quote_for_cron() {
  printf "%q" "$1"
}

if [[ "$mode" == "cron-line" ]]; then
  repo_quoted="$(quote_for_cron "$host_repo_root")"
  printf '29 4 * * * R=%s; "$R/scripts/auto-update-downloader-production.sh" run >> "$R/data/logs/downloader-auto-update-cron.log" 2>&1\n' "$repo_quoted"
  exit 0
fi

if [[ "$mode" != "run" ]]; then
  usage
  exit 2
fi

require_origin_repo() {
  if [[ ! -d "$origin_dir/.git" ]]; then
    log "ORIGIN_DIR is not a git repository: $origin_dir"
    exit 1
  fi
  for required in main.go go.mod go.sum utils config.yaml.example; do
    if [[ ! -e "$origin_dir/$required" ]]; then
      log "ORIGIN_DIR missing required downloader path: $required"
      exit 1
    fi
  done
}

ensure_webapp_idle() {
  if [[ "${SKIP_WEBAPP_IDLE_CHECK:-0}" == "1" ]]; then
    log "Skipping webapp idle check because SKIP_WEBAPP_IDLE_CHECK=1"
    return
  fi
  python - "$webapp_url" <<'PY'
import json
import sys
from urllib import request

base_url = sys.argv[1].rstrip("/")
try:
    with request.urlopen(f"{base_url}/api/tasks", timeout=10) as response:
        tasks = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    raise SystemExit(f"webapp idle check failed: {exc}")

active = [
    task for task in tasks
    if str(task.get("status", "")) in {"queued", "running"}
]
if active:
    urls = ", ".join(str(task.get("url", "-")) for task in active[:3])
    raise SystemExit(f"webapp has active downloader tasks: {len(active)} ({urls})")
PY
}

collect_local_patches() {
  if [[ ! -d "$local_patch_dir" ]]; then
    log "Local patch directory does not exist: $local_patch_dir"
    exit 1
  fi

  shopt -s nullglob
  local_patches=("$local_patch_dir"/*.patch)
  shopt -u nullglob

  if [[ ${#local_patches[@]} -eq 0 ]]; then
    log "No local downloader patches found in $local_patch_dir"
    exit 1
  fi
}

local_patch_fingerprint() {
  {
    local patch_file
    for patch_file in "${local_patches[@]}"; do
      printf '%s %s\n' "$(git hash-object "$patch_file")" "$(basename "$patch_file")"
    done
  } | git hash-object --stdin
}

create_build_context() {
  local build_dir="$1"
  mkdir -p "$build_dir"
  tar \
    --exclude='.git' \
    --exclude='downloads' \
    -C "$origin_dir" \
    -cf - . | tar -C "$build_dir" -xf -
  cat > "$build_dir/Dockerfile.codex-resident" <<'EOF'
ARG GOVERSION=1.25.5

FROM golang:${GOVERSION}-alpine AS builder
WORKDIR /app
COPY . .
RUN CGO_ENABLED=0 go build -o /bin/apple-music-dl main.go

FROM gpac/ubuntu
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /bin/apple-music-dl /usr/local/bin/apple-music-dl
WORKDIR /app
COPY config.yaml.example config.yaml
RUN echo 'alac-save-folder: "/downloads/ALAC"' >> config.yaml \
    && echo 'atmos-save-folder: "/downloads/Atmos"' >> config.yaml \
    && echo 'aac-save-folder: "/downloads/AAC"' >> config.yaml
CMD ["sleep", "infinity"]
EOF
}

apply_local_patches() {
  local build_dir="$1"

  for patch_file in "${local_patches[@]}"; do
    log "Applying local downloader patch: $(basename "$patch_file")"
    if ! (cd "$build_dir" && git apply --check "$patch_file"); then
      log "Local patch failed to apply cleanly: $patch_file"
      exit 1
    fi
    (cd "$build_dir" && git apply "$patch_file")
  done
}

smoke_test_image() {
  local smoke_container="${container_name}_candidate_smoke"
  local help_output
  docker_local rm -f "$smoke_container" >/dev/null 2>&1 || true
  docker_local run -d --name "$smoke_container" "$candidate_tag" >/dev/null

  if ! docker_local exec "$smoke_container" sh -c 'command -v apple-music-dl >/dev/null'; then
    docker_local rm -f "$smoke_container" >/dev/null 2>&1 || true
    log "Candidate smoke test failed: apple-music-dl not found"
    return 1
  fi

  help_output="$(docker_local exec "$smoke_container" apple-music-dl --help 2>&1 || true)"
  if [[ "$help_output" != *"Usage:"* ]]; then
    docker_local rm -f "$smoke_container" >/dev/null 2>&1 || true
    log "Candidate smoke test failed: --help output did not contain Usage:"
    return 1
  fi

  if ! docker_local exec "$smoke_container" sh -c 'test "$(cat /proc/1/comm)" = "sleep"'; then
    docker_local rm -f "$smoke_container" >/dev/null 2>&1 || true
    log "Candidate smoke test failed: PID 1 is not sleep"
    return 1
  fi

  docker_local rm -f "$smoke_container" >/dev/null
}

start_production_container() {
  local image="$1"
  docker_local run -d \
    --name "$container_name" \
    --network "$network_name" \
    --ip "$container_ip" \
    -e TZ=Asia/Shanghai \
    -v "$config_mount" \
    -v "$downloads_mount" \
    "$image" >/dev/null
}

deploy_candidate() {
  local previous_image
  local previous_image_id
  local candidate_image_id
  previous_image="$(docker_local inspect "$container_name" --format '{{.Config.Image}}' 2>/dev/null || true)"
  if [[ -n "$previous_image" ]]; then
    previous_image_id="$(docker_local image inspect "$previous_image" --format '{{.Id}}' 2>/dev/null || true)"
    candidate_image_id="$(docker_local image inspect "$candidate_tag" --format '{{.Id}}' 2>/dev/null || true)"
    if [[ -n "$previous_image_id" && "$previous_image_id" != "$candidate_image_id" ]]; then
      docker_local tag "$previous_image" "$rollback_tag"
      log "Rollback tag updated: $rollback_tag <- $previous_image"
    else
      log "Rollback tag unchanged because candidate matches current production image"
    fi
  fi

  docker_local tag "$candidate_tag" "$image_tag"
  log "Production image tag updated: $image_tag <- $candidate_tag"

  docker_local rm -f "$container_name" >/dev/null 2>&1 || true
  if ! start_production_container "$image_tag"; then
    log "Failed to start candidate production container."
    if docker_local image inspect "$rollback_tag" >/dev/null 2>&1; then
      log "Attempting rollback with $rollback_tag"
      start_production_container "$rollback_tag"
    fi
    exit 1
  fi

  if ! docker_local exec "$container_name" sh -c 'command -v apple-music-dl >/dev/null && test "$(cat /proc/1/comm)" = "sleep"'; then
    log "Production smoke check failed after deployment."
    docker_local rm -f "$container_name" >/dev/null 2>&1 || true
    if docker_local image inspect "$rollback_tag" >/dev/null 2>&1; then
      log "Rolling back with $rollback_tag"
      start_production_container "$rollback_tag"
    fi
    exit 1
  fi
}

local_patches=()

acquire_lock
require_origin_repo
collect_local_patches
log "Pulling clean downloader source: $origin_dir"
git -C "$origin_dir" pull --ff-only "$origin_remote" "$origin_branch" --quiet
origin_sha="$(git -C "$origin_dir" rev-parse HEAD)"
patch_fingerprint="$(local_patch_fingerprint)"
deployment_id="$origin_sha:$patch_fingerprint"
last_deployed_sha=""
if [[ -f "$last_deployed_file" ]]; then
  last_deployed_sha="$(cat "$last_deployed_file")"
fi

if [[ "$deployment_id" == "$last_deployed_sha" && "${FORCE:-0}" != "1" ]]; then
  log "Downloader source and local patches already deployed at $origin_sha"
  exit 0
fi

ensure_webapp_idle

build_dir="$(mktemp -d)"
cleanup_dirs+=("$build_dir")

create_build_context "$build_dir"
apply_local_patches "$build_dir"
log "Building candidate image $candidate_tag from origin commit $origin_sha"
docker_local build \
  --label "org.opencontainers.image.revision=$origin_sha" \
  -f "$build_dir/Dockerfile.codex-resident" \
  -t "$candidate_tag" \
  "$build_dir"

log "Running candidate smoke test"
smoke_test_image

log "Deploying candidate to production container $container_name"
deploy_candidate

printf '%s\n' "$deployment_id" > "$last_deployed_file"
log "Downloader production update complete: origin=$origin_sha patches=$patch_fingerprint"
