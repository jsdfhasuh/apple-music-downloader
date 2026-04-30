# Web Dedup Status Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update the web dashboard so repeated URL submissions clearly show `already downloaded` or `download already in progress` states instead of always looking like a fresh task start.

**Architecture:** Keep the backend API contract unchanged and make the frontend interpret `POST /api/downloads` responses more carefully. When the API returns a completed record, the page should render a completed state immediately with result paths. When the API returns an existing running task, the page should show a reuse message and subscribe to the existing task stream.

**Tech Stack:** Flask, vanilla JavaScript, HTML/CSS, unittest

---

### Task 1: Add failing tests for response rendering behavior

**Files:**
- Modify: `webapp/tests/test_app.py`

**Step 1: Add a backend contract test for completed dedup response fields**

Verify the completed dedup response contains:
- `status = completed`
- `message = already downloaded`
- `result` list

**Step 2: Add a backend contract test for running reuse response fields**

Verify the running dedup response contains:
- `status = running`
- `message = download already in progress`
- `taskId`

**Step 3: Run tests**

Run:

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

Expected:
- tests pass or confirm existing contract remains correct

### Task 2: Update web UI state handling

**Files:**
- Modify: `webapp/static/app.js`
- Modify: `webapp/templates/index.html`
- Modify: `webapp/static/app.css`

**Step 1: Add explicit status messages**

Handle three submit outcomes:
- new task started
- existing running task reused
- already downloaded result reused

**Step 2: Render completed dedup state immediately**

When response is `completed`:
- show `已下载` in stage/status area
- set progress to 100
- render result list immediately
- append message to logs without opening SSE

**Step 3: Render running reuse state properly**

When response is `running` with reuse message:
- show reuse message in UI
- append message to logs
- open SSE for returned `taskId`

**Step 4: Add light UI emphasis for dedup states**

Use a small status hint area or message line so users can distinguish:
- fresh download
- reused running task
- already downloaded

**Step 5: Verify in browser manually**

Submit the same completed URL and confirm the page shows completed state immediately without pretending a new task started.
