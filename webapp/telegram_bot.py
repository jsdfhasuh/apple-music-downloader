import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib import error, request

from webapp.config_loader import getConfigValue, resolveConfigPath


APPLE_MUSIC_URL_RE = re.compile(r"https://music\.apple\.com/[a-z]{2}/[^\s]+")


@dataclass
class TelegramConfig:
  botToken: str
  allowedChatId: int
  webappBaseUrl: str
  pollIntervalSeconds: float = 3.0
  updatesTimeoutSeconds: int = 30
  storePath: str = "webapp/data/telegram_tasks.db"


class TelegramTaskStore:
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
        CREATE TABLE IF NOT EXISTS telegram_tasks (
          task_id TEXT PRIMARY KEY,
          chat_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL,
          url TEXT NOT NULL,
          notify_status TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
      )
      connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
      )
      connection.commit()

  def savePendingTask(self, taskId: str, chatId: int, messageId: int, url: str) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO telegram_tasks (task_id, chat_id, message_id, url, notify_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(task_id) DO UPDATE SET
          chat_id = excluded.chat_id,
          message_id = excluded.message_id,
          url = excluded.url,
          notify_status = 'pending',
          updated_at = CURRENT_TIMESTAMP
        """,
        (taskId, chatId, messageId, url),
      )
      connection.commit()

  def listPendingTasks(self) -> list[dict[str, object]]:
    with closing(self._connect()) as connection:
      rows = connection.execute(
        "SELECT task_id, chat_id, message_id, url, notify_status FROM telegram_tasks WHERE notify_status = 'pending'"
      ).fetchall()
    return [dict(row) for row in rows]

  def markTaskNotified(self, taskId: str, status: str) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        "UPDATE telegram_tasks SET notify_status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
        (status, taskId),
      )
      connection.execute("DELETE FROM telegram_tasks WHERE task_id = ?", (taskId,))
      connection.commit()

  def getLastUpdateId(self) -> int | None:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT value FROM bot_state WHERE key = 'last_update_id'"
      ).fetchone()
    if row is None:
      return None
    return int(row["value"])

  def setLastUpdateId(self, updateId: int) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO bot_state (key, value, updated_at)
        VALUES ('last_update_id', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          updated_at = CURRENT_TIMESTAMP
        """,
        (str(updateId),),
      )
      connection.commit()


def extractAppleMusicUrl(text: str) -> str | None:
  match = APPLE_MUSIC_URL_RE.search(text or "")
  if match is None:
    return None
  return match.group(0)


def isAllowedChat(chatId: int, allowedChatId: int) -> bool:
  return chatId == allowedChatId


def formatAcceptedMessage(url: str) -> str:
  return f"已接收，开始下载\n{url}"


def formatCompletedMessage(task: dict[str, object]) -> str:
  result = task.get("result")
  if isinstance(result, list) and result:
    firstItem = result[0] if isinstance(result[0], dict) else {}
    song = str(firstItem.get("song", "")) if isinstance(firstItem, dict) else ""
    path = str(firstItem.get("path", "")) if isinstance(firstItem, dict) else ""
    details = song or path or "任务已完成"
    suffix = f"\n{path}" if path else ""
    return f"下载完成\n{details}{suffix}"
  return "下载完成"


def formatFailedMessage(task: dict[str, object]) -> str:
  errorMessage = str(task.get("error", "download failed")).strip() or "download failed"
  return f"下载失败\n{errorMessage}"


def logBotMessage(message: str) -> None:
  print(f"[telegram-bot] {message}", flush=True)


def handleUpdate(
  update: dict[str, object],
  allowedChatId: int,
  store: TelegramTaskStore,
  createTask: Callable[[str], dict[str, object]],
  sendMessage: Callable[[int, str, int | None], None],
) -> None:
  message = update.get("message")
  if not isinstance(message, dict):
    return
  chat = message.get("chat")
  if not isinstance(chat, dict):
    return
  if str(chat.get("type", "")) != "private":
    return
  chatId = int(chat.get("id", 0))
  if not isAllowedChat(chatId, allowedChatId):
    return
  text = str(message.get("text", ""))
  url = extractAppleMusicUrl(text)
  if not url:
    return
  response = createTask(url)
  messageId = int(message.get("message_id", 0))
  logBotMessage(f"accepted url from chat {chatId}: {url}")
  sendMessage(chatId, formatAcceptedMessage(url), messageId)
  taskId = str(response.get("taskId", "")).strip()
  status = str(response.get("status", "")).strip()
  if taskId and status == "running":
    logBotMessage(f"tracking task {taskId} for chat {chatId}")
    store.savePendingTask(taskId=taskId, chatId=chatId, messageId=messageId, url=url)
  elif status == "completed":
    logBotMessage(f"task for url already completed: {url}")
    sendMessage(chatId, "该链接已下载，直接返回历史记录", messageId)


def pollTaskUpdates(
  store: TelegramTaskStore,
  fetchTask: Callable[[str], dict[str, object]],
  sendMessage: Callable[[int, str, int | None], None],
) -> None:
  for task in store.listPendingTasks():
    taskId = str(task["task_id"])
    payload = fetchTask(taskId)
    status = str(payload.get("status", ""))
    if status == "completed":
      logBotMessage(f"task completed: {taskId}")
      sendMessage(int(task["chat_id"]), formatCompletedMessage(payload), int(task["message_id"]))
      store.markTaskNotified(taskId, "completed")
    elif status == "failed":
      logBotMessage(f"task failed: {taskId}")
      sendMessage(int(task["chat_id"]), formatFailedMessage(payload), int(task["message_id"]))
      store.markTaskNotified(taskId, "failed")


def getTelegramConfig(configPath: Path | None = None) -> TelegramConfig:
  resolvedConfigPath = resolveConfigPath(configPath)
  botToken = getConfigValue(resolvedConfigPath, "telegram-bot-token") or ""
  allowedChatIdRaw = getConfigValue(resolvedConfigPath, "telegram-allowed-chat-id") or ""
  webappBaseUrl = getConfigValue(resolvedConfigPath, "telegram-webapp-base-url") or "http://127.0.0.1:5000"
  storePath = getConfigValue(resolvedConfigPath, "telegram-store-path") or "webapp/data/telegram_tasks.db"
  if not botToken:
    raise ValueError("Missing config value: telegram-bot-token")
  if not allowedChatIdRaw:
    raise ValueError("Missing config value: telegram-allowed-chat-id")
  return TelegramConfig(
    botToken=botToken,
    allowedChatId=int(allowedChatIdRaw),
    webappBaseUrl=webappBaseUrl.rstrip("/"),
    storePath=storePath,
  )


def callJsonApi(url: str, method: str = "GET", payload: dict[str, object] | None = None) -> dict[str, object]:
  requestBody = None
  headers: dict[str, str] = {}
  if payload is not None:
    requestBody = json.dumps(payload).encode("utf-8")
    headers["Content-Type"] = "application/json"
  httpRequest = request.Request(url=url, data=requestBody, method=method, headers=headers)
  with request.urlopen(httpRequest, timeout=60) as response:
    body = response.read().decode("utf-8")
  parsed = json.loads(body)
  if not isinstance(parsed, dict):
    raise ValueError(f"Expected dict response from {url}")
  return parsed


def createDownloadTask(webappBaseUrl: str, url: str) -> dict[str, object]:
  return callJsonApi(
    f"{webappBaseUrl}/api/downloads",
    method="POST",
    payload={"url": url, "force": False, "source": "telegram"},
  )


def fetchTaskStatus(webappBaseUrl: str, taskId: str) -> dict[str, object]:
  return callJsonApi(f"{webappBaseUrl}/api/tasks/{taskId}")


def callTelegramApi(botToken: str, method: str, payload: dict[str, object]) -> dict[str, object]:
  url = f"https://api.telegram.org/bot{botToken}/{method}"
  encodedPayload = json.dumps(payload).encode("utf-8")
  httpRequest = request.Request(
    url=url,
    data=encodedPayload,
    method="POST",
    headers={"Content-Type": "application/json"},
  )
  with request.urlopen(httpRequest, timeout=90) as response:
    body = response.read().decode("utf-8")
  parsed = json.loads(body)
  if not isinstance(parsed, dict) or not parsed.get("ok"):
    raise ValueError(f"Telegram API error calling {method}: {parsed}")
  result = parsed.get("result", {})
  if not isinstance(result, dict):
    return {"result": result}
  return result


def sendTelegramMessage(botToken: str, chatId: int, text: str, replyToMessageId: int | None = None) -> None:
  payload: dict[str, object] = {"chat_id": chatId, "text": text}
  if replyToMessageId is not None:
    payload["reply_to_message_id"] = replyToMessageId
  callTelegramApi(botToken, "sendMessage", payload)


def getUpdates(botToken: str, offset: int | None, timeoutSeconds: int) -> list[dict[str, object]]:
  payload: dict[str, object] = {"timeout": timeoutSeconds}
  if offset is not None:
    payload["offset"] = offset
  result = callTelegramApi(botToken, "getUpdates", payload)
  updates = result.get("result", result)
  if isinstance(updates, list):
    return [item for item in updates if isinstance(item, dict)]
  return []


def runPollingCycle(
  offset: int | None,
  allowedChatId: int,
  updatesTimeoutSeconds: int,
  store: TelegramTaskStore,
  getUpdatesFn: Callable[[int | None, int], list[dict[str, object]]],
  createTask: Callable[[str], dict[str, object]],
  fetchTask: Callable[[str], dict[str, object]],
  sendMessage: Callable[[int, str, int | None], None],
) -> int | None:
  nextOffset = offset
  updates = getUpdatesFn(offset, updatesTimeoutSeconds)
  for update in updates:
    updateId = int(update.get("update_id", 0))
    handleUpdate(
      update=update,
      allowedChatId=allowedChatId,
      store=store,
      createTask=createTask,
      sendMessage=sendMessage,
    )
    store.setLastUpdateId(updateId)
    nextOffset = updateId + 1
  pollTaskUpdates(
    store=store,
    fetchTask=fetchTask,
    sendMessage=sendMessage,
  )
  return nextOffset


def runPollingLoop() -> None:
  config = getTelegramConfig()
  store = TelegramTaskStore(config.storePath)
  lastUpdateId = store.getLastUpdateId()
  offset: int | None = (lastUpdateId + 1) if lastUpdateId is not None else None
  logBotMessage(f"starting polling loop with offset {offset}")
  while True:
    try:
      offset = runPollingCycle(
        offset=offset,
        allowedChatId=config.allowedChatId,
        updatesTimeoutSeconds=config.updatesTimeoutSeconds,
        store=store,
        getUpdatesFn=lambda currentOffset, timeoutSeconds: getUpdates(
          config.botToken,
          currentOffset,
          timeoutSeconds,
        ),
        createTask=lambda url: createDownloadTask(config.webappBaseUrl, url),
        fetchTask=lambda taskId: fetchTaskStatus(config.webappBaseUrl, taskId),
        sendMessage=lambda chatId, text, replyToMessageId=None: sendTelegramMessage(
          config.botToken,
          chatId,
          text,
          replyToMessageId,
        ),
      )
    except error.URLError as exc:
      logBotMessage(f"network error: {exc}")
      time.sleep(config.pollIntervalSeconds)
    except Exception as exc:  # noqa: BLE001
      logBotMessage(f"error: {exc}")
      time.sleep(config.pollIntervalSeconds)


def main() -> None:
  runPollingLoop()


if __name__ == "__main__":
  main()
