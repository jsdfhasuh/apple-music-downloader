# Telegram Reply Keyboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a richer Telegram bot interface with a persistent reply keyboard, a failed-task retry action, queue summary lookup, and inline help text while keeping direct Apple Music URL submission unchanged.

**Architecture:** Extend `webapp.telegram_bot` with a small command router that recognizes fixed button labels before URL parsing. Add Telegram API helpers for reply keyboards and webapp API helpers for batch retry and task list retrieval, then keep all logic inside the existing polling flow so no webhook or callback-query support is needed.

**Tech Stack:** Python stdlib, Flask JSON APIs, Telegram Bot HTTP API, unittest

---

### Task 1: Telegram command routing tests

**Files:**
- Modify: `webapp/tests/test_telegram_bot.py`
- Modify: `webapp/telegram_bot.py`

**Step 1: Write the failing test**

Add tests covering:
- help/menu text command returns a keyboarded response
- retry button triggers batch retry summary message
- queue button returns current task summary
- plain Apple Music URL still creates a task

**Step 2: Run test to verify it fails**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k keyboard -v`
Expected: FAIL because keyboard helpers and command routing do not exist yet

**Step 3: Write minimal implementation**

Add:
- button labels/constants
- keyboard payload builder
- helper formatters for help/retry/queue text
- command dispatch inside `handleUpdate(...)`

**Step 4: Run test to verify it passes**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k keyboard -v`
Expected: PASS

### Task 2: Telegram/Webapp integration helpers

**Files:**
- Modify: `webapp/telegram_bot.py`
- Modify: `webapp/tests/test_telegram_bot.py`

**Step 1: Write the failing test**

Add tests covering:
- retry helper posts to `/api/tasks/retry-failed`
- queue helper gets `/api/tasks`
- Telegram send helper can carry `reply_markup`

**Step 2: Run test to verify it fails**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k reply_markup -v`
Expected: FAIL because helper signatures and API wrappers are missing

**Step 3: Write minimal implementation**

Add:
- `retryFailedTasks(...)`
- `listTasks(...)`
- optional `replyMarkup` support in `sendTelegramMessage(...)`

**Step 4: Run test to verify it passes**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k reply_markup -v`
Expected: PASS

### Task 3: Verification

**Files:**
- Modify: `webapp/tests/test_telegram_bot.py`

**Step 1: Run Telegram bot tests**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -v`
Expected: PASS

**Step 2: Run broader related tests**

Run: `python -m pytest webapp/tests/test_app.py webapp/tests/test_build_nfo.py webapp/tests/test_telegram_bot.py`
Expected: PASS
