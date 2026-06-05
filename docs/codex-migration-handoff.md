# Apple Music Downloader Codex Migration Handoff

## 1. Project Overview

This repository is a fork of `zhaarey/apple-music-downloader` with a substantial `webapp` layer added on top.

The current system has two major responsibilities:

1. The original downloader container, which performs the actual Apple Music download work.
2. A Flask-based web application and Telegram private bot, which provide task submission, queueing, history, and post-processing.

The most important architectural fact is:

- The `webapp` container does **not** download media directly.
- It invokes the existing downloader container named `applemusic_download` via `docker exec`.


## 2. Current Architecture

### Components

1. Downloader container
- Container name must be `applemusic_download`
- Runs the upstream Apple Music downloader CLI
- Handles the real media download work

2. Webapp container
- Image name currently used: `apple-music-webapp:latest`
- Runs Flask app plus Telegram bot from `webapp/start.sh`
- Exposes APIs, queueing, task tracking, history, and post-processing

3. Shared downloads mount
- Downloader writes media under `/downloads`
- Webapp must mount the same `/downloads` path to post-process results

### Task execution flow

1. Web or Telegram submits an Apple Music URL to `/api/downloads`
2. Webapp creates a task
3. Tasks now run through a **serial queue**
4. When a task starts, webapp runs:

```bash
docker exec -w /app applemusic_download apple-music-dl --json <url>
```

5. If download succeeds, webapp continues post-processing in the same task lifecycle:
- `python -m tools.convert_to_flac`
- `python -m tools.build_nfo`
6. Only after the full pipeline succeeds is the task marked `completed`


## 3. Key Directories And Files

### Core runtime files

- `webapp/app.py`
  - Flask app
  - task model
  - serial queue
  - history APIs
  - retry logic
  - downloader log parsing
  - post-processing pipeline

- `webapp/telegram_bot.py`
  - Telegram private bot
  - polling loop
  - pending task tracking
  - queue/help/failed/running/recent commands

- `webapp/config_loader.py`
  - config resolution logic

- `webapp/start.sh`
  - starts Flask app
  - conditionally starts Telegram bot

- `webapp/Dockerfile`
  - builds the webapp image

### Post-processing and downloader integration

- `tools/convert_to_flac.py`
  - converts `.m4a` to `.flac`

- `tools/build_nfo.py`
  - builds album NFO
  - moves completed album directories to configured completed root

- `tools/__init__.py`
  - required so `python -m tools.*` works

- `main.go`
  - upstream downloader entrypoint
  - important because webapp status parsing depends on its log output

### Tests

- `webapp/tests/test_app.py`
- `webapp/tests/test_telegram_bot.py`
- `webapp/tests/test_build_nfo.py`
- `webapp/tests/test_app_js.js`

### Documentation and plans

- `webapp/README.md`
- `README.md`
- `README-CN.md`
- `docs/plans/*.md`

The `docs/plans/` folder contains the design and implementation history for the webapp, Telegram bot, retry logic, queue visibility, and related features.


## 4. Container And Runtime Model

### Current container relationship

The system expects two containers:

1. `applemusic_download`
2. `apple-music-webapp`

The webapp container depends on:

- Docker socket mount: `/var/run/docker.sock`
- Shared downloads mount: `/downloads`
- Persistent app data mount: `/app/data`

### Typical runtime mounts

Recommended pattern:

```bash
-v <host-data-dir>:/app/data
-v <host-downloads-dir>:/downloads
-v /var/run/docker.sock:/var/run/docker.sock
```

### Important runtime assumption

The downloader container name is hard-coded in `webapp/app.py` inside `buildCommand()` as `applemusic_download`.

If the container name changes, Codex must update that logic or ensure the runtime name remains identical.


## 5. Config And Persistent Data

### Config resolution order

The webapp resolves config in this order:

1. `WEBAPP_CONFIG_PATH`
2. `/app/data/config.yaml`
3. repository root `config.yaml`
4. `webapp/config.yaml`
5. `webapp/config.example.yaml`

### Important persistent files

- `data/downloads.db`
  - default history database path in local repo context

- `/app/data/downloads.db`
  - expected history DB path inside the running webapp container

- `/app/data/telegram_tasks.db`
  - Telegram pending task state and last update id

- `/app/data/logs/webapp.log`
- `/app/data/logs/telegram-bot.log`

### High-risk operational detail

There were previously multiple possible DB locations in use:

- `data/downloads.db`
- `docker_data/downloads.db`
- `webapp/data/downloads.db`

Codex should verify which host path is actually mounted into `/app/data` before making assumptions about runtime history.


## 6. Features Already Implemented

### Webapp

- Flask dashboard
- task creation via `/api/downloads`
- task detail API
- task list API
- SSE task stream
- shared Web and Telegram task visibility
- download deduplication by exact URL
- `force=true` support
- serial queue with single active task at a time
- post-processing in the same task lifecycle

### Post-processing

- auto FLAC conversion via `tools.convert_to_flac`
- NFO generation via `tools.build_nfo`
- optional original-file removal controlled by `convert-keep-original: false`
- completed album relocation via `completed-root-folder`

### History and retry

- SQLite-backed history
- `GET /api/history`
- `POST /api/history/retry-failed`
- `POST /api/history/retry`
- history retry only allows URLs currently marked `failed` and never successfully completed
- retry now treats both `queued` and `running` as active tasks
- retry now preserves the original codec instead of forcing `alac`

### Telegram bot

- private chat only
- single allowed chat id only
- polling with persisted `last_update_id`
- reply keyboard
- slash commands:
  - `/start`
  - `/help`
  - `/force`
  - `/retry_failed`
  - `/queue`
  - `/failed`
  - `/running`
  - `/recent`
- startup welcome message
- task completion/failure notifications
- queued-task acceptance message


## 7. Known Behavior And Important Fixes

### Serial queue

This was changed from parallel background execution to a single-active-task queue.

Current behavior:

- first task starts immediately
- later tasks become `queued`
- when current task finishes, next queued task starts automatically
- failure does not stop the queue; the next task still proceeds

### Telegram completed-link response

This was fixed so that when `/api/downloads` returns `completed` immediately, the bot does **not** first send a misleading “started download” message.

### Retry deduplication

This was fixed so repeated retry calls do not create duplicate queued tasks for the same URL.

### Downloader non-zero exit handling

This was fixed so the downloader process exiting non-zero marks the task failed even if no prior log line already did so.

### False failure on retry prompt

This was fixed recently.

The upstream downloader prints these lines when it wants to retry after errors:

- `Error detected, press Enter to try again...`
- `Start trying again...`

Those lines are now excluded from immediate failure classification in `webapp/app.py`, because they do not necessarily mean the overall task is finally failed.


## 8. Remaining Risks And Open Problems

### Log parsing is still fragile

The webapp still relies heavily on parsing downloader stdout.

This is inherently brittle because upstream `main.go` prints many lines containing `Failed` or `Error` that are not always equivalent to final task failure.

Codex should be very careful before expanding failure-matching rules.

### History model stores final state per URL

The current `downloads` table is effectively one row per URL, not a full append-only attempt history.

Consequences:

- a later success can overwrite visible failed state
- history is better thought of as latest known state plus `ever_completed`
- it is not a perfect audit trail of all attempts

### Runtime DB ambiguity

As noted earlier, there have been multiple host DB paths. Runtime verification is required before any history debugging.

### Sensitive local config

Repository-safe config values were replaced with placeholders in tracked files, but local runtime files may still contain real Telegram credentials or other secrets.

Codex must avoid committing runtime secrets from:

- `docker_data/config.yaml`
- `webapp/config.yaml`
- other local untracked config files


## 9. Test And Verification Commands

### Python tests

```bash
python -m pytest webapp/tests/test_app.py webapp/tests/test_telegram_bot.py webapp/tests/test_build_nfo.py -v
```

### Frontend Node tests

```bash
node --test webapp/tests/test_app_js.js
```

### Docker build

```bash
docker build -t apple-music-webapp:latest -f webapp/Dockerfile .
```

### Useful runtime checks

```bash
docker logs apple-music-webapp
docker logs applemusic_download
docker ps -a
```


## 10. Codex Handoff Notes

If Codex takes over this repo, these are the highest-value habits to follow:

1. Read `webapp/app.py` before changing queue, retry, or history behavior.
2. Read `webapp/telegram_bot.py` before changing Telegram UX or notification behavior.
3. Treat downloader stdout parsing as fragile and verify against `main.go` before changing any status logic.
4. Preserve the shared `/downloads` mount assumption unless intentionally redesigning the system.
5. Verify actual runtime DB mount location before debugging missing history.
6. Run Python and Node tests before claiming completion.


## 11. Migration Checklist For Moving From OpenCode To Codex

### Environment handoff

1. Open the repository root in Codex.
2. Confirm the repo is the forked project, not upstream original.
3. Confirm Docker is available in the Codex environment.
4. Confirm the following containers and assumptions still exist:
   - `applemusic_download`
   - `apple-music-webapp`
5. Confirm `/var/run/docker.sock` is available if runtime debugging is needed.

### Config handoff

1. Determine the real runtime config file.
2. Determine the real `/app/data` host mount.
3. Confirm whether Telegram runtime credentials are present locally but intentionally untracked.
4. Avoid committing secrets during migration.

### Validation handoff

1. Run Python test suite.
2. Run Node test suite.
3. If changing runtime behavior, rebuild `apple-music-webapp:latest`.
4. If deploying, restart the `apple-music-webapp` container.

### Operational sanity checks

1. Submit one Telegram URL and confirm it starts immediately.
2. Submit a second Telegram URL and confirm it becomes `queued`.
3. Confirm the first task finishing starts the second task automatically.
4. Confirm completed URLs do not trigger misleading “started download” Telegram messages.


## 12. Short Summary For Codex

If Codex reads only one section, this is the minimal summary:

- The real downloader is the `applemusic_download` container.
- The Flask `webapp` is a controller/orchestrator, not the downloader itself.
- Queueing is now serial: one active task, others queued.
- Task status still depends partly on parsing downloader stdout from `main.go`.
- Post-processing is part of the same task lifecycle.
- Telegram and web share the same task system.
- History is latest-state-per-URL, not a full append-only attempt log.
- Always verify actual mounted config/data paths before debugging runtime behavior.
