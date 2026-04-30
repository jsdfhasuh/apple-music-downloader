import json
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator

from flask import Flask, Response, jsonify, render_template, request


APPLE_MUSIC_URL_RE = re.compile(r"^https://music\.apple\.com/[a-z]{2}/")
DECRYPT_PROGRESS_RE = re.compile(r"Decrypting\.\.\.\s+(\d+)%")


@dataclass
class TaskEvent:
  eventType: str
  payload: dict[str, object]


@dataclass
class DownloadTask:
  id: str
  url: str
  codec: str
  source: str = "web"
  createdAt: float = field(default_factory=time.time)
  status: str = "pending"
  stage: str = "pending"
  progress: int = 0
  logs: list[str] = field(default_factory=list)
  result: list[dict[str, str]] = field(default_factory=list)
  error: str = ""
  events: list[TaskEvent] = field(default_factory=list)
  condition: threading.Condition = field(default_factory=threading.Condition)

  def toDict(self) -> dict[str, object]:
    return {
      "taskId": self.id,
      "url": self.url,
      "codec": self.codec,
      "source": self.source,
      "createdAt": self.createdAt,
      "status": self.status,
      "stage": self.stage,
      "progress": self.progress,
      "logs": list(self.logs),
      "result": list(self.result),
      "error": self.error,
    }

  def publishEvent(self, eventType: str, **payload: object) -> None:
    with self.condition:
      self.events.append(TaskEvent(eventType=eventType, payload=payload))
      self.condition.notify_all()

  def appendLog(self, line: str) -> None:
    cleaned = line.strip()
    if not cleaned:
      return
    with self.condition:
      self.logs.append(cleaned)
      self.events.append(TaskEvent(eventType="log", payload={"message": cleaned}))
      self.condition.notify_all()

  def setStatus(self, status: str) -> None:
    self.status = status
    self.publishEvent("status", status=status)

  def setStage(self, stage: str) -> None:
    self.stage = stage
    self.publishEvent("stage", stage=stage)

  def setProgress(self, progress: int) -> None:
    bounded = max(0, min(progress, 100))
    self.progress = bounded
    self.publishEvent("progress", progress=bounded)

  def setResult(self, result: list[dict[str, str]]) -> None:
    self.result = result

  def setError(self, message: str) -> None:
    self.error = message
    self.publishEvent("error", error=message)


class DownloadHistoryStore:
  def __init__(self, dbPath: str) -> None:
    self.dbPath = dbPath
    Path(dbPath).parent.mkdir(parents=True, exist_ok=True)
    self._initDb()

  def _connect(self) -> sqlite3.Connection:
    connection = sqlite3.connect(self.dbPath)
    connection.row_factory = sqlite3.Row
    return connection

  def _initDb(self) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        CREATE TABLE IF NOT EXISTS downloads (
          url TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          status TEXT NOT NULL,
          codec TEXT NOT NULL,
          result_json TEXT NOT NULL DEFAULT '[]',
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
      )
      connection.commit()

  def getByUrl(self, url: str) -> dict[str, str] | None:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT url, task_id, status, codec, result_json, error, created_at, updated_at FROM downloads WHERE url = ?",
        (url,)
      ).fetchone()
    if row is None:
      return None
    return {key: str(row[key]) for key in row.keys()}

  def saveRunning(self, url: str, taskId: str, codec: str) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO downloads (url, task_id, status, codec, result_json, error, created_at, updated_at)
        VALUES (?, ?, 'running', ?, '[]', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          task_id = excluded.task_id,
          status = 'running',
          codec = excluded.codec,
          result_json = '[]',
          error = '',
          updated_at = CURRENT_TIMESTAMP
        """,
        (url, taskId, codec)
      )
      connection.commit()

  def saveCompleted(self, url: str, taskId: str, codec: str, result: list[dict[str, str]]) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        UPDATE downloads
        SET task_id = ?, status = 'completed', codec = ?, result_json = ?, error = '', updated_at = CURRENT_TIMESTAMP
        WHERE url = ?
        """,
        (taskId, codec, json.dumps(result, ensure_ascii=False), url)
      )
      connection.commit()

  def saveFailed(self, url: str, taskId: str, codec: str, error: str) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO downloads (url, task_id, status, codec, result_json, error, created_at, updated_at)
        VALUES (?, ?, 'failed', ?, '[]', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          task_id = excluded.task_id,
          status = 'failed',
          codec = excluded.codec,
          error = excluded.error,
          updated_at = CURRENT_TIMESTAMP
        """,
        (url, taskId, codec, error)
      )
      connection.commit()


class TaskStore:
  def __init__(self) -> None:
    self._tasks: dict[str, DownloadTask] = {}
    self._lock = threading.Lock()

  def createTask(self, url: str, codec: str, source: str = "web") -> DownloadTask:
    task = DownloadTask(id=uuid.uuid4().hex, url=url, codec=codec, source=source)
    with self._lock:
      self._tasks[task.id] = task
    return task

  def getTask(self, taskId: str) -> DownloadTask | None:
    with self._lock:
      return self._tasks.get(taskId)

  def listTasks(self) -> list[DownloadTask]:
    with self._lock:
      return list(self._tasks.values())


class DownloaderRunner:
  def __call__(self, task: DownloadTask, url: str, codec: str) -> None:
    command = buildCommand(url, codec)
    process = subprocess.Popen(
      command,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      bufsize=0,
    )
    assert process.stdout is not None
    task.setStatus("running")
    for line in iterOutput(process.stdout):
      task.appendLog(line)
      updateTaskFromLine(task, line)
    returnCode = process.wait()


class PipelineRunner:
  def __init__(self, downloadRunner: Callable[[DownloadTask, str, str], None]) -> None:
    self.downloadRunner = downloadRunner

  def __call__(self, task: DownloadTask, url: str, codec: str) -> None:
    task.setStatus("running")
    self.downloadRunner(task, url, codec)
    if task.status == "failed":
      return
    if task.result:
      if task.status == "completed":
        task.setStatus("running")
      task.setStage("post_processing")
      task.appendLog("Post-processing: converting to FLAC...")
      self._convertToFlac(task)
    if task.status not in {"failed"} and task.result:
      task.setStage("building_nfo")
      task.appendLog("Post-processing: building NFO...")
      self._buildNfo(task)
    if task.status not in {"failed"}:
      task.setStatus("completed")
      task.setStage("completed")
      task.setProgress(100)

  def _convertToFlac(self, task: DownloadTask) -> None:
    updatedResult: list[dict[str, str]] = []
    for item in task.result:
      sourcePath = item.get("path", "")
      if not sourcePath:
        updatedResult.append(item)
        continue
      if not sourcePath.lower().endswith(".m4a"):
        updatedResult.append(item)
        task.appendLog(f"Skipped conversion (not M4A): {sourcePath}")
        continue
      if not Path(sourcePath).exists():
        task.setStage("failed")
        task.setStatus("failed")
        task.setError(
          f"Source file not accessible: {sourcePath}. Mount the downloads directory into the webapp container."
        )
        return
      output = self._runScript(task, ["python", "tools/convert_to_flac.py", sourcePath])
      flacPath = Path(sourcePath).with_suffix(".flac")
      if output is None:
        return
      finalPath = flacPath
      outputPath = Path(output.strip()) if output.strip() else None
      if outputPath is not None and outputPath.exists():
        finalPath = outputPath
      if not finalPath.exists():
        task.setStage("failed")
        task.setStatus("failed")
        task.setError(f"Converted file not found: {finalPath}")
        return
      task.appendLog(f"Conversion output: {finalPath}")
      newItem = {**item, "path": str(finalPath)}
      updatedResult.append(newItem)
    task.setResult(updatedResult)
    task.publishEvent("result", result=updatedResult)

  def _buildNfo(self, task: DownloadTask) -> None:
    directories: set[str] = set()
    for item in task.result:
      parent = str(Path(item["path"]).parent) if item.get("path") else ""
      if parent:
        directories.add(parent)
    if not directories:
      task.appendLog("No album directories found, skipping NFO build")
      return
    for directory in sorted(directories):
      self._runScript(task, ["python", "tools/build_nfo.py", directory])

  def _runScript(self, task: DownloadTask, command: list[str]) -> str | None:
    task.appendLog(f"Running: {' '.join(command)}")
    process = subprocess.Popen(
      command,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      bufsize=0,
    )
    assert process.stdout is not None
    lastLine = ""
    for line in iterOutput(process.stdout):
      if line.strip():
        task.appendLog(line)
        lastLine = line.strip()
    returnCode = process.wait()
    if returnCode != 0:
      task.setStage("failed")
      task.setStatus("failed")
      scriptName = Path(command[1]).name if len(command) > 1 else "script"
      task.setError(f"{scriptName} exited with code {returnCode}")
      return None
    return lastLine


def buildCommand(url: str, codec: str) -> list[str]:
  command = [
    "docker",
    "exec",
    "-w",
    "/app",
    "applemusic_download",
    "apple-music-dl",
    "--json",
  ]
  if codec == "aac":
    command.append("--aac")
  elif codec == "atmos":
    command.append("--atmos")
  command.append(url)
  return command


def iterOutput(stream) -> Generator[str, None, None]:
  buffer = ""
  while True:
    char = stream.read(1)
    if char == "":
      if buffer.strip():
        yield buffer.strip()
      break
    if char in {"\n", "\r"}:
      if buffer.strip():
        yield buffer.strip()
      buffer = ""
      continue
    buffer += char


def updateTaskFromLine(task: DownloadTask, line: str) -> None:
  if line.startswith("Queue "):
    task.setStage("queued")
    task.setProgress(max(task.progress, 5))
    return
  if line.startswith("Track "):
    task.setStage("resolving")
    task.setProgress(max(task.progress, 15))
    return
  if line.startswith("Downloading"):
    task.setStage("downloading")
    task.setProgress(max(task.progress, 30))
    return
  progressMatch = DECRYPT_PROGRESS_RE.search(line)
  if progressMatch:
    task.setStage("decrypting")
    percent = int(progressMatch.group(1))
    mappedPercent = min(89, 30 + int(percent * 0.6))
    task.setProgress(max(task.progress, mappedPercent))
    return
  if line == "Decrypted":
    task.setStage("tagging")
    task.setProgress(max(task.progress, 90))
    return
  if line.startswith("Converting ->"):
    task.setStage("converting")
    task.setProgress(max(task.progress, 95))
    return
  if line.startswith("Conversion completed"):
    task.setStage("converting")
    task.setProgress(max(task.progress, 98))
    return
  if line.startswith("=======  [✔ ] Completed:"):
    task.setStage("completed")
    task.setStatus("completed")
    task.setProgress(100)
    return
  if line.startswith("["):
    try:
      parsed = json.loads(line)
    except json.JSONDecodeError:
      return
    if isinstance(parsed, list):
      result: list[dict[str, str]] = []
      for item in parsed:
        if isinstance(item, dict):
          normalized: dict[str, str] = {}
          for key, value in item.items():
            if isinstance(key, str) and isinstance(value, str):
              normalized[key] = value
          result.append(normalized)
      task.setResult(result)
      task.publishEvent("result", result=result)
      return
  if "Failed" in line or "Error" in line:
    task.setStage("failed")
    task.setStatus("failed")
    task.setError(line)


def normalizeUrl(url: str) -> str:
  return url.strip()


def parseStoredResult(rawResult: str) -> list[dict[str, str]]:
  try:
    parsed = json.loads(rawResult)
  except json.JSONDecodeError:
    return []
  if not isinstance(parsed, list):
    return []
  result: list[dict[str, str]] = []
  for item in parsed:
    if isinstance(item, dict):
      normalized: dict[str, str] = {}
      for key, value in item.items():
        if isinstance(key, str) and isinstance(value, str):
          normalized[key] = value
      result.append(normalized)
  return result


def createTaskResponse(
  app: Flask,
  taskStore: TaskStore,
  historyStore: DownloadHistoryStore,
  url: str,
  codec: str,
  source: str = "web",
):
  task = taskStore.createTask(url, codec, source)
  task.setStatus("running")
  runner = app.config["RUNNER_FACTORY"]()
  historyStore.saveRunning(url, task.id, codec)
  responsePayload = task.toDict()

  def runTask() -> None:
    try:
      runner(task, url, codec)
      if task.status == "completed" and task.result:
        historyStore.saveCompleted(url, task.id, codec, task.result)
      elif task.status == "failed":
        historyStore.saveFailed(url, task.id, codec, task.error or "download failed")
    except Exception as exc:  # noqa: BLE001
      task.setStage("failed")
      task.setStatus("failed")
      task.setError(str(exc))
      historyStore.saveFailed(url, task.id, codec, str(exc))

  if app.testing:
    runTask()
  else:
    thread = threading.Thread(target=runTask, daemon=True)
    thread.start()

  return jsonify(responsePayload), 202


def createApp(
  runnerFactory: Callable[[], Callable[[DownloadTask, str, str], None]] | None = None,
  dbPath: str | None = None
) -> Flask:
  app = Flask(__name__, template_folder="templates", static_folder="static")
  taskStore = TaskStore()
  historyDbPath = dbPath or str(Path("webapp/data/downloads.db"))
  historyStore = DownloadHistoryStore(historyDbPath)
  app.config["TASK_STORE"] = taskStore
  app.config["HISTORY_STORE"] = historyStore
  app.config["RUNNER_FACTORY"] = runnerFactory or (lambda: PipelineRunner(DownloaderRunner()))

  @app.get("/")
  def index() -> str:
    return render_template("index.html")

  @app.post("/api/tasks")
  def createTaskRoute():
    payload = request.get_json(silent=True) or {}
    url = normalizeUrl(str(payload.get("url", "")))
    codec = str(payload.get("codec", "alac")).strip() or "alac"
    if not APPLE_MUSIC_URL_RE.match(url):
      return jsonify({"error": "invalid Apple Music URL"}), 400
    if codec not in {"alac", "aac", "atmos"}:
      return jsonify({"error": "unsupported codec"}), 400

    return createTaskResponse(app, taskStore, historyStore, url, codec)

  @app.post("/api/downloads")
  def createDownloadRoute():
    payload = request.get_json(silent=True) or {}
    url = normalizeUrl(str(payload.get("url", "")))
    force = bool(payload.get("force", False))
    source = str(payload.get("source", "web")).strip().lower() or "web"
    if not APPLE_MUSIC_URL_RE.match(url):
      return jsonify({"error": "invalid Apple Music URL"}), 400
    codec = "alac"
    if source not in {"web", "telegram"}:
      source = "web"

    if not force:
      existing = historyStore.getByUrl(url)
      if existing is not None:
        storedResult = parseStoredResult(existing["result_json"])
        if existing["status"] == "completed":
          return jsonify({
            "taskId": existing["task_id"],
            "status": "completed",
            "message": "already downloaded",
            "result": storedResult,
          }), 200
        if existing["status"] == "running":
          activeTask = taskStore.getTask(existing["task_id"])
          if activeTask is None or activeTask.status != "running":
            return createTaskResponse(app, taskStore, historyStore, url, codec, source)
          return jsonify({
            "taskId": existing["task_id"],
            "status": "running",
            "message": "download already in progress",
          }), 200

    return createTaskResponse(app, taskStore, historyStore, url, codec, source)

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

  @app.get("/api/tasks/<taskId>")
  def getTaskRoute(taskId: str):
    task = taskStore.getTask(taskId)
    if task is None:
      return jsonify({"error": "task not found"}), 404
    return jsonify(task.toDict())

  @app.get("/api/tasks/<taskId>/stream")
  def streamTaskRoute(taskId: str):
    task = taskStore.getTask(taskId)
    if task is None:
      return jsonify({"error": "task not found"}), 404

    def eventStream() -> Generator[str, None, None]:
      yield formatSse("snapshot", task.toDict())
      eventIndex = 0
      while True:
        with task.condition:
          while eventIndex >= len(task.events) and task.status not in {"completed", "failed"}:
            task.condition.wait(timeout=1.0)
            yield formatSse("snapshot", task.toDict())
          events = task.events[eventIndex:]
          eventIndex = len(task.events)
        for event in events:
          yield formatSse(event.eventType, event.payload)
        if task.status in {"completed", "failed"} and eventIndex >= len(task.events):
          yield formatSse("snapshot", task.toDict())
          break

    return Response(eventStream(), mimetype="text/event-stream")

  @app.get("/api/summary")
  def summaryRoute():
    tasks = taskStore.listTasks()
    summary = {
      "queueCount": sum(1 for task in tasks if task.status == "running"),
      "successCount": sum(1 for task in tasks if task.status == "completed"),
      "failureCount": sum(1 for task in tasks if task.status == "failed"),
      "activeTaskCount": sum(1 for task in tasks if task.status == "running"),
    }
    return jsonify(summary)

  return app


def formatSse(eventType: str, payload: dict[str, object]) -> str:
  return f"event: {eventType}\ndata: {json.dumps({'type': eventType, **payload}, ensure_ascii=False)}\n\n"


if __name__ == "__main__":
  createApp().run(host="0.0.0.0", port=5000, debug=False, threaded=True)
