# Retry Failed Tasks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a one-click action that retries failed in-memory tasks from both Web and Telegram sources while skipping URLs that already have a completed record.

**Architecture:** Add a dedicated Flask batch endpoint that scans failed `TaskStore` entries, deduplicates them by URL, checks download history and active running tasks, and creates new retry tasks only when needed. Extend the existing task-list UI with a single retry button that calls the new endpoint and reports a concise summary through the current submission note area.

**Tech Stack:** Flask, Python stdlib, vanilla JavaScript, Node test runner, pytest

---

### Task 1: Back-end retry API

**Files:**
- Modify: `webapp/app.py`
- Test: `webapp/tests/test_app.py`

**Step 1: Write the failing test**

Add tests that cover:
- no failed tasks returns zero counts
- failed tasks with completed history are skipped
- failed tasks with active running tasks are skipped
- duplicate failed URLs retry only once
- retry preserves original `source`

**Step 2: Run test to verify it fails**

Run: `python -m pytest webapp/tests/test_app.py -k retryFailed -v`
Expected: FAIL because `/api/tasks/retry-failed` does not exist yet

**Step 3: Write minimal implementation**

Add a helper in `webapp/app.py` that:
- collects failed tasks from `TaskStore`
- deduplicates by URL
- checks `DownloadHistoryStore.getByUrl(url)` for `completed`
- checks for active `running` tasks with the same URL
- calls `createTaskResponse(...)` for retryable entries
- returns summary payload with counts and URLs/taskIds

**Step 4: Run test to verify it passes**

Run: `python -m pytest webapp/tests/test_app.py -k retryFailed -v`
Expected: PASS

### Task 2: Front-end retry action

**Files:**
- Modify: `webapp/templates/index.html`
- Modify: `webapp/static/app.css`
- Modify: `webapp/static/app.js`
- Test: `webapp/tests/test_app_js.js`

**Step 1: Write the failing test**

Add tests that cover:
- summary message formatting for retry results
- optional request helper calling `POST /api/tasks/retry-failed`

**Step 2: Run test to verify it fails**

Run: `node --test webapp/tests/test_app_js.js`
Expected: FAIL because retry helpers do not exist yet

**Step 3: Write minimal implementation**

Add:
- a single retry button in the task-list section header
- a JS helper to call the batch retry endpoint
- a message formatter for retry summary
- a click handler that updates `submission-note` and refreshes the task list

**Step 4: Run test to verify it passes**

Run: `node --test webapp/tests/test_app_js.js`
Expected: PASS

### Task 3: Verification

**Files:**
- Modify: `webapp/tests/test_app.py`
- Modify: `webapp/tests/test_app_js.js`

**Step 1: Run focused back-end tests**

Run: `python -m pytest webapp/tests/test_app.py -k retryFailed -v`
Expected: PASS

**Step 2: Run focused front-end tests**

Run: `node --test webapp/tests/test_app_js.js`
Expected: PASS

**Step 3: Run broader related tests**

Run: `python -m pytest webapp/tests/test_app.py webapp/tests/test_telegram_bot.py webapp/tests/test_build_nfo.py`
Expected: PASS if dependencies are installed; otherwise capture the exact blocker
