# Telegram Private Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Telegram private-chat bot that accepts Apple Music URLs, triggers the existing download pipeline, and sends completion or failure messages back to the same chat.

**Architecture:** Keep `webapp/app.py` as the single source of truth for downloads. Add a separate polling process in `webapp/telegram_bot.py` that uses Telegram Bot API `getUpdates`, stores `taskId` to Telegram message context in a dedicated sqlite file, and polls existing task endpoints until each task completes.

**Tech Stack:** Python 3, unittest, sqlite3, urllib.request, existing Flask APIs

---

### Task 1: Add failing tests for Telegram helpers and store

**Files:**
- Create: `webapp/tests/test_telegram_bot.py`
- Test: `webapp/tests/test_telegram_bot.py`

**Step 1: Write the failing tests**

```python
def testExtractAppleMusicUrlReturnsFirstMatch(self):
  result = extractAppleMusicUrl("see https://music.apple.com/cn/album/foo/123 and more")
  self.assertEqual(result, "https://music.apple.com/cn/album/foo/123")


def testTelegramTaskStoreSavesAndListsPendingTasks(self):
  store.savePendingTask(...)
  tasks = store.listPendingTasks()
  self.assertEqual(len(tasks), 1)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: FAIL because helpers and store do not exist yet.

### Task 2: Implement minimal Telegram helpers and sqlite store

**Files:**
- Create: `webapp/telegram_bot.py`
- Test: `webapp/tests/test_telegram_bot.py`

**Step 1: Implement pure helpers**

```python
def extractAppleMusicUrl(text: str) -> str | None:
  ...


def isAllowedChat(chatId: int, allowedChatId: int) -> bool:
  ...
```

**Step 2: Implement TelegramTaskStore**

```python
class TelegramTaskStore:
  def savePendingTask(...):
    ...
```

**Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: PASS

### Task 3: Add message handling against existing download API

**Files:**
- Modify: `webapp/telegram_bot.py`
- Test: `webapp/tests/test_telegram_bot.py`

**Step 1: Write failing tests for message handling**

Cover:
- ignore non-private chats
- ignore wrong chat ID
- ignore messages without Apple Music URLs
- create a task on valid URL
- reuse `completed` or `running` responses from `/api/downloads`

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: FAIL because message handler does not exist yet.

**Step 3: Implement minimal handler**

```python
def handleUpdate(update: dict[str, object], ...):
  ...
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: PASS

### Task 4: Add completion polling and Telegram notifications

**Files:**
- Modify: `webapp/telegram_bot.py`
- Test: `webapp/tests/test_telegram_bot.py`

**Step 1: Write failing tests for polling and notification**

Cover:
- notify on `completed`
- notify on `failed`
- mark task as notified
- do not send duplicate notifications

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: FAIL because poller logic does not exist yet.

**Step 3: Implement minimal poller**

```python
def pollTaskUpdates(...):
  ...
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: PASS

### Task 5: Add bot runtime configuration and polling loop

**Files:**
- Modify: `webapp/telegram_bot.py`
- Modify: `webapp/requirements.txt` if needed

**Step 1: Implement config loading**

```python
def getTelegramConfig() -> TelegramConfig:
  ...
```

**Step 2: Implement long-poll loop using standard library**

Use Telegram Bot API endpoints:
- `getUpdates`
- `sendMessage`

**Step 3: Add a `main()` entrypoint**

Run: `.venv/bin/python -m py_compile webapp/telegram_bot.py`
Expected: PASS

### Task 6: Document and verify end-to-end pieces

**Files:**
- Modify: `README.md`
- Modify: `README-CN.md`
- Modify: `webapp/Dockerfile` only if runtime command guidance needs examples

**Step 1: Document required environment variables**

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_ID`
- `TELEGRAM_WEBAPP_BASE_URL`

**Step 2: Run full test suite**

Run: `.venv/bin/python -m unittest webapp.tests.test_app webapp.tests.test_build_nfo webapp.tests.test_telegram_bot -v`
Expected: PASS

**Step 3: Run compile check**

Run: `.venv/bin/python -m py_compile webapp/app.py webapp/tests/test_app.py webapp/tests/test_build_nfo.py webapp/tests/test_telegram_bot.py webapp/telegram_bot.py tools/build_nfo.py`
Expected: PASS

**Step 4: Build image**

Run: `docker build -f webapp/Dockerfile -t apple-music-webapp:test .`
Expected: PASS
