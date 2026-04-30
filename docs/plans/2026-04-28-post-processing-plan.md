# Post-Processing (Convert FLAC + Build NFO) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** After a download task finishes, automatically run `tools/convert_to_flac.py` on each downloaded file, then run `tools/build_nfo.py` on each album directory, and only mark the task as completed after the entire chain succeeds.

**Architecture:** Keep the existing runner for downloading unchanged. Wrap the runner inside a new sequential pipeline in `webapp/app.py` that runs download, converts to FLAC, and builds NFO in order, logging all output through the task. Update the task stage mapping and SQLite persistence to reflect the new completion criteria.

**Tech Stack:** Python 3, Flask, sqlite3, subprocess, tools/convert_to_flac.py, tools/build_nfo.py

---

### Task 1: Add failing tests for post-processing pipeline

**Files:**
- Modify: `webapp/tests/test_app.py`

**Step 1: Add a test that verifies convert and build_nfo are called after download**

Create a FakeRunner that records download then calls fake convert/nfo scripts.

**Step 2: Run tests to verify red**

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

Expected:
- new tests fail because post-processing is not called

### Task 2: Implement post-processing pipeline in app.py

**Files:**
- Modify: `webapp/app.py`

**Step 1: Create a pipeline runner that sequences download + convert + NFO**

Add a function that:
- runs download
- calls `python tools/convert_to_flac.py` on each result path
- calls `python tools/build_nfo.py` on each album directory
- updates task stages accordingly

**Step 2: Integrate into task creation flow**

Replace the direct runner call with the pipeline runner.

**Step 3: Update stage mappings**

Add stages for `post_processing` and `building_nfo`.

**Step 4: Update SQLite completion timing**

Move `saveCompleted` from download-end to pipeline-end.

### Task 3: Update frontend and docs

**Files:**
- Modify: `webapp/templates/index.html`
- Modify: `webapp/static/app.js`
- Modify: `README-CN.md`

**Step 1: Add stage display for post_processing and building_nfo**

**Step 2: Document the automatic conversion and NFO behavior**

### Task 4: Verify and rebuild

**Files:**
- All above

**Step 1: Run full test suite**

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

**Step 2: Rebuild webapp Docker image**

```bash
docker build -f webapp/Dockerfile -t apple-music-webapp:test .
```
