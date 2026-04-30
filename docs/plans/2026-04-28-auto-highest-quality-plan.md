# Auto Highest Quality Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the web app always request the highest quality path by default, using the downloader's ALAC/Hi-Res flow instead of user-selectable codecs.

**Architecture:** Remove codec selection from the dashboard UI and treat `/api/downloads` as an auto-highest-quality endpoint. The backend will always run the simplified download flow with ALAC semantics for this endpoint, while the lower-level task route can remain available for internal use if needed.

**Tech Stack:** Flask, vanilla JavaScript, HTML/CSS, unittest

---

### Task 1: Add failing tests for auto-highest-quality behavior

**Files:**
- Modify: `webapp/tests/test_app.py`

**Step 1: Add a failing test for `/api/downloads` ignoring codec overrides**

Verify that posting:

```json
{"url": "https://music.apple.com/...", "codec": "aac"}
```

still causes the runner to execute with `alac`.

**Step 2: Run tests to verify red**

Run:

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

Expected:
- the new test fails because `/api/downloads` still accepts codec overrides

### Task 2: Lock `/api/downloads` to the ALAC path

**Files:**
- Modify: `webapp/app.py`

**Step 1: Change `/api/downloads` to ignore `codec` input**

Always call task creation with `alac` for this endpoint.

**Step 2: Keep dedup behavior intact**

Continue supporting:
- `force=true`
- completed reuse
- running reuse

**Step 3: Keep `/api/tasks` unchanged unless necessary**

Only simplify the main public download entrypoint.

### Task 3: Remove codec selection from the page

**Files:**
- Modify: `webapp/templates/index.html`
- Modify: `webapp/static/app.js`
- Modify: `webapp/static/app.css`

**Step 1: Remove the codec select control**

Replace it with a short hint such as `自动最高音质（Hi-Res/ALAC 优先）`.

**Step 2: Update request payload**

Stop sending `codec` from the page and only send:

```json
{"url": "...", "force": false}
```

**Step 3: Update success text**

Reflect the new behavior in UI messages.

### Task 4: Verify and document the new default

**Files:**
- Modify: `README.md`
- Modify: `README-CN.md`

**Step 1: Run the full test suite**

```bash
.venv/bin/python -m unittest webapp.tests.test_app -v
```

**Step 2: Run a live API check**

```bash
curl -X POST http://127.0.0.1:5000/api/downloads \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://music.apple.com/..."}'
```

Expected:
- task is created or deduped normally
- resulting task/record codec is `alac`

**Step 3: Update docs**

Document that the web app now defaults to auto-highest-quality with Hi-Res/ALAC priority.
