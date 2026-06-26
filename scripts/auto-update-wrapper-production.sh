#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/auto-update-wrapper-production.sh [run|cron-line|help]

Modes:
  run        Download latest wrapper release, build candidate image, smoke test, deploy production.
  cron-line  Print a daily crontab line for automatic wrapper updates.
  help       Show this help.

Environment:
  WRAPPER_DIR       Host wrapper release directory. Default: /mnt/user/docker/apple_music_download/Wrapper.x86_64.latest
  RELEASE_API_URL   GitHub release API URL.
  ASSET_NAME        Release asset name. Default: Wrapper.x86_64.latest.zip
  IMAGE_TAG         Production image tag. Default: wrapper:1.0
  CANDIDATE_TAG     Candidate image tag. Default: wrapper:candidate
  ROLLBACK_TAG      Rollback image tag. Default: wrapper:rollback
  CONTAINER_NAME    Wrapper container name. Default: wrapper
  NETWORK_NAME      Docker network. Default: br0
  CONTAINER_IP      Wrapper fixed IP. Default: 192.168.100.56
  ACCOUNT_URL       Account health URL. Default: http://<CONTAINER_IP>:30020/
  DATA_MOUNT        Data bind mount. Default: <WRAPPER_DIR>/rootfs/data:/app/rootfs/data:rw
  LOCK_DIR          Runtime lock directory. Default: <state-dir>/wrapper-auto-update.lock
  FORCE=1           Rebuild/deploy even when the latest release digest was already deployed.
  HOST_REPO_ROOT    Host path used by cron-line. Default: /downloads/docker/apple_music_download/apple-music-downloader

The script preserves WRAPPER_DIR/rootfs/data and reuses the current wrapper
container environment, including USERNAME/PASSWORD when present.
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
wrapper_dir="${WRAPPER_DIR:-/mnt/user/docker/apple_music_download/Wrapper.x86_64.latest}"
release_api_url="${RELEASE_API_URL:-https://api.github.com/repos/WorldObservationLog/wrapper/releases/tags/wrapper.x86_64.latest}"
asset_name="${ASSET_NAME:-Wrapper.x86_64.latest.zip}"
image_tag="${IMAGE_TAG:-wrapper:1.0}"
candidate_tag="${CANDIDATE_TAG:-wrapper:candidate}"
rollback_tag="${ROLLBACK_TAG:-wrapper:rollback}"
container_name="${CONTAINER_NAME:-wrapper}"
network_name="${NETWORK_NAME:-br0}"
container_ip="${CONTAINER_IP:-192.168.100.56}"
account_url="${ACCOUNT_URL:-http://$container_ip:30020/}"
state_dir="${STATE_DIR:-$repo_root/data/updater}"
log_dir="${LOG_DIR:-$repo_root/data/logs}"
log_file="${LOG_FILE:-$log_dir/wrapper-auto-update.log}"
last_deployed_file="$state_dir/wrapper-release-last-deployed.digest"
lock_dir="${LOCK_DIR:-$state_dir/wrapper-auto-update.lock}"
data_mount="${DATA_MOUNT:-$wrapper_dir/rootfs/data:/app/rootfs/data:rw}"
restart_policy="${RESTART_POLICY:-no}"

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
    log "Another wrapper auto-update process is already running: $lock_dir"
    exit 0
  fi
  cleanup_dirs+=("$lock_dir")
}

quote_for_cron() {
  printf "%q" "$1"
}

if [[ "$mode" == "cron-line" ]]; then
  repo_quoted="$(quote_for_cron "$host_repo_root")"
  printf '17 4 * * * R=%s; "$R/scripts/auto-update-wrapper-production.sh" run >> "$R/data/logs/wrapper-auto-update-cron.log" 2>&1\n' "$repo_quoted"
  exit 0
fi

if [[ "$mode" != "run" ]]; then
  usage
  exit 2
fi

require_wrapper_dir() {
  if [[ ! -d "$wrapper_dir" ]]; then
    log "WRAPPER_DIR does not exist: $wrapper_dir"
    exit 1
  fi
  if [[ ! -d "$wrapper_dir/rootfs/data" ]]; then
    log "Wrapper data directory does not exist: $wrapper_dir/rootfs/data"
    exit 1
  fi
}

fetch_release_metadata() {
  local metadata_file="$1"
  curl -fsSL \
    --connect-timeout 20 \
    --retry 3 \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: apple-music-wrapper-auto-update" \
    "$release_api_url" \
    -o "$metadata_file"
}

parse_release_metadata() {
  local metadata_file="$1"
  python - "$metadata_file" "$asset_name" <<'PY'
import json
import sys

metadata_path, asset_name = sys.argv[1], sys.argv[2]
with open(metadata_path, "r", encoding="utf-8") as fh:
    release = json.load(fh)

asset = None
for item in release.get("assets", []):
    if item.get("name") == asset_name:
        asset = item
        break

if not asset:
    raise SystemExit(f"release asset not found: {asset_name}")

digest = asset.get("digest") or ""
if digest.startswith("sha256:"):
    digest = digest.split(":", 1)[1]

if not digest:
    raise SystemExit(f"release asset has no sha256 digest: {asset_name}")

print(asset.get("browser_download_url") or "")
print(digest)
print(release.get("tag_name") or "")
print(release.get("updated_at") or "")
PY
}

download_release_zip() {
  local download_url="$1"
  local expected_digest="$2"
  local zip_path="$3"
  local actual_digest

  curl -fL \
    --connect-timeout 20 \
    --retry 3 \
    -H "User-Agent: apple-music-wrapper-auto-update" \
    "$download_url" \
    -o "$zip_path"

  actual_digest="$(sha256sum "$zip_path" | awk '{print $1}')"
  if [[ "$actual_digest" != "$expected_digest" ]]; then
    log "Release digest mismatch: expected=$expected_digest actual=$actual_digest"
    exit 1
  fi
}

find_package_root() {
  local extract_dir="$1"
  local candidate

  if [[ -f "$extract_dir/wrapper" && -d "$extract_dir/rootfs" ]]; then
    printf '%s\n' "$extract_dir"
    return
  fi

  candidate="$(find "$extract_dir" -maxdepth 3 -type f -name wrapper -exec dirname {} \; | head -n 1 || true)"
  if [[ -n "$candidate" && -d "$candidate/rootfs" ]]; then
    printf '%s\n' "$candidate"
    return
  fi

  log "Unable to find wrapper package root inside extracted release."
  exit 1
}

require_package_root() {
  local package_root="$1"
  local required
  for required in wrapper entrypoint.sh rootfs/system/bin/main rootfs/system/bin/linker64; do
    if [[ ! -e "$package_root/$required" ]]; then
      log "Release package missing required path: $required"
      exit 1
    fi
  done
}

create_build_context() {
  local package_root="$1"
  local build_dir="$2"

  mkdir -p "$build_dir"
  tar --exclude='rootfs/data' --exclude='./rootfs/data' -C "$package_root" -cf - wrapper entrypoint.sh rootfs | tar -C "$build_dir" -xf -
  cat > "$build_dir/Dockerfile.wrapper-runtime" <<'EOF'
FROM debian:13.2
WORKDIR /app
COPY wrapper /app/wrapper
COPY rootfs /app/rootfs
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/wrapper /app/entrypoint.sh && mkdir -p /app/rootfs/data
CMD ["/app/entrypoint.sh"]
EXPOSE 10020 20020 30020
EOF
}

build_candidate_image() {
  local build_dir="$1"
  local release_digest="$2"
  local release_tag="$3"

  docker_local build \
    --label "org.opencontainers.image.source=https://github.com/WorldObservationLog/wrapper" \
    --label "org.opencontainers.image.version=$release_tag" \
    --label "org.opencontainers.image.revision=$release_digest" \
    -f "$build_dir/Dockerfile.wrapper-runtime" \
    -t "$candidate_tag" \
    "$build_dir"
}

smoke_test_image() {
  local version_output

  if ! docker_local run --rm --entrypoint /bin/sh "$candidate_tag" -c \
    'test -x /app/wrapper && test -x /app/entrypoint.sh && test -x /app/rootfs/system/bin/main'; then
    log "Candidate smoke test failed: expected wrapper files are missing or not executable"
    return 1
  fi

  version_output="$(docker_local run --rm --entrypoint /app/wrapper "$candidate_tag" -V 2>&1 || true)"
  if [[ "$version_output" != *"wrapper"* && "$version_output" != *"Wrapper"* ]]; then
    log "Candidate smoke test failed: wrapper -V did not look valid"
    return 1
  fi
}

backup_data_dir() {
  local backup_root="$state_dir/wrapper-backups"
  local backup_dir
  backup_dir="$backup_root/$(date '+%Y%m%d-%H%M%S')"
  mkdir -p "$backup_dir"
  tar -C "$wrapper_dir/rootfs" -czf "$backup_dir/rootfs-data.tar.gz" data
  printf '%s\n' "$backup_dir/rootfs-data.tar.gz"
}

restore_data_backup() {
  local backup_file="$1"
  if [[ ! -f "$backup_file" ]]; then
    log "Data backup is missing, cannot restore: $backup_file"
    return 1
  fi

  rm -rf "$wrapper_dir/rootfs/data"
  mkdir -p "$wrapper_dir/rootfs"
  tar -C "$wrapper_dir/rootfs" -xzf "$backup_file"
}

container_env_args=()
container_label_args=()
collect_container_env() {
  local source_container="$1"
  local env_line
  local label_line
  local has_tz=0

  container_env_args=()
  container_label_args=()
  if docker_local inspect "$source_container" >/dev/null 2>&1; then
    while IFS= read -r env_line; do
      if [[ -z "$env_line" || "$env_line" == PATH=* ]]; then
        continue
      fi
      if [[ "$env_line" == TZ=* ]]; then
        has_tz=1
      fi
      container_env_args+=(-e "$env_line")
    done < <(docker_local inspect "$source_container" --format '{{range .Config.Env}}{{println .}}{{end}}')

    while IFS= read -r label_line; do
      if [[ -n "$label_line" ]]; then
        container_label_args+=(--label "$label_line")
      fi
    done < <(docker_local inspect "$source_container" --format '{{range $key, $value := .Config.Labels}}{{printf "%s=%s\n" $key $value}}{{end}}')
  fi

  if [[ ${#container_env_args[@]} -eq 0 ]]; then
    if [[ -n "${USERNAME:-}" ]]; then
      container_env_args+=(-e "USERNAME=$USERNAME")
    fi
    if [[ -n "${PASSWORD:-}" ]]; then
      container_env_args+=(-e "PASSWORD=$PASSWORD")
    fi
    if [[ -n "${TZ:-}" ]]; then
      container_env_args+=(-e "TZ=$TZ")
      has_tz=1
    fi
  fi

  if [[ "$has_tz" == "0" ]]; then
    container_env_args+=(-e "TZ=Asia/Shanghai")
  fi
}

start_production_container() {
  local image="$1"
  docker_local run -d \
    --name "$container_name" \
    --privileged \
    --network "$network_name" \
    --ip "$container_ip" \
    --restart "$restart_policy" \
    --pids-limit 3096 \
    --security-opt label=disable \
    --log-driver json-file \
    --log-opt max-size=100m \
    --log-opt max-file=3 \
    "${container_label_args[@]}" \
    "${container_env_args[@]}" \
    -v "$data_mount" \
    "$image" >/dev/null
}

health_check_production() {
  local attempt
  for attempt in $(seq 1 30); do
    if python - "$account_url" <<'PY' >/dev/null 2>&1
import json
import sys
from urllib import request

url = sys.argv[1]
with request.urlopen(url, timeout=5) as response:
    data = json.loads(response.read().decode("utf-8"))

required = ("storefront_id", "dev_token", "music_token")
missing = [key for key in required if not data.get(key)]
if missing:
    raise SystemExit(f"missing keys: {', '.join(missing)}")
PY
    then
      return 0
    fi
    sleep 2
  done
  return 1
}

rollback_production() {
  local backup_file="$1"

  docker_local rm -f "$container_name" >/dev/null 2>&1 || true
  if ! restore_data_backup "$backup_file"; then
    log "Rollback data restore failed."
    return 1
  fi

  if docker_local image inspect "$rollback_tag" >/dev/null 2>&1; then
    log "Rolling back wrapper container with $rollback_tag"
    start_production_container "$rollback_tag"
    if health_check_production; then
      log "Rollback health check passed."
      return 0
    fi
    log "Rollback container started but health check failed."
    return 1
  fi

  log "Rollback image does not exist: $rollback_tag"
  return 1
}

deploy_candidate() {
  local previous_image
  local previous_image_id
  local candidate_image_id
  local backup_file

  collect_container_env "$container_name"
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

  log "Backing up wrapper data directory"
  backup_file="$(backup_data_dir)"

  docker_local tag "$candidate_tag" "$image_tag"
  log "Production image tag updated: $image_tag <- $candidate_tag"

  docker_local rm -f "$container_name" >/dev/null 2>&1 || true
  if ! start_production_container "$image_tag"; then
    log "Failed to start candidate wrapper container."
    rollback_production "$backup_file" || true
    exit 1
  fi

  if ! health_check_production; then
    log "Production health check failed after wrapper deployment."
    rollback_production "$backup_file" || true
    exit 1
  fi
}

sync_release_files() {
  local package_root="$1"
  local optional_file

  for optional_file in Dockerfile compose.yaml entrypoint.sh wrapper; do
    if [[ -f "$package_root/$optional_file" ]]; then
      cp -f "$package_root/$optional_file" "$wrapper_dir/$optional_file"
    fi
  done

  chmod +x "$wrapper_dir/wrapper" "$wrapper_dir/entrypoint.sh"
  mkdir -p "$wrapper_dir/rootfs"
  rm -rf "$wrapper_dir/rootfs/system"
  tar --exclude='data' --exclude='./data' -C "$package_root/rootfs" -cf - . | tar -C "$wrapper_dir/rootfs" -xf -
}

acquire_lock
require_wrapper_dir

work_dir="$(mktemp -d)"
cleanup_dirs+=("$work_dir")
metadata_file="$work_dir/release.json"
zip_path="$work_dir/$asset_name"
extract_dir="$work_dir/extract"
build_dir="$work_dir/build"

log "Fetching wrapper release metadata"
fetch_release_metadata "$metadata_file"
release_metadata="$(parse_release_metadata "$metadata_file")"
mapfile -t release_fields <<< "$release_metadata"
if [[ ${#release_fields[@]} -lt 4 ]]; then
  log "Failed to parse wrapper release metadata."
  exit 1
fi
download_url="${release_fields[0]}"
release_digest="${release_fields[1]}"
release_tag="${release_fields[2]}"
release_updated_at="${release_fields[3]}"

last_deployed_digest=""
if [[ -f "$last_deployed_file" ]]; then
  last_deployed_digest="$(cat "$last_deployed_file")"
fi

if [[ "$release_digest" == "$last_deployed_digest" && "${FORCE:-0}" != "1" ]]; then
  log "Wrapper release already deployed: tag=$release_tag digest=$release_digest"
  exit 0
fi

log "Downloading wrapper release: tag=$release_tag updated=$release_updated_at"
download_release_zip "$download_url" "$release_digest" "$zip_path"

mkdir -p "$extract_dir"
unzip -q "$zip_path" -d "$extract_dir"
package_root="$(find_package_root "$extract_dir")"
require_package_root "$package_root"

create_build_context "$package_root" "$build_dir"
log "Building candidate image $candidate_tag from wrapper release digest $release_digest"
build_candidate_image "$build_dir" "$release_digest" "$release_tag"

log "Running candidate smoke test"
smoke_test_image

log "Deploying candidate to production container $container_name"
deploy_candidate

log "Syncing local wrapper release files while preserving rootfs/data"
sync_release_files "$package_root"

printf '%s\n' "$release_digest" > "$last_deployed_file"
log "Wrapper production update complete: tag=$release_tag digest=$release_digest"
