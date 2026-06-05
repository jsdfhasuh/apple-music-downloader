#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/downloader-upstream-check.sh [check|cron-line|help]

Modes:
  check      Fetch upstream and create a downloader-only update report when changes exist.
  cron-line  Print a crontab line for daily checks.
  help       Show this help.

Environment:
  REMOTE       Upstream remote name. Default: upstream
  BRANCH       Upstream branch name. Default: main
  STATE_DIR    State directory. Default: <repo>/data/updater
  REPORT_DIR   Report directory. Default: <repo>/data/update-reports
  LOG_DIR      Log directory. Default: <repo>/data/logs

This script intentionally does not merge or apply upstream changes. The upstream
tree can remove webapp files and change Docker behavior, so scheduled automation
only creates a report and patch for review.
EOF
}

mode="${1:-check}"
if [[ "$mode" == "help" || "$mode" == "-h" || "$mode" == "--help" ]]; then
  usage
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
remote="${REMOTE:-upstream}"
branch="${BRANCH:-main}"
remote_ref="$remote/$branch"
state_dir="${STATE_DIR:-$repo_root/data/updater}"
report_dir="${REPORT_DIR:-$repo_root/data/update-reports}"
log_dir="${LOG_DIR:-$repo_root/data/logs}"
log_file="${LOG_FILE:-$log_dir/downloader-upstream-check.log}"

downloader_paths=(
  main.go
  go.mod
  go.sum
  utils
  cmd
)

protected_paths=(
  webapp
  Dockerfile
  config.yaml
  config.yaml.example
  README.md
  README-CN.md
  docs
)

mkdir -p "$state_dir" "$report_dir" "$log_dir"

log() {
  local timestamp
  timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
  printf '[%s] %s\n' "$timestamp" "$*" | tee -a "$log_file"
}

quote_for_cron() {
  printf "%q" "$1"
}

if [[ "$mode" == "cron-line" ]]; then
  repo_quoted="$(quote_for_cron "$repo_root")"
  script_quoted="$(quote_for_cron "$repo_root/scripts/downloader-upstream-check.sh")"
  log_quoted="$(quote_for_cron "$log_dir/downloader-upstream-cron.log")"
  printf '17 4 * * * cd %s && %s check >> %s 2>&1\n' "$repo_quoted" "$script_quoted" "$log_quoted"
  exit 0
fi

if [[ "$mode" != "check" ]]; then
  usage
  exit 2
fi

cd "$repo_root"

if ! git remote get-url "$remote" >/dev/null 2>&1; then
  log "Remote '$remote' does not exist."
  exit 1
fi

log "Fetching $remote $branch"
git fetch "$remote" "$branch" --quiet

remote_sha="$(git rev-parse "$remote_ref")"
head_sha="$(git rev-parse HEAD)"
base_sha="$(git merge-base HEAD "$remote_ref")"
last_seen_file="$state_dir/${remote}-${branch}.sha"
last_seen_sha=""
if [[ -f "$last_seen_file" ]]; then
  last_seen_sha="$(cat "$last_seen_file")"
fi

changed_downloader_file="$(mktemp)"
changed_protected_file="$(mktemp)"
cleanup() {
  rm -f "$changed_downloader_file" "$changed_protected_file"
}
trap cleanup EXIT

git diff --name-only "$head_sha..$remote_ref" -- "${downloader_paths[@]}" > "$changed_downloader_file"
git diff --name-only "$head_sha..$remote_ref" -- "${protected_paths[@]}" > "$changed_protected_file"

if [[ ! -s "$changed_downloader_file" ]]; then
  printf '%s\n' "$remote_sha" > "$last_seen_file"
  log "No downloader-path changes found. upstream=$remote_sha head=$head_sha"
  exit 0
fi

timestamp="$(date '+%Y%m%d-%H%M%S')"
report_file="$report_dir/downloader-upstream-$timestamp.md"
patch_file="$report_dir/downloader-upstream-$timestamp.patch"
latest_report="$report_dir/latest-downloader-upstream.md"
latest_patch="$report_dir/latest-downloader-upstream.patch"

git diff --binary "$head_sha..$remote_ref" -- "${downloader_paths[@]}" > "$patch_file"

{
  printf '# Downloader Upstream Update Report\n\n'
  printf '%s\n' "- Generated: $(date '+%Y-%m-%d %H:%M:%S %z')"
  printf '%s\n' "- Remote: \`$remote_ref\`"
  printf '%s\n' "- Local HEAD: \`$head_sha\`"
  printf '%s\n' "- Upstream HEAD: \`$remote_sha\`"
  printf '%s\n' "- Merge base: \`$base_sha\`"
  if [[ -n "$last_seen_sha" ]]; then
    printf '%s\n' "- Previous seen upstream HEAD: \`$last_seen_sha\`"
  fi
  printf '%s\n\n' "- Patch: \`$patch_file\`"

  printf '## Downloader Files Changed\n\n'
  sed 's/^/- /' "$changed_downloader_file"
  printf '\n'

  printf '## Protected Files Also Changed Upstream\n\n'
  if [[ -s "$changed_protected_file" ]]; then
    sed 's/^/- /' "$changed_protected_file"
  else
    printf '%s\n' "- None"
  fi
  printf '\n'

  printf '## Upstream Commits Touching Downloader Paths\n\n'
  git log --oneline --decorate "$head_sha..$remote_ref" -- "${downloader_paths[@]}" | sed 's/^/- /'
  printf '\n'

  printf '## Recommended Review Steps\n\n'
  printf '1. Read this report and patch.\n'
  printf '2. Do not run a full upstream merge unless you intend to remove/rework the webapp.\n'
  printf '3. Manually migrate downloader changes from the patch into local files.\n'
  printf '4. Rebuild `apple-music-downloader:test` and recreate `applemusic_download` after review.\n'
} > "$report_file"

ln -sfn "$(basename "$report_file")" "$latest_report"
ln -sfn "$(basename "$patch_file")" "$latest_patch"
printf '%s\n' "$remote_sha" > "$last_seen_file"

log "Downloader upstream changes found. report=$report_file patch=$patch_file"
if [[ -s "$changed_protected_file" ]]; then
  log "Protected upstream paths changed too; review required before applying anything."
fi
