# Telegram Menu Phase 2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the Telegram bot with richer task views and slash commands while keeping the existing direct-link download flow unchanged.

**Architecture:** Reuse the existing `/api/tasks` endpoint as the single source for task summaries, then filter and format those tasks in `webapp.telegram_bot` for failed, running, and recent-result views. Register Telegram slash commands at bot startup via `setMyCommands`, and route both reply-keyboard labels and slash command text through the same handler functions.

**Tech Stack:** Python stdlib, Telegram Bot HTTP API, unittest

---

### Task 1: Command-routing tests

**Files:**
- Modify: `webapp/tests/test_telegram_bot.py`
- Modify: `webapp/telegram_bot.py`

**Step 1: Write the failing test**

Add tests for:
- `/start` and `/help` showing help + keyboard
- `/failed`, `/running`, `/recent` routing to filtered task views
- task filtering helpers returning the expected subsets

**Step 2: Run test to verify it fails**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k "failed or running or recent or start or help" -v`
Expected: FAIL because the new commands/helpers do not exist yet

**Step 3: Write minimal implementation**

Add:
- slash-command normalization
- task filtering helpers
- formatters for failed/running/recent task views
- shared routing for keyboard labels and slash commands

**Step 4: Run test to verify it passes**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k "failed or running or recent or start or help" -v`
Expected: PASS

### Task 2: Slash command registration

**Files:**
- Modify: `webapp/telegram_bot.py`
- Modify: `webapp/tests/test_telegram_bot.py`

**Step 1: Write the failing test**

Add tests for:
- command payload generation
- `setTelegramCommands(...)` calling `setMyCommands`

**Step 2: Run test to verify it fails**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k "setMyCommands or command payload" -v`
Expected: FAIL because command registration helpers do not exist yet

**Step 3: Write minimal implementation**

Add:
- a helper returning Telegram command payload
- a helper calling `setMyCommands`
- one registration call before the polling loop starts

**Step 4: Run test to verify it passes**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -k "setMyCommands or command payload" -v`
Expected: PASS

### Task 3: Verification

**Files:**
- Modify: `webapp/tests/test_telegram_bot.py`

**Step 1: Run Telegram bot tests**

Run: `python -m pytest webapp/tests/test_telegram_bot.py -v`
Expected: PASS

**Step 2: Run related regression tests**

Run: `python -m pytest webapp/tests/test_app.py webapp/tests/test_build_nfo.py webapp/tests/test_telegram_bot.py`
Expected: PASS
