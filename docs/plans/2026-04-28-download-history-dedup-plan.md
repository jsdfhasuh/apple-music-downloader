# Download History Dedup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add SQLite-backed URL download history so completed Apple Music URLs are not downloaded again unless `force=true` is requested.

**Architecture:** Keep the current Flask task execution model, but add a small persistence layer backed by SQLite. Every incoming URL request checks a normalized URL record before starting a task, writes `running` before execution, updates to `completed` only after real success with result paths, and updates to `failed` on errors. The simplified `POST /api/downloads` endpoint will support `force` and optional `codec`.

**Tech Stack:** Python 3, Flask, sqlite3, unittest

---

### Task 1: Add failing tests for download history behavior

**Files:**
- Modify: `webapp/tests/test_app.py`

**Step 1: Write a failing test for successful dedup**

Add a test that:
- creates a completed download for a URL
- calls `POST /api/downloads` again with the same URL
- expects no new runner invocation
- expects the API to return the previous `taskId` and `status: completed`

**Step 2: Write a failing test for `force=true` override**

Add a test that:
- creates a completed record
- calls `POST /api/downloads` again with `force: true`
- expects a new task to be created and the runner to execute again

**Step 3: Write a failing test for `running` reuse**

Add a test that:
- simulates a stored `running` record for the URL
- calls `POST /api/downloads`
- expects the API to return the same running task id instead of creating another task

**Step 4: Run the tests to verify red**

Run:

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

Expected:
- new tests fail because there is no persistence layer yet

### Task 2: Add SQLite download history store

**Files:**
- Modify: `webapp/app.py`

**Step 1: Create a small SQLite repository**

Add a repository object that manages:
- database file path
- schema creation
- get by normalized URL
- upsert running record
- mark completed with result json
- mark failed with error

**Step 2: Define schema**

Use one table with columns:
- `url` primary key
- `task_id`
- `status`
- `codec`
- `result_json`
- `error`
- `created_at`
- `updated_at`

**Step 3: Add URL normalization helper**

For now, normalize by trimming whitespace and keeping the exact URL string after trim.

**Step 4: Initialize the repository in `createApp()`**

Use a default DB path such as:

```text
webapp/data/downloads.db
```

### Task 3: Integrate history checks into task creation

**Files:**
- Modify: `webapp/app.py`

**Step 1: Extend `POST /api/downloads` payload parsing**

Support:

```json
{
  "url": "https://music.apple.com/...",
  "force": false,
  "codec": "alac"
}
```

`url` required, `force` optional, `codec` optional.

**Step 2: Check history before creating a task**

Rules:
- if existing status is `completed` and `force` is false -> return old task info with message `already downloaded`
- if existing status is `running` and `force` is false -> return old task info with message `download already in progress`
- otherwise create a new task

**Step 3: Persist `running` before starting the runner**

When a new task is accepted, immediately write the URL record with:
- `status = running`
- new `task_id`
- chosen codec

**Step 4: Update completion and failure transitions**

When the runner finishes:
- only mark `completed` after task result exists and final status is complete
- mark `failed` on any task failure path

**Step 5: Return minimal API response for `/api/downloads`**

Return:

```json
{
  "taskId": "...",
  "status": "running"
}
```

For dedup reuse, also include a short message.

### Task 4: Verify and document the new behavior

**Files:**
- Modify: `README-CN.md`
- Modify: `README.md`

**Step 1: Run the full test suite**

Run:

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

Expected:
- all tests pass

**Step 2: Run a live API smoke test**

Start Flask and issue:

```bash
curl -X POST http://127.0.0.1:5000/api/downloads \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://music.apple.com/..."}'
```

Then issue the same request again.

Expected:
- first request creates a task
- second request returns the existing completed or running record

**Step 3: Document request parameters**

Update docs to mention:
- `url`
- optional `force`
- optional `codec`
- dedup behavior based on completed URL history
