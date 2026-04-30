# Telegram Update Offset Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist Telegram `update_id` progress so the bot can resume polling from the last processed update after network interruption or process restart.

**Architecture:** Extend the existing SQLite-backed `TelegramTaskStore` with a tiny key/value state table and store `last_update_id` there. The polling loop reads that state on startup, advances it only after a successful `handleUpdate`, and keeps the existing pending-download notification flow unchanged.

**Tech Stack:** Python 3, sqlite3, unittest, Telegram Bot API `getUpdates`

---

### Task 1: Add failing tests for offset persistence

**Files:**
- Modify: `webapp/tests/test_telegram_bot.py`

**Step 1: Write the failing tests**

```python
def testTelegramTaskStorePersistsLastUpdateId(self):
  store.setLastUpdateId(42)
  self.assertEqual(store.getLastUpdateId(), 42)

def testGetUpdatesStartsFromPersistedOffset(self):
  # Seed last_update_id, run one loop iteration, assert offset=last+1
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest webapp.tests.test_telegram_bot -v`
Expected: FAIL because the new store methods and resumable loop helper do not exist yet.

### Task 2: Implement persisted bot state

**Files:**
- Modify: `webapp/telegram_bot.py`

**Step 1: Add minimal storage support**

```python
CREATE TABLE IF NOT EXISTS bot_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
```

**Step 2: Add store helpers**

```python
def getLastUpdateId(self) -> int | None: ...
def setLastUpdateId(self, updateId: int) -> None: ...
```

**Step 3: Extract one polling cycle helper**

```python
def runPollingCycle(config, store, offset):
  ...
  return nextOffset
```

**Step 4: Update loop startup and persistence**

```python
offset = getNextOffset(store.getLastUpdateId())
offset = runPollingCycle(..., offset)
```

### Task 3: Verify with tests

**Files:**
- Test: `webapp/tests/test_telegram_bot.py`

**Step 1: Run targeted tests**

Run: `python3 -m unittest webapp.tests.test_telegram_bot -v`
Expected: PASS

**Step 2: Run broader related tests if needed**

Run: `python3 -m unittest webapp.tests.test_app webapp.tests.test_telegram_bot -v`
Expected: PASS
