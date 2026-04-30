# Flask Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a minimal Flask web dashboard that triggers `apple-music-dl` through `docker exec`, streams live logs and parsed progress to the browser, and shows the final downloaded file paths.

**Architecture:** Keep the existing Go downloader unchanged and treat it as an external execution engine. Build a separate Flask app that manages in-memory download tasks, starts subprocesses for `docker exec`, parses stdout into stage/progress events, and pushes those events to a browser via Server-Sent Events. The UI will be server-rendered with a single lightweight page using vanilla JavaScript.

**Tech Stack:** Python 3, Flask, Server-Sent Events, vanilla JavaScript, HTML/CSS, Docker CLI

---

### Task 1: Create Flask app skeleton

**Files:**
- Create: `webapp/app.py`
- Create: `webapp/requirements.txt`
- Create: `webapp/templates/index.html`
- Create: `webapp/static/app.css`
- Create: `webapp/static/app.js`

**Step 1: Write the failing smoke test plan manually**

Target behavior:
- Flask app exposes `/`
- Flask app exposes `POST /api/tasks`
- Flask app exposes `GET /api/tasks/<taskId>/stream`
- Flask app exposes `GET /api/tasks/<taskId>`

**Step 2: Create minimal Flask application**

Implement:
- `createApp()` returning a Flask instance
- root route rendering `index.html`
- placeholder JSON routes returning `501` until later tasks fill them in

**Step 3: Add Python dependency file**

Use a minimal dependency set:

```text
Flask==3.1.0
```

**Step 4: Add placeholder template and assets**

Create a static page shell with:
- title area
- URL form area
- progress card
- log panel
- recent results panel

**Step 5: Verify the skeleton starts**

Run:

```bash
python3 -m venv .venv && .venv/bin/pip install -r webapp/requirements.txt && .venv/bin/python webapp/app.py
```

Expected:
- Flask development server starts without import errors

### Task 2: Add task state model and process runner

**Files:**
- Modify: `webapp/app.py`

**Step 1: Define task state structures**

Add a small in-memory task store keyed by task id. Each task should track:
- `id`
- `url`
- `status`
- `stage`
- `progress`
- `logs`
- `result`
- `error`

**Step 2: Implement background task execution**

Add a worker function that starts:

```bash
docker exec -w /app applemusic_download apple-music-dl --json <url>
```

Use `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, text=True)` and read line-by-line.

**Step 3: Capture and persist task logs**

Append every output line to the task log buffer.

**Step 4: Parse downloader output into task state**

Recognize at least these patterns:
- `Queue` -> `queued`
- `Track` -> `resolving`
- `Downloading...` -> `downloading`
- `Decrypting... XX%` -> `decrypting` + parsed percent
- `Converting ->` -> `converting`
- `Completed:` -> `completed`
- final JSON array line -> `result`

**Step 5: Verify background execution manually**

Run a real download task from the Flask route and confirm logs accumulate in memory while the subprocess is running.

### Task 3: Implement HTTP API and SSE stream

**Files:**
- Modify: `webapp/app.py`

**Step 1: Implement `POST /api/tasks`**

Accept JSON like:

```json
{"url":"https://music.apple.com/...","codec":"alac"}
```

Create task id, start worker thread, return task metadata.

**Step 2: Implement `GET /api/tasks/<taskId>`**

Return latest task snapshot as JSON.

**Step 3: Implement `GET /api/tasks/<taskId>/stream` using SSE**

Stream JSON events containing:
- `type`
- `status`
- `stage`
- `progress`
- `message`
- `result`

**Step 4: Emit periodic snapshots while running**

Ensure the stream does not wait for completion before sending updates.

**Step 5: Verify SSE manually**

Run:

```bash
curl -N http://127.0.0.1:5000/api/tasks/<taskId>/stream
```

Expected:
- incremental events appear during download

### Task 4: Build the dashboard UI

**Files:**
- Modify: `webapp/templates/index.html`
- Modify: `webapp/static/app.css`
- Modify: `webapp/static/app.js`

**Step 1: Implement the page structure**

Add sections for:
- header with service status badge
- URL input and codec selector
- progress summary card
- live logs card
- recent download result card
- small stat cards

**Step 2: Implement form submission in JavaScript**

POST to `/api/tasks`, store returned task id, and open an `EventSource` to the task stream.

**Step 3: Render live progress updates**

Update:
- stage label
- percent bar width
- current message
- task counters

**Step 4: Render logs and final result**

Append log lines to a scrolling panel and show result paths when task completes.

**Step 5: Verify in browser**

Open the page and confirm one real download shows live logs and a final file path.

### Task 5: Add minimal validation and documentation

**Files:**
- Modify: `webapp/app.py`
- Modify: `README-CN.md`
- Modify: `README.md`

**Step 1: Validate URL input**

Reject empty or obviously invalid non-Apple Music URLs with `400`.

**Step 2: Prevent unsupported interactive modes**

Only expose codec selection and direct URL download from the web UI.

**Step 3: Document how to run the Flask app**

Add short setup instructions covering:
- install Python dependencies
- start Flask app
- required running Docker container name `applemusic_download`

**Step 4: Verify docs match implementation**

Read the final commands and paths back against the code.

**Step 5: Run final end-to-end verification**

Run:

```bash
.venv/bin/python webapp/app.py
```

Then in another shell:

```bash
curl -X POST http://127.0.0.1:5000/api/tasks -H 'Content-Type: application/json' -d '{"url":"https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"}'
```

And:

```bash
curl -N http://127.0.0.1:5000/api/tasks/<taskId>/stream
```

Expected:
- task is created
- stream emits live updates
- final result contains downloaded path
