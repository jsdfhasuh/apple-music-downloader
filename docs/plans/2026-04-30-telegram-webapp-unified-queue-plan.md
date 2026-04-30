# Telegram + Webapp Unified Queue Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a unified task list so web and Telegram downloads share one queue view, and auto-start both webapp and bot in the container.

**Architecture:** Extend `DownloadTask` with `source` and `createdAt`, expose a task list API, update the frontend to poll and render tasks, and update the container entrypoint to launch both processes.

**Tech Stack:** Python 3, Flask, sqlite3, vanilla JS/HTML/CSS, Docker

---

### Task 1: Add failing backend tests for task list + source

**Files:**
- Modify: `webapp/tests/test_app.py`

**Step 1: Write the failing tests**

```python
def testListTasksReturnsSummaryWithSource(self):
  response = self.client.post(
    "/api/downloads",
    data=json.dumps({
      "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
      "source": "telegram",
    }),
    content_type="application/json"
  )

  payload = response.get_json()
  listResponse = self.client.get("/api/tasks")
  listPayload = listResponse.get_json()

  taskItem = next(item for item in listPayload if item["taskId"] == payload["taskId"])
  self.assertEqual(taskItem["source"], "telegram")
  self.assertIn("createdAt", taskItem)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest webapp.tests.test_app -v`
Expected: FAIL because `/api/tasks` does not exist and `source/createdAt` are missing.

### Task 2: Implement task source + createdAt + list API

**Files:**
- Modify: `webapp/app.py`

**Step 1: Add fields to DownloadTask**

```python
@dataclass
class DownloadTask:
  id: str
  url: str
  codec: str
  source: str = "web"
  createdAt: float = field(default_factory=time.time)
```

**Step 2: Update TaskStore.createTask signature**

```python
def createTask(self, url: str, codec: str, source: str) -> DownloadTask:
  task = DownloadTask(id=uuid.uuid4().hex, url=url, codec=codec, source=source)
```

**Step 3: Update createDownloadRoute / createTaskResponse**

```python
source = str(payload.get("source", "web")).strip().lower() or "web"
if source not in {"web", "telegram"}:
  source = "web"
```

**Step 4: Add /api/tasks list route**

```python
@app.get("/api/tasks")
def listTasksRoute():
  tasks = sorted(taskStore.listTasks(), key=lambda task: task.createdAt, reverse=True)
  return jsonify([
    {
      "taskId": task.id,
      "url": task.url,
      "status": task.status,
      "stage": task.stage,
      "progress": task.progress,
      "source": task.source,
      "createdAt": task.createdAt,
    }
    for task in tasks
  ])
```

**Step 5: Run tests**

Run: `.venv/bin/python -m unittest webapp.tests.test_app -v`
Expected: PASS

### Task 3: Add failing bot test for source payload

**Files:**
- Modify: `webapp/tests/test_telegram_bot.py`

**Step 1: Write the failing test**

```python
def testCreateDownloadTaskSendsSource(self):
  calls = []

  def fakeCallJsonApi(url, method="GET", payload=None):
    calls.append((url, method, payload))
    return {"status": "running"}

  original = telegram_bot.callJsonApi
  telegram_bot.callJsonApi = fakeCallJsonApi
  try:
    telegram_bot.createDownloadTask("http://example", "https://music.apple.com/cn/album/foo/123")
  finally:
    telegram_bot.callJsonApi = original

  self.assertEqual(calls[0][2]["source"], "telegram")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: FAIL because payload has no `source`.

### Task 4: Update bot payload

**Files:**
- Modify: `webapp/telegram_bot.py`

**Step 1: Add source to createDownloadTask**

```python
return callJsonApi(
  f"{webappBaseUrl}/api/downloads",
  method="POST",
  payload={"url": url, "force": False, "source": "telegram"},
)
```

**Step 2: Run tests**

Run: `.venv/bin/python -m unittest webapp.tests.test_telegram_bot -v`
Expected: PASS

### Task 5: Frontend task list + selection

**Files:**
- Modify: `webapp/templates/index.html`
- Modify: `webapp/static/app.js`
- Modify: `webapp/static/app.css`

**Step 1: Add task list container in HTML**

```html
<section class="card task-list-card">
  <div class="section-header">
    <h2>任务列表</h2>
  </div>
  <div id="task-list" class="task-list"></div>
</section>
```

**Step 2: Add JS polling + selection**

```javascript
async function fetchTasks() { ... }
function renderTaskList(tasks) { ... }
function selectTask(taskId, manual) { ... }
```

**Step 3: Add CSS for task list**

```css
.task-list { display: grid; gap: 12px; }
.task-item { ... }
.task-item.selected { ... }
```

**Step 4: Manual verification**

- 打开前端页面，确认任务列表显示
- Telegram 触发下载后，任务列表出现新任务
- 选中任务可看到实时进度与日志

### Task 6: Container entrypoint to start webapp + bot

**Files:**
- Create: `webapp/start.sh`
- Modify: `webapp/Dockerfile`

**Step 1: Add start script**

```bash
#!/usr/bin/env bash
set -euo pipefail

python webapp/app.py &
webappPid=$!
python webapp/telegram_bot.py &
botPid=$!

trap 'kill $webappPid $botPid' TERM INT
wait -n $webappPid $botPid
exitCode=$?
kill $webappPid $botPid >/dev/null 2>&1 || true
wait $webappPid $botPid >/dev/null 2>&1 || true
exit $exitCode
```

**Step 2: Update Dockerfile CMD**

```dockerfile
CMD ["bash", "webapp/start.sh"]
```

**Step 3: Manual verification**

- Build image and run container
- `docker logs` should show both webapp + bot outputs

### Task 7: Docs update

**Files:**
- Modify: `README.md`

**Step 1: Update Flask dashboard section**

- 标注容器启动会自动带起 bot
- 提示日志查看位置（容器 stdout）

### Task 8: Final verification

**Step 1: Run tests**

Run: `.venv/bin/python -m unittest webapp.tests.test_app webapp.tests.test_telegram_bot -v`
Expected: PASS

**Step 2: Manual flow**

- 浏览器打开前端，提交 URL
- Telegram 私聊发送 URL
- 前端列表显示两个任务并可切换查看日志
