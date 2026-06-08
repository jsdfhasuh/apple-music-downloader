import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib import error, parse, request

from webapp.config_loader import getConfigValue, resolveConfigPath


APPLE_MUSIC_URL_RE = re.compile(r"https://music\.apple\.com/[a-z]{2}/[^\s]+")
TRAILING_URL_PUNCTUATION = ".,;:!)]}>，。；：！）】》、"
URL_TERMINATORS = ",;!)]}>，。；：！）】》、"
HELP_BUTTON_TEXT = "帮助"
QUEUE_BUTTON_TEXT = "查看队列"
RETRY_FAILED_BUTTON_TEXT = "重试失败任务"
DOWNLOAD_HINT_BUTTON_TEXT = "下载说明"
FAILED_TASKS_BUTTON_TEXT = "查看失败任务"
RUNNING_TASKS_BUTTON_TEXT = "查看运行中任务"
RECENT_RESULTS_BUTTON_TEXT = "最近结果"
FORCE_DOWNLOAD_HINT_BUTTON_TEXT = "强制下载说明"
SUBSCRIPTION_HINT_BUTTON_TEXT = "订阅说明"
SUBSCRIPTIONS_BUTTON_TEXT = "查看订阅"
SCAN_SUBSCRIPTIONS_BUTTON_TEXT = "扫描订阅"
MENU_BUTTON_TEXT = "显示菜单"
HIDE_MENU_BUTTON_TEXT = "收起菜单"
SUBSCRIPTION_ALBUM_CALLBACK_PREFIX = "sa"
SUBSCRIPTION_REVIEW_PAGE_CALLBACK_PREFIX = "srp"
SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE = 5
SUBSCRIPTION_ALBUM_CALLBACK_ACTIONS = {
  "d": "download",
  "i": "ignore",
  "m": "mark_imported",
  "c": "mark_completed",
  "p": "pending",
}
SUBSCRIPTION_ALBUM_ACTION_CALLBACK_CODES = {
  value: key for key, value in SUBSCRIPTION_ALBUM_CALLBACK_ACTIONS.items()
}


@dataclass
class TelegramConfig:
  botToken: str
  allowedChatId: int
  webappBaseUrl: str
  pollIntervalSeconds: float = 3.0
  updatesTimeoutSeconds: int = 30
  storePath: str = "data/telegram_tasks.db"


@dataclass
class BatchSubmissionSummary:
  extractedCount: int
  uniqueCount: int
  duplicateCount: int
  startedCount: int = 0
  queuedCount: int = 0
  reusedRunningCount: int = 0
  reusedQueuedCount: int = 0
  completedCount: int = 0
  startedUrls: list[str] = field(default_factory=list)
  queuedUrls: list[str] = field(default_factory=list)
  reusedRunningUrls: list[str] = field(default_factory=list)
  reusedQueuedUrls: list[str] = field(default_factory=list)
  completedUrls: list[str] = field(default_factory=list)
  duplicateUrls: list[str] = field(default_factory=list)


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
      connection.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_updates (
          update_id INTEGER PRIMARY KEY,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

  def deletePendingTask(self, taskId: str) -> None:
    with closing(self._connect()) as connection:
      connection.execute("DELETE FROM telegram_tasks WHERE task_id = ?", (taskId,))
      connection.commit()

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

  def hasProcessedUpdate(self, updateId: int) -> bool:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT 1 FROM processed_updates WHERE update_id = ? LIMIT 1",
        (updateId,),
      ).fetchone()
    return row is not None

  def markUpdateProcessed(self, updateId: int) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT OR IGNORE INTO processed_updates (update_id, created_at)
        VALUES (?, CURRENT_TIMESTAMP)
        """,
        (updateId,),
      )
      connection.commit()

  def pruneProcessedUpdates(self, limit: int = 1000) -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        DELETE FROM processed_updates
        WHERE update_id NOT IN (
          SELECT update_id FROM processed_updates ORDER BY update_id DESC LIMIT ?
        )
        """,
        (limit,),
      )
      connection.commit()


def extractAppleMusicUrl(text: str) -> str | None:
  urls = extractAppleMusicUrls(text)
  if not urls:
    return None
  return urls[0]


def extractAppleMusicUrls(text: str) -> list[str]:
  return [
    normalizeExtractedAppleMusicUrl(url)
    for url in APPLE_MUSIC_URL_RE.findall(text or "")
  ]


def normalizeExtractedAppleMusicUrl(url: str) -> str:
  normalized = url.strip()
  firstTerminatorIndex = min(
    (index for token in URL_TERMINATORS if (index := normalized.find(token)) >= 0),
    default=-1,
  )
  if firstTerminatorIndex >= 0:
    normalized = normalized[:firstTerminatorIndex]
  return normalized.rstrip(TRAILING_URL_PUNCTUATION)


def deduplicateUrls(urls: list[str]) -> list[str]:
  uniqueUrls: list[str] = []
  seenUrls: set[str] = set()
  for url in urls:
    if url in seenUrls:
      continue
    seenUrls.add(url)
    uniqueUrls.append(url)
  return uniqueUrls


def isInProgressMessage(message: str) -> bool:
  return message == "download already in progress"


def isQueuedMessage(message: str) -> bool:
  return message == "download already queued"


def shouldTrackTask(taskId: str, status: str) -> bool:
  return bool(taskId) and status in {"queued", "running"}


def trackPendingTask(
  store: TelegramTaskStore,
  taskId: str,
  chatId: int,
  messageId: int,
  url: str,
) -> None:
  store.savePendingTask(taskId=taskId, chatId=chatId, messageId=messageId, url=url)


def summarizeRepeatedUrls(urls: list[str]) -> list[tuple[str, int]]:
  countsByUrl: dict[str, int] = {}
  orderedUrls: list[str] = []
  for url in urls:
    if url not in countsByUrl:
      countsByUrl[url] = 0
      orderedUrls.append(url)
    countsByUrl[url] += 1
  return [(url, countsByUrl[url]) for url in orderedUrls]


def formatUrlSection(title: str, urls: list[str]) -> list[str]:
  if not urls:
    return []
  lines = [f"{title} ({len(urls)}):"]
  lines.extend(f"- {url}" for url in urls)
  return lines


def formatDuplicateSection(title: str, urls: list[str]) -> list[str]:
  if not urls:
    return []
  lines = [f"{title} ({len(urls)}):"]
  for url, count in summarizeRepeatedUrls(urls):
    if count == 1:
      lines.append(f"- {url}")
    else:
      lines.append(f"- {url} (重复 {count} 次)")
  return lines


def formatBatchAcceptedMessage(summary: BatchSubmissionSummary) -> str:
  lines = [f"本条消息共识别 {summary.extractedCount} 个链接，去重后 {summary.uniqueCount} 个"]
  lines.extend(formatUrlSection("开始下载", summary.startedUrls))
  lines.extend(formatUrlSection("新加入队列", summary.queuedUrls))
  lines.extend(formatUrlSection("复用进行中任务", summary.reusedRunningUrls))
  lines.extend(formatUrlSection("复用排队任务", summary.reusedQueuedUrls))
  lines.extend(formatUrlSection("历史已完成", summary.completedUrls))
  lines.extend(formatDuplicateSection("消息内重复跳过", summary.duplicateUrls))
  return "\n".join(lines)


def submitAppleMusicUrls(
  store: TelegramTaskStore,
  createTask: Callable[..., dict[str, object]],
  chatId: int,
  messageId: int,
  urls: list[str],
  force: bool,
) -> BatchSubmissionSummary:
  uniqueUrls = deduplicateUrls(urls)
  seenUrls: set[str] = set()
  summary = BatchSubmissionSummary(
    extractedCount=len(urls),
    uniqueCount=len(uniqueUrls),
    duplicateCount=len(urls) - len(uniqueUrls),
  )
  for url in urls:
    if url in seenUrls:
      summary.duplicateUrls.append(url)
      continue
    seenUrls.add(url)

  for url in uniqueUrls:
    response = createTaskWithOptionalForce(createTask, url, force)
    logBotMessage(f"accepted url from chat {chatId}: {url}")
    taskId = str(response.get("taskId", "")).strip()
    status = str(response.get("status", "")).strip()
    message = str(response.get("message", "")).strip()

    if status == "completed":
      summary.completedCount += 1
      summary.completedUrls.append(url)
      continue
    if status == "running":
      if isInProgressMessage(message):
        summary.reusedRunningCount += 1
        summary.reusedRunningUrls.append(url)
      else:
        summary.startedCount += 1
        summary.startedUrls.append(url)
    elif status == "queued":
      if isQueuedMessage(message):
        summary.reusedQueuedCount += 1
        summary.reusedQueuedUrls.append(url)
      else:
        summary.queuedCount += 1
        summary.queuedUrls.append(url)

    if shouldTrackTask(taskId, status):
      logBotMessage(f"tracking task {taskId} for chat {chatId}")
      trackPendingTask(store, taskId, chatId, messageId, url)

  return summary


def isAllowedChat(chatId: int, allowedChatId: int) -> bool:
  return chatId == allowedChatId


def formatAcceptedMessage(url: str, status: str = "running") -> str:
  if status == "queued":
    return f"已接收，已加入队列，等待开始下载\n{url}"
  return f"已接收，开始下载\n{url}"


def buildReplyKeyboard() -> dict[str, object]:
  return {
    "keyboard": [
      [{"text": DOWNLOAD_HINT_BUTTON_TEXT}, {"text": FORCE_DOWNLOAD_HINT_BUTTON_TEXT}],
      [{"text": RETRY_FAILED_BUTTON_TEXT}, {"text": QUEUE_BUTTON_TEXT}],
      [{"text": FAILED_TASKS_BUTTON_TEXT}, {"text": RUNNING_TASKS_BUTTON_TEXT}],
      [{"text": SUBSCRIPTION_HINT_BUTTON_TEXT}, {"text": SUBSCRIPTIONS_BUTTON_TEXT}],
      [{"text": SCAN_SUBSCRIPTIONS_BUTTON_TEXT}, {"text": RECENT_RESULTS_BUTTON_TEXT}],
      [{"text": HELP_BUTTON_TEXT}, {"text": HIDE_MENU_BUTTON_TEXT}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": True,
  }


def buildRemoveKeyboard() -> dict[str, object]:
  return {"remove_keyboard": True}


def formatHelpMessage() -> str:
  return (
    "直接发送 Apple Music 链接即可下载，支持一条消息多个链接。\n"
    "强制下载请使用 /force <Apple Music URL>，也支持一条消息多个链接。\n"
    "歌手订阅：/artist_search <关键词> 搜索，/subscribe <artist_url> 订阅，/subscriptions 查看，/unsubscribe <artist_id> 取消，/scan_subscriptions 手动扫描。\n"
    "订阅专辑：/subscription_policy <artist_id> confirm|auto 切换策略，/subscription_album <artist_id> <album_id> download|ignore|imported|completed|pending 处理专辑。\n"
    "快捷菜单不会长期展示，发送 /menu 打开，发送 /hide_menu 收起。\n"
    "可用菜单：下载说明、强制下载说明、重试失败任务、查看队列、查看失败任务、查看运行中任务、订阅说明、查看订阅、扫描订阅、最近结果、帮助、收起菜单\n"
    "可用命令：/start /menu /hide_menu /help /force /retry_failed /queue /failed /running /recent /artist_search /subscribe /subscriptions /unsubscribe /scan_subscriptions /subscription_policy /subscription_album"
  )


def formatStartupWelcomeMessage() -> str:
  return "机器人已启动\n直接发送 Apple Music 链接即可下载，支持一条消息多个链接\n发送 /menu 打开快捷菜单，发送 /help 查看完整命令说明。"


def formatForceDownloadHelpMessage() -> str:
  return "强制下载用法：\n/force https://music.apple.com/...\n支持一条消息多个链接，这会对提取到的全部链接强制创建新任务，不复用已完成记录。"


def formatSubscriptionHelpMessage() -> str:
  return (
    "歌手订阅用法：\n"
    "/artist_search <关键词> 搜索歌手\n"
    "/subscribe https://music.apple.com/.../artist/... 订阅歌手\n"
    "/subscriptions 查看已订阅歌手\n"
    "/unsubscribe <artist_id> 取消订阅\n"
    "/scan_subscriptions 手动扫描订阅\n"
    "/subscription_policy <artist_id> confirm|auto 切换新专辑策略\n"
    "/subscription_album <artist_id> <album_id> download|ignore|imported|completed|pending 处理专辑"
  )


def createTaskWithOptionalForce(
  createTask: Callable[..., dict[str, object]],
  url: str,
  force: bool,
) -> dict[str, object]:
  if force:
    return createTask(url, True)
  try:
    return createTask(url, False)
  except TypeError:
    return createTask(url)


def filterTasksByStatus(tasks: list[dict[str, object]], status: str) -> list[dict[str, object]]:
  return [task for task in tasks if str(task.get("status", "")) == status]


def formatRetryFailedTasksMessage(payload: dict[str, object]) -> str:
  retriedCount = int(payload.get("retriedCount", 0) or 0)
  skippedCompletedCount = int(payload.get("skippedCompletedCount", 0) or 0)
  skippedRunningCount = int(payload.get("skippedRunningCount", 0) or 0)
  if retriedCount == 0 and skippedCompletedCount == 0 and skippedRunningCount == 0:
    return "当前没有可重试的失败任务"

  parts = [f"已重试 {retriedCount} 个失败任务"]
  if skippedCompletedCount > 0:
    parts.append(f"跳过 {skippedCompletedCount} 个已成功任务")
  if skippedRunningCount > 0:
    parts.append(f"跳过 {skippedRunningCount} 个进行中任务")
  return "，".join(parts)


def formatTaskListMessage(tasks: list[dict[str, object]], title: str = "当前任务队列") -> str:
  if not tasks:
    return f"{title}为空"

  lines = [title]
  for task in tasks[:5]:
    source = str(task.get("source", "web"))
    status = str(task.get("status", "pending"))
    stage = str(task.get("stage", "idle"))
    url = str(task.get("url", "-"))
    lines.append(f"- [{source}] {status}/{stage}")
    lines.append(url)
  return "\n".join(lines)


def isKeyboardCommand(text: str) -> bool:
  return text in {
    HELP_BUTTON_TEXT,
    QUEUE_BUTTON_TEXT,
    RETRY_FAILED_BUTTON_TEXT,
    DOWNLOAD_HINT_BUTTON_TEXT,
    FORCE_DOWNLOAD_HINT_BUTTON_TEXT,
    FAILED_TASKS_BUTTON_TEXT,
    RUNNING_TASKS_BUTTON_TEXT,
    RECENT_RESULTS_BUTTON_TEXT,
    SUBSCRIPTION_HINT_BUTTON_TEXT,
    SUBSCRIPTIONS_BUTTON_TEXT,
    SCAN_SUBSCRIPTIONS_BUTTON_TEXT,
    MENU_BUTTON_TEXT,
    HIDE_MENU_BUTTON_TEXT,
  }


def normalizeCommand(text: str) -> str:
  candidate = (text or "").strip()
  if not candidate.startswith("/"):
    return candidate
  firstToken = candidate.split()[0]
  command = firstToken[1:]
  return command.split("@", 1)[0].lower()


def getCommandArgument(text: str) -> str:
  candidate = (text or "").strip()
  if not candidate.startswith("/"):
    return ""
  parts = candidate.split(maxsplit=1)
  if len(parts) < 2:
    return ""
  return parts[1].strip()


def formatArtistSearchResultsMessage(results: list[dict[str, object]]) -> str:
  if not results:
    return "没有找到匹配歌手"
  lines = ["搜索结果"]
  for item in results[:5]:
    artistName = str(item.get("artistName", item.get("name", "未知歌手")))
    storefront = str(item.get("storefront", "")).upper()
    artistUrl = str(item.get("artistUrl", item.get("url", "")))
    lines.append(f"- {artistName} {storefront}".rstrip())
    if artistUrl:
      lines.append(artistUrl)
  return "\n".join(lines)


def formatSubscriptionScanSummaryMessage(payload: dict[str, object]) -> str:
  scannedCount = int(payload.get("scannedCount", 1))
  foundCount = int(payload.get("foundCount", 0) or 0)
  queuedCount = int(payload.get("queuedCount", 0) or 0)
  pendingCount = int(payload.get("pendingCount", 0) or 0)
  skippedCompletedCount = int(payload.get("skippedCompletedCount", 0) or 0)
  skippedActiveCount = int(payload.get("skippedActiveCount", 0) or 0)
  skippedIgnoredCount = int(payload.get("skippedIgnoredCount", 0) or 0)
  skippedImportedCount = int(payload.get("skippedImportedCount", 0) or 0)
  errorCount = int(payload.get("errorCount", 0) or 0)
  lines = [
    f"歌手订阅扫描完成：{scannedCount} 个订阅",
    f"发现 {foundCount} 个专辑，待确认 {pendingCount} 个，入队 {queuedCount} 个，历史跳过 {skippedCompletedCount} 个，队列跳过 {skippedActiveCount} 个，忽略 {skippedIgnoredCount} 个，已导入 {skippedImportedCount} 个，错误 {errorCount} 个",
  ]
  errors = payload.get("errors")
  if isinstance(errors, list) and errors:
    lines.append("错误：")
    lines.extend(f"- {str(item)}" for item in errors[:3])
  summaries = payload.get("summaries")
  if isinstance(summaries, list):
    nestedErrors: list[str] = []
    for summary in summaries:
      if isinstance(summary, dict):
        for item in summary.get("errors", []):
          nestedErrors.append(str(item))
    if nestedErrors:
      lines.append("错误：")
      lines.extend(f"- {item}" for item in nestedErrors[:3])
  return "\n".join(lines)


def formatSubscriptionCreatedMessage(payload: dict[str, object]) -> str:
  subscription = payload.get("subscription")
  if not isinstance(subscription, dict):
    return "订阅已创建"
  artistName = str(subscription.get("artistName", "未知歌手"))
  artistId = str(subscription.get("artistId", ""))
  scan = payload.get("scan")
  lines = [f"已订阅 {artistName}", f"artist_id: {artistId}"]
  if isinstance(scan, dict):
    lines.append(formatSubscriptionScanSummaryMessage(scan))
  return "\n".join(lines)


def normalizeSubscriptionAlbumUserState(userState: object) -> str:
  normalized = str(userState or "").strip().lower()
  if normalized in {"pending", "subscribed", "ignored", "imported"}:
    return normalized
  return "subscribed"


def getSubscriptionAlbumDetectedStatus(album: dict[str, object]) -> str:
  detectedStatus = str(album.get("detectedStatus", album.get("detected_status", "")) or "").strip().lower()
  if detectedStatus:
    return detectedStatus
  status = str(album.get("status", "") or "").strip().lower()
  if status == "failed":
    return "failed_history"
  return status or "missing"


def getSubscriptionAlbumStatusLabel(status: str) -> str:
  labels = {
    "queued": "已在队列",
    "running": "下载中",
    "completed": "已完成",
    "failed_history": "失败历史",
    "stale_history": "过期历史",
    "missing": "待处理",
    "seen": "已发现",
  }
  return labels.get(status, status or "未知")


def getSubscriptionAlbumTitle(album: dict[str, object]) -> str:
  title = str(album.get("albumName", album.get("album_name", "")) or "").strip()
  if title:
    return title
  url = str(album.get("albumUrl", album.get("album_url", "")) or "").strip()
  if url:
    parts = [part for part in url.rstrip("/").split("/") if part]
    if len(parts) >= 2:
      return parse.unquote(parts[-2]).replace("-", " ").strip() or parts[-1]
  return str(album.get("albumId", album.get("album_id", "")) or "未知专辑")


def sortSubscriptionAlbums(albums: list[dict[str, object]]) -> list[dict[str, object]]:
  return sorted(
    albums,
    key=lambda album: (
      str(album.get("releaseDate", album.get("release_date", "")) or ""),
      str(album.get("updatedAt", album.get("updated_at", "")) or ""),
      str(album.get("albumId", album.get("album_id", "")) or ""),
    ),
    reverse=True,
  )


def canReviewSubscriptionAlbum(album: dict[str, object]) -> bool:
  if album.get("canDownload") is False:
    return False
  userState = normalizeSubscriptionAlbumUserState(album.get("userState", album.get("user_state", "")))
  if userState != "pending":
    return False
  return getSubscriptionAlbumDetectedStatus(album) in {"missing", "failed_history", "stale_history"}


def listSubscriptionReviewAlbums(subscription: dict[str, object], limit: int | None = None) -> list[dict[str, object]]:
  albums = subscription.get("recentAlbums", [])
  if not isinstance(albums, list):
    return []
  reviewAlbums = [
    album
    for album in albums
    if isinstance(album, dict) and canReviewSubscriptionAlbum(album)
  ]
  sortedAlbums = sortSubscriptionAlbums(reviewAlbums)
  if limit is None:
    return sortedAlbums
  return sortedAlbums[:limit]


def normalizeSubscriptionReviewPageSize(pageSize: int = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE) -> int:
  try:
    normalizedPageSize = int(pageSize)
  except (TypeError, ValueError):
    normalizedPageSize = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE
  return max(1, normalizedPageSize)


def getSubscriptionReviewPageCount(subscription: dict[str, object], pageSize: int = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE) -> int:
  safePageSize = normalizeSubscriptionReviewPageSize(pageSize)
  reviewAlbumCount = len(listSubscriptionReviewAlbums(subscription))
  if reviewAlbumCount == 0:
    return 0
  return ((reviewAlbumCount - 1) // safePageSize) + 1


def clampSubscriptionReviewPage(subscription: dict[str, object], page: int, pageSize: int = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE) -> int:
  pageCount = getSubscriptionReviewPageCount(subscription, pageSize)
  if pageCount == 0:
    return 0
  try:
    requestedPage = int(page)
  except (TypeError, ValueError):
    requestedPage = 0
  return max(0, min(requestedPage, pageCount - 1))


def listSubscriptionReviewPageAlbums(
  subscription: dict[str, object],
  page: int = 0,
  pageSize: int = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE,
) -> tuple[list[dict[str, object]], int, int]:
  safePageSize = normalizeSubscriptionReviewPageSize(pageSize)
  safePage = clampSubscriptionReviewPage(subscription, page, safePageSize)
  reviewAlbums = listSubscriptionReviewAlbums(subscription)
  pageCount = getSubscriptionReviewPageCount(subscription, safePageSize)
  start = safePage * safePageSize
  return reviewAlbums[start:start + safePageSize], safePage, pageCount


def summarizeSubscriptionReviewAlbums(subscription: dict[str, object]) -> dict[str, int]:
  albums = subscription.get("recentAlbums", [])
  summary = {
    "albumCount": 0,
    "pendingCount": 0,
    "activeCount": 0,
    "completedCount": 0,
    "ignoredCount": 0,
    "importedCount": 0,
  }
  if not isinstance(albums, list):
    return summary
  for album in albums:
    if not isinstance(album, dict):
      continue
    summary["albumCount"] += 1
    userState = normalizeSubscriptionAlbumUserState(album.get("userState", album.get("user_state", "")))
    detectedStatus = getSubscriptionAlbumDetectedStatus(album)
    if userState == "pending" and canReviewSubscriptionAlbum(album):
      summary["pendingCount"] += 1
    if detectedStatus in {"queued", "running"}:
      summary["activeCount"] += 1
    if detectedStatus == "completed":
      summary["completedCount"] += 1
    if userState == "ignored":
      summary["ignoredCount"] += 1
    if userState == "imported":
      summary["importedCount"] += 1
  return summary


def formatSubscriptionReviewMessage(
  subscription: dict[str, object],
  page: int = 0,
  pageSize: int = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE,
) -> str:
  artistName = str(subscription.get("artistName", "未知歌手") or "未知歌手")
  summary = summarizeSubscriptionReviewAlbums(subscription)
  safePageSize = normalizeSubscriptionReviewPageSize(pageSize)
  reviewAlbums, safePage, pageCount = listSubscriptionReviewPageAlbums(subscription, page, safePageSize)
  lines = [
    f"{artistName} 专辑确认",
    (
      f"已发现 {summary['albumCount']} 个，待确认 {summary['pendingCount']} 个，"
      f"进行中 {summary['activeCount']} 个，已完成 {summary['completedCount']} 个，"
      f"已忽略 {summary['ignoredCount']} 个，已导入 {summary['importedCount']} 个"
    ),
  ]
  if not reviewAlbums:
    lines.append("没有需要处理的专辑。")
    return "\n".join(lines)

  lines.append(f"待确认专辑：第 {safePage + 1}/{pageCount} 页")
  startIndex = safePage * safePageSize
  for index, album in enumerate(reviewAlbums, start=startIndex + 1):
    releaseDate = str(album.get("releaseDate", album.get("release_date", "")) or "").strip()
    detectedStatus = getSubscriptionAlbumDetectedStatus(album)
    suffix = f" ({releaseDate})" if releaseDate else ""
    lines.append(f"{index}. {getSubscriptionAlbumTitle(album)}{suffix} - {getSubscriptionAlbumStatusLabel(detectedStatus)}")

  hiddenCount = summary["pendingCount"] - (safePage + 1) * safePageSize
  if hiddenCount > 0:
    lines.append(f"还有 {hiddenCount} 个待确认专辑，点下一页继续处理。")
  return "\n".join(lines)


def buildSubscriptionAlbumCallbackData(subscriptionId: str, albumId: str, action: str, page: int | None = None) -> str:
  actionCode = SUBSCRIPTION_ALBUM_ACTION_CALLBACK_CODES.get(action, "")
  quotedAlbumId = parse.quote(str(albumId), safe="")
  callbackData = f"{SUBSCRIPTION_ALBUM_CALLBACK_PREFIX}:{subscriptionId}:{quotedAlbumId}:{actionCode}"
  if page is None:
    return callbackData
  try:
    safePage = int(page)
  except (TypeError, ValueError):
    safePage = 0
  return f"{callbackData}:{max(0, safePage)}"


def parseSubscriptionAlbumCallbackData(data: str) -> tuple[str, str, str, int | None] | None:
  parts = str(data or "").split(":")
  if len(parts) not in {4, 5} or parts[0] != SUBSCRIPTION_ALBUM_CALLBACK_PREFIX:
    return None
  subscriptionId = parts[1].strip()
  albumId = parse.unquote(parts[2].strip())
  action = SUBSCRIPTION_ALBUM_CALLBACK_ACTIONS.get(parts[3].strip())
  if not subscriptionId or not albumId or not action:
    return None
  page: int | None = None
  if len(parts) == 5:
    try:
      page = int(parts[4])
    except ValueError:
      return None
    if page < 0:
      return None
  return subscriptionId, albumId, action, page


def buildSubscriptionReviewPageCallbackData(subscriptionId: str, page: int) -> str:
  return f"{SUBSCRIPTION_REVIEW_PAGE_CALLBACK_PREFIX}:{subscriptionId}:{max(0, page)}"


def parseSubscriptionReviewPageCallbackData(data: str) -> tuple[str, int] | None:
  parts = str(data or "").split(":")
  if len(parts) != 3 or parts[0] != SUBSCRIPTION_REVIEW_PAGE_CALLBACK_PREFIX:
    return None
  subscriptionId = parts[1].strip()
  if not subscriptionId:
    return None
  try:
    page = int(parts[2])
  except ValueError:
    return None
  if page < 0:
    return None
  return subscriptionId, page


def buildSubscriptionReviewKeyboard(
  subscription: dict[str, object],
  page: int = 0,
  pageSize: int = SUBSCRIPTION_REVIEW_ALBUM_PAGE_SIZE,
) -> dict[str, object] | None:
  subscriptionId = str(subscription.get("id", "") or "").strip()
  if not subscriptionId:
    return None
  rows: list[list[dict[str, str]]] = []
  safePageSize = normalizeSubscriptionReviewPageSize(pageSize)
  reviewAlbums, safePage, pageCount = listSubscriptionReviewPageAlbums(subscription, page, safePageSize)
  startIndex = safePage * safePageSize
  for index, album in enumerate(reviewAlbums, start=startIndex + 1):
    albumId = str(album.get("albumId", album.get("album_id", "")) or "").strip()
    if not albumId:
      continue
    row: list[dict[str, str]] = []
    for label, action in (("下载", "download"), ("完成", "mark_completed"), ("忽略", "ignore"), ("已导入", "mark_imported")):
      callbackPage = safePage if safePage > 0 else None
      callbackData = buildSubscriptionAlbumCallbackData(subscriptionId, albumId, action, callbackPage)
      if len(callbackData.encode("utf-8")) > 64 and callbackPage is not None:
        callbackData = buildSubscriptionAlbumCallbackData(subscriptionId, albumId, action)
      if len(callbackData.encode("utf-8")) <= 64:
        row.append({"text": f"{index} {label}", "callback_data": callbackData})
    if row:
      rows.append(row)
  if pageCount > 1:
    pageRow: list[dict[str, str]] = []
    if safePage > 0:
      pageRow.append({
        "text": "上一页",
        "callback_data": buildSubscriptionReviewPageCallbackData(subscriptionId, safePage - 1),
      })
    if safePage < pageCount - 1:
      pageRow.append({
        "text": "下一页",
        "callback_data": buildSubscriptionReviewPageCallbackData(subscriptionId, safePage + 1),
      })
    rows.append(pageRow)
  if not rows:
    return None
  return {"inline_keyboard": rows}


def findSubscriptionByIdentity(
  subscriptions: list[dict[str, object]],
  subscriptionId: str = "",
  artistId: str = "",
) -> dict[str, object] | None:
  for subscription in subscriptions:
    if subscriptionId and str(subscription.get("id", "") or "") == subscriptionId:
      return subscription
    if artistId and str(subscription.get("artistId", "") or "") == artistId:
      return subscription
  return None


def findAlbumInSubscription(subscription: dict[str, object] | None, albumId: str) -> dict[str, object] | None:
  if subscription is None:
    return None
  albums = subscription.get("recentAlbums", [])
  if not isinstance(albums, list):
    return None
  for album in albums:
    if isinstance(album, dict) and str(album.get("albumId", album.get("album_id", "")) or "") == albumId:
      return album
  return None


def resolveCreatedSubscriptionWithAlbums(
  payload: dict[str, object],
  fetchSubscriptions: Callable[[], list[dict[str, object]]],
) -> dict[str, object] | None:
  subscription = payload.get("subscription")
  if not isinstance(subscription, dict):
    return None
  subscriptionId = str(subscription.get("id", "") or "")
  artistId = str(subscription.get("artistId", "") or "")
  subscriptions = fetchSubscriptions()
  return findSubscriptionByIdentity(subscriptions, subscriptionId=subscriptionId, artistId=artistId)


def sendSubscriptionReviewMessage(
  chatId: int,
  replyToMessageId: int | None,
  subscription: dict[str, object],
  sendMessage: Callable[[int, str, int | None, dict[str, object] | None], None],
  page: int = 0,
) -> None:
  sendMessage(
    chatId,
    formatSubscriptionReviewMessage(subscription, page),
    replyToMessageId,
    buildSubscriptionReviewKeyboard(subscription, page),
  )


def formatSubscriptionCallbackResultMessage(
  action: str,
  album: dict[str, object] | None,
  payload: dict[str, object],
) -> str:
  title = getSubscriptionAlbumTitle(album or {})
  if action == "download":
    return f"已请求下载：{title}\n{formatSubscriptionAlbumActionMessage(payload)}"
  labels = {
    "ignore": "已忽略",
    "mark_imported": "已标记已导入",
    "mark_completed": "已确认完成",
    "pending": "已恢复待确认",
  }
  return f"{labels.get(action, '已更新')}：{title}"


def formatSubscriptionsMessage(subscriptions: list[dict[str, object]]) -> str:
  if not subscriptions:
    return "当前没有歌手订阅"
  lines = ["歌手订阅"]
  for item in subscriptions[:10]:
    artistName = str(item.get("artistName", "未知歌手"))
    artistId = str(item.get("artistId", ""))
    storefront = str(item.get("storefront", "")).upper()
    lastCheckedAt = str(item.get("lastCheckedAt", "") or "未扫描")
    albumCount = int(item.get("albumCount", 0) or 0)
    pendingAlbumCount = int(item.get("pendingAlbumCount", 0) or 0)
    ignoredAlbumCount = int(item.get("ignoredAlbumCount", 0) or 0)
    importedAlbumCount = int(item.get("importedAlbumCount", 0) or 0)
    policy = str(item.get("newAlbumPolicy", "") or "confirm")
    lines.append(f"- {artistName} {storefront} / artist_id: {artistId}")
    lines.append(f"  策略：{policy}，已发现 {albumCount} 个专辑，待确认 {pendingAlbumCount} 个，忽略 {ignoredAlbumCount} 个，已导入 {importedAlbumCount} 个，上次扫描：{lastCheckedAt}")
  return "\n".join(lines)


def parseSubscriptionPolicyCommand(text: str) -> tuple[str, str] | None:
  argument = getCommandArgument(text)
  parts = argument.split()
  if len(parts) != 2:
    return None
  artistId, policy = parts
  normalizedPolicy = policy.strip().lower()
  if normalizedPolicy not in {"confirm", "auto"}:
    return None
  return artistId.strip(), normalizedPolicy


def parseSubscriptionAlbumCommand(text: str) -> tuple[str, str, str] | None:
  argument = getCommandArgument(text)
  parts = argument.split()
  if len(parts) != 3:
    return None
  artistId, albumId, action = parts
  normalizedAction = action.strip().lower().replace("-", "_")
  if normalizedAction == "imported":
    normalizedAction = "mark_imported"
  if normalizedAction in {"completed", "complete"}:
    normalizedAction = "mark_completed"
  if normalizedAction not in {"download", "ignore", "mark_imported", "mark_completed", "pending"}:
    return None
  return artistId.strip(), albumId.strip(), normalizedAction


def formatSubscriptionPolicyMessage(payload: dict[str, object]) -> str:
  subscription = payload.get("subscription")
  if not isinstance(subscription, dict):
    return "订阅策略已更新"
  artistName = str(subscription.get("artistName", "未知歌手"))
  policy = str(subscription.get("newAlbumPolicy", "confirm"))
  return f"{artistName} 新专辑策略已更新为 {policy}"


def formatSubscriptionAlbumActionMessage(payload: dict[str, object]) -> str:
  action = str(payload.get("action", ""))
  updatedCount = int(payload.get("updatedCount", 0) or 0)
  if action == "download":
    return formatSubscriptionScanSummaryMessage(payload)
  labels = {
    "ignore": "已忽略",
    "mark_imported": "已标记导入",
    "mark_completed": "已确认完成",
    "pending": "已恢复待确认",
  }
  return f"{labels.get(action, '已更新')} {updatedCount} 个专辑"


def formatCommandErrorMessage(action: str, exc: Exception) -> str:
  message = str(exc).strip() or "未知错误"
  if isinstance(exc, error.HTTPError):
    message = f"HTTP {exc.code}"
    try:
      body = exc.read().decode("utf-8")
      parsed = json.loads(body)
      if isinstance(parsed, dict) and str(parsed.get("error", "")).strip():
        message = str(parsed["error"]).strip()
    except Exception:  # noqa: BLE001
      pass
  return f"{action}失败：{message}"


def isTelegramMessageNotModifiedError(exc: Exception) -> bool:
  message = str(exc).lower()
  if "message is not modified" in message:
    return True
  if not isinstance(exc, error.HTTPError):
    return False
  try:
    body = exc.read().decode("utf-8", errors="replace").lower()
  except Exception:  # noqa: BLE001
    return False
  return "message is not modified" in body


def buildTelegramCommands() -> list[dict[str, str]]:
  return [
    {"command": "start", "description": "显示帮助和菜单"},
    {"command": "menu", "description": "打开快捷菜单"},
    {"command": "hide_menu", "description": "收起快捷菜单"},
    {"command": "help", "description": "查看帮助"},
    {"command": "force", "description": "强制重新下载指定链接"},
    {"command": "artist_search", "description": "搜索可订阅歌手"},
    {"command": "subscribe", "description": "订阅歌手 URL"},
    {"command": "subscriptions", "description": "查看歌手订阅"},
    {"command": "unsubscribe", "description": "取消歌手订阅"},
    {"command": "scan_subscriptions", "description": "手动扫描歌手订阅"},
    {"command": "subscription_policy", "description": "切换歌手订阅策略"},
    {"command": "subscription_album", "description": "处理订阅专辑"},
    {"command": "retry_failed", "description": "重试失败任务"},
    {"command": "queue", "description": "查看当前队列"},
    {"command": "failed", "description": "查看失败任务"},
    {"command": "running", "description": "查看运行中任务"},
    {"command": "recent", "description": "查看最近结果"},
  ]


def setTelegramCommands(botToken: str) -> None:
  callTelegramApi(botToken, "setMyCommands", {"commands": buildTelegramCommands()})


def initializeTelegramBot(
  botToken: str,
  allowedChatId: int,
  setCommands: Callable[[str], None] | None = None,
  sendMessage: Callable[[int, str, int | None, dict[str, object] | None], None] | None = None,
) -> None:
  if setCommands is None:
    setCommands = setTelegramCommands
  if sendMessage is None:
    sendMessage = lambda chatId, text, replyToMessageId=None, replyMarkup=None: sendTelegramMessage(
      botToken,
      chatId,
      text,
      replyToMessageId,
      replyMarkup,
    )

  setCommands(botToken)
  sendMessage(allowedChatId, formatStartupWelcomeMessage(), None, None)


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


def isBotAuthoredMessage(message: dict[str, object]) -> bool:
  sender = message.get("from")
  if isinstance(sender, dict) and bool(sender.get("is_bot")):
    return True
  return message.get("sender_chat") is not None


def handleSubscriptionCallbackQuery(
  callbackQuery: dict[str, object],
  allowedChatId: int,
  fetchSubscriptions: Callable[[], list[dict[str, object]]],
  updateSubscriptionAlbumBySubscriptionId: Callable[[str, str, str], dict[str, object]],
  sendMessage: Callable[[int, str, int | None, dict[str, object] | None], None],
  answerCallback: Callable[[str, str], None],
  editMessageReplyMarkup: Callable[[int, int, dict[str, object] | None], None],
  editMessageText: Callable[[int, int, str, dict[str, object] | None], None],
) -> None:
  def safeAnswerCallback(text: str) -> None:
    if not callbackId:
      return
    try:
      answerCallback(callbackId, text)
    except Exception as exc:  # noqa: BLE001
      logBotMessage(f"failed to answer subscription callback: {exc}")

  def safeSendMessage(text: str, replyMarkup: dict[str, object] | None = None) -> None:
    try:
      sendMessage(chatId, text, replyToMessageId, replyMarkup)
    except Exception as exc:  # noqa: BLE001
      logBotMessage(f"failed to send subscription callback message: {exc}")

  callbackId = str(callbackQuery.get("id", "") or "")
  fromUser = callbackQuery.get("from")
  userId = int(fromUser.get("id", 0)) if isinstance(fromUser, dict) else 0
  message = callbackQuery.get("message")
  chatId = userId
  replyToMessageId: int | None = None
  if isinstance(message, dict):
    chat = message.get("chat")
    if isinstance(chat, dict):
      if str(chat.get("type", "")) != "private":
        return
      chatId = int(chat.get("id", userId) or userId)
    replyToMessageId = int(message.get("message_id", 0) or 0) or None
  if not isAllowedChat(chatId, allowedChatId):
    return
  callbackData = str(callbackQuery.get("data", "") or "")
  parsedPage = parseSubscriptionReviewPageCallbackData(callbackData)
  if parsedPage is not None:
    subscriptionId, page = parsedPage
    try:
      subscription = findSubscriptionByIdentity(fetchSubscriptions(), subscriptionId=subscriptionId)
    except Exception as exc:  # noqa: BLE001
      safeAnswerCallback("翻页失败")
      safeSendMessage(formatCommandErrorMessage("刷新订阅分页", exc))
      return
    if subscription is None:
      safeAnswerCallback("订阅不存在")
      return
    safePage = clampSubscriptionReviewPage(subscription, page)
    safeAnswerCallback(f"第 {safePage + 1} 页")
    text = formatSubscriptionReviewMessage(subscription, safePage)
    replyMarkup = buildSubscriptionReviewKeyboard(subscription, safePage)
    if replyToMessageId is None:
      safeSendMessage(text, replyMarkup)
      return
    try:
      editMessageText(chatId, replyToMessageId, text, replyMarkup)
    except Exception as exc:  # noqa: BLE001
      if isTelegramMessageNotModifiedError(exc):
        logBotMessage("subscription review page already up to date")
        return
      logBotMessage(f"failed to edit subscription review page: {exc}")
      safeSendMessage(text, replyMarkup)
    return

  parsed = parseSubscriptionAlbumCallbackData(callbackData)
  if parsed is None:
    safeAnswerCallback("无法识别的订阅操作")
    return

  subscriptionId, albumId, action, sourcePage = parsed
  subscription: dict[str, object] | None = None
  album: dict[str, object] | None = None
  try:
    subscription = findSubscriptionByIdentity(fetchSubscriptions(), subscriptionId=subscriptionId)
    album = findAlbumInSubscription(subscription, albumId)
  except Exception:  # noqa: BLE001
    subscription = None
    album = None

  try:
    payload = updateSubscriptionAlbumBySubscriptionId(subscriptionId, albumId, action)
  except Exception as exc:  # noqa: BLE001
    safeAnswerCallback("处理失败")
    safeSendMessage(formatCommandErrorMessage("处理订阅专辑", exc))
    return

  safeAnswerCallback("已处理")
  if replyToMessageId is not None:
    try:
      editMessageReplyMarkup(chatId, replyToMessageId, None)
    except Exception as exc:  # noqa: BLE001
      logBotMessage(f"failed to clear subscription callback keyboard: {exc}")
  safeSendMessage(formatSubscriptionCallbackResultMessage(action, album, payload))
  try:
    refreshedSubscription = findSubscriptionByIdentity(fetchSubscriptions(), subscriptionId=subscriptionId)
  except Exception as exc:  # noqa: BLE001
    safeSendMessage(formatCommandErrorMessage("刷新订阅确认", exc))
    return
  if refreshedSubscription is not None:
    try:
      sendSubscriptionReviewMessage(chatId, replyToMessageId, refreshedSubscription, sendMessage, page=sourcePage or 0)
    except Exception as exc:  # noqa: BLE001
      logBotMessage(f"failed to send refreshed subscription review: {exc}")


def handleUpdate(
  update: dict[str, object],
  allowedChatId: int,
  store: TelegramTaskStore,
  createTask: Callable[..., dict[str, object]],
  retryTasks: Callable[[], dict[str, object]] | None = None,
  fetchTasks: Callable[[], list[dict[str, object]]] | None = None,
  searchArtists: Callable[[str], list[dict[str, object]]] | None = None,
  createSubscription: Callable[[str], dict[str, object]] | None = None,
  fetchSubscriptions: Callable[[], list[dict[str, object]]] | None = None,
  deleteSubscription: Callable[[str], dict[str, object]] | None = None,
  scanSubscriptions: Callable[[], dict[str, object]] | None = None,
  updateSubscriptionPolicy: Callable[[str, str], dict[str, object]] | None = None,
  updateSubscriptionAlbum: Callable[[str, str, str], dict[str, object]] | None = None,
  sendMessage: Callable[[int, str, int | None, dict[str, object] | None], None] | None = None,
  updateSubscriptionAlbumBySubscriptionId: Callable[[str, str, str], dict[str, object]] | None = None,
  answerCallback: Callable[[str, str], None] | None = None,
  editMessageReplyMarkup: Callable[[int, int, dict[str, object] | None], None] | None = None,
  editMessageText: Callable[[int, int, str, dict[str, object] | None], None] | None = None,
) -> None:
  if retryTasks is None:
    retryTasks = lambda: {}
  if fetchTasks is None:
    fetchTasks = lambda: []
  if searchArtists is None:
    searchArtists = lambda term: []
  if createSubscription is None:
    createSubscription = lambda artistUrl: {}
  if fetchSubscriptions is None:
    fetchSubscriptions = lambda: []
  if deleteSubscription is None:
    deleteSubscription = lambda artistId: {}
  if scanSubscriptions is None:
    scanSubscriptions = lambda: {}
  if updateSubscriptionPolicy is None:
    updateSubscriptionPolicy = lambda artistId, policy: {}
  if updateSubscriptionAlbum is None:
    updateSubscriptionAlbum = lambda artistId, albumId, action: {}
  if sendMessage is None:
    sendMessage = lambda chatId, text, replyToMessageId=None, replyMarkup=None: None
  if updateSubscriptionAlbumBySubscriptionId is None:
    updateSubscriptionAlbumBySubscriptionId = lambda subscriptionId, albumId, action: {}
  if answerCallback is None:
    answerCallback = lambda callbackQueryId, text="": None
  if editMessageReplyMarkup is None:
    editMessageReplyMarkup = lambda chatId, messageId, replyMarkup=None: None
  if editMessageText is None:
    editMessageText = lambda chatId, messageId, text, replyMarkup=None: None

  callbackQuery = update.get("callback_query")
  if isinstance(callbackQuery, dict):
    handleSubscriptionCallbackQuery(
      callbackQuery=callbackQuery,
      allowedChatId=allowedChatId,
      fetchSubscriptions=fetchSubscriptions,
      updateSubscriptionAlbumBySubscriptionId=updateSubscriptionAlbumBySubscriptionId,
      sendMessage=sendMessage,
      answerCallback=answerCallback,
      editMessageReplyMarkup=editMessageReplyMarkup,
      editMessageText=editMessageText,
    )
    return

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
  if isBotAuthoredMessage(message):
    updateId = int(update.get("update_id", 0) or 0)
    logBotMessage(f"bot-authored message ignored: {updateId}")
    return
  text = str(message.get("text", ""))
  messageId = int(message.get("message_id", 0))
  command = normalizeCommand(text)
  forceRequested = command == "force"
  keyboardActionReplyMarkup = buildRemoveKeyboard() if isKeyboardCommand(text) else None
  if text == HIDE_MENU_BUTTON_TEXT or command == "hide_menu":
    sendMessage(chatId, "快捷菜单已收起。发送 /menu 可重新打开。", messageId, buildRemoveKeyboard())
    return
  if text == MENU_BUTTON_TEXT or command == "menu":
    sendMessage(chatId, "快捷菜单已打开，用完后会自动收起。", messageId, buildReplyKeyboard())
    return
  if text == FORCE_DOWNLOAD_HINT_BUTTON_TEXT:
    sendMessage(chatId, formatForceDownloadHelpMessage(), messageId, keyboardActionReplyMarkup)
    return
  if text == SUBSCRIPTION_HINT_BUTTON_TEXT:
    sendMessage(chatId, formatSubscriptionHelpMessage(), messageId, buildRemoveKeyboard())
    return
  if command == "start":
    sendMessage(chatId, formatHelpMessage(), messageId, buildReplyKeyboard())
    return
  if text in {HELP_BUTTON_TEXT, DOWNLOAD_HINT_BUTTON_TEXT} or command == "help":
    sendMessage(chatId, formatHelpMessage(), messageId, keyboardActionReplyMarkup)
    return
  if command == "artist_search":
    term = getCommandArgument(text)
    if not term:
      sendMessage(chatId, "用法：/artist_search 歌手名", messageId)
      return
    try:
      searchResults = searchArtists(term)
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("搜索歌手", exc), messageId)
      return
    sendMessage(chatId, formatArtistSearchResultsMessage(searchResults), messageId)
    return
  if command == "subscribe":
    artistUrl = getCommandArgument(text)
    if not artistUrl:
      sendMessage(chatId, "用法：/subscribe https://music.apple.com/.../artist/...", messageId)
      return
    try:
      subscriptionPayload = createSubscription(artistUrl)
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("订阅歌手", exc), messageId)
      return
    sendMessage(chatId, formatSubscriptionCreatedMessage(subscriptionPayload), messageId)
    try:
      reviewedSubscription = resolveCreatedSubscriptionWithAlbums(subscriptionPayload, fetchSubscriptions)
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("获取订阅专辑", exc), messageId)
      return
    if reviewedSubscription is not None:
      sendSubscriptionReviewMessage(chatId, messageId, reviewedSubscription, sendMessage)
    return
  if text == SUBSCRIPTIONS_BUTTON_TEXT or command == "subscriptions":
    try:
      subscriptions = fetchSubscriptions()
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("获取订阅列表", exc), messageId, keyboardActionReplyMarkup)
      return
    sendMessage(chatId, formatSubscriptionsMessage(subscriptions), messageId, keyboardActionReplyMarkup)
    return
  if command == "unsubscribe":
    artistId = getCommandArgument(text)
    if not artistId:
      sendMessage(chatId, "用法：/unsubscribe <artist_id>", messageId)
      return
    try:
      deleteSubscription(artistId)
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("取消订阅", exc), messageId)
      return
    sendMessage(chatId, f"已取消订阅 artist_id: {artistId}", messageId)
    return
  if text == SCAN_SUBSCRIPTIONS_BUTTON_TEXT or command == "scan_subscriptions":
    try:
      scanPayload = scanSubscriptions()
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("扫描订阅", exc), messageId, keyboardActionReplyMarkup)
      return
    sendMessage(chatId, formatSubscriptionScanSummaryMessage(scanPayload), messageId, keyboardActionReplyMarkup)
    return
  if command == "subscription_policy":
    parsedPolicyCommand = parseSubscriptionPolicyCommand(text)
    if parsedPolicyCommand is None:
      sendMessage(chatId, "用法：/subscription_policy <artist_id> confirm|auto", messageId)
      return
    artistId, policy = parsedPolicyCommand
    try:
      policyPayload = updateSubscriptionPolicy(artistId, policy)
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("更新订阅策略", exc), messageId)
      return
    sendMessage(chatId, formatSubscriptionPolicyMessage(policyPayload), messageId)
    return
  if command == "subscription_album":
    parsedAlbumCommand = parseSubscriptionAlbumCommand(text)
    if parsedAlbumCommand is None:
      sendMessage(chatId, "用法：/subscription_album <artist_id> <album_id> download|ignore|imported|completed|pending", messageId)
      return
    artistId, albumId, action = parsedAlbumCommand
    try:
      albumPayload = updateSubscriptionAlbum(artistId, albumId, action)
    except Exception as exc:  # noqa: BLE001
      sendMessage(chatId, formatCommandErrorMessage("处理订阅专辑", exc), messageId)
      return
    sendMessage(chatId, formatSubscriptionAlbumActionMessage(albumPayload), messageId)
    return
  urls = extractAppleMusicUrls(text)
  if forceRequested and not urls:
    sendMessage(chatId, formatForceDownloadHelpMessage(), messageId)
    return
  if text == RETRY_FAILED_BUTTON_TEXT or command == "retry_failed":
    sendMessage(chatId, formatRetryFailedTasksMessage(retryTasks()), messageId, keyboardActionReplyMarkup)
    return
  tasks = fetchTasks() if text in {
    QUEUE_BUTTON_TEXT,
    FAILED_TASKS_BUTTON_TEXT,
    RUNNING_TASKS_BUTTON_TEXT,
    RECENT_RESULTS_BUTTON_TEXT,
  } or command in {"queue", "failed", "running", "recent"} else []
  if text == QUEUE_BUTTON_TEXT or command == "queue":
    sendMessage(chatId, formatTaskListMessage(tasks), messageId, keyboardActionReplyMarkup)
    return
  if text == FAILED_TASKS_BUTTON_TEXT or command == "failed":
    sendMessage(chatId, formatTaskListMessage(filterTasksByStatus(tasks, "failed"), "失败任务"), messageId, keyboardActionReplyMarkup)
    return
  if text == RUNNING_TASKS_BUTTON_TEXT or command == "running":
    sendMessage(chatId, formatTaskListMessage(filterTasksByStatus(tasks, "running"), "运行中任务"), messageId, keyboardActionReplyMarkup)
    return
  if text == RECENT_RESULTS_BUTTON_TEXT or command == "recent":
    sendMessage(chatId, formatTaskListMessage(filterTasksByStatus(tasks, "completed"), "最近结果"), messageId, keyboardActionReplyMarkup)
    return
  if not urls:
    return
  if len(urls) > 1:
    summary = submitAppleMusicUrls(
      store=store,
      createTask=createTask,
      chatId=chatId,
      messageId=messageId,
      urls=urls,
      force=forceRequested,
    )
    sendMessage(chatId, formatBatchAcceptedMessage(summary), messageId)
    return

  url = urls[0]
  response = createTaskWithOptionalForce(createTask, url, forceRequested)
  logBotMessage(f"accepted url from chat {chatId}: {url}")
  taskId = str(response.get("taskId", "")).strip()
  status = str(response.get("status", "")).strip()
  if shouldTrackTask(taskId, status):
    sendMessage(chatId, formatAcceptedMessage(url, status), messageId)
    logBotMessage(f"tracking task {taskId} for chat {chatId}")
    trackPendingTask(store, taskId, chatId, messageId, url)
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
    try:
      payload = fetchTask(taskId)
    except error.HTTPError as exc:
      if exc.code == 404:
        logBotMessage(f"stale pending task removed: {taskId}")
        store.deletePendingTask(taskId)
        continue
      raise
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
  storePath = getConfigValue(resolvedConfigPath, "telegram-store-path") or "data/telegram_tasks.db"
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


def callListApi(url: str) -> list[dict[str, object]]:
  httpRequest = request.Request(url=url, method="GET")
  with request.urlopen(httpRequest, timeout=60) as response:
    body = response.read().decode("utf-8")
  parsed = json.loads(body)
  if not isinstance(parsed, list):
    raise ValueError(f"Expected list response from {url}")
  return [item for item in parsed if isinstance(item, dict)]


def createDownloadTask(webappBaseUrl: str, url: str, force: bool = False) -> dict[str, object]:
  return callJsonApi(
    f"{webappBaseUrl}/api/downloads",
    method="POST",
    payload={"url": url, "force": force, "source": "telegram"},
  )


def fetchTaskStatus(webappBaseUrl: str, taskId: str) -> dict[str, object]:
  return callJsonApi(f"{webappBaseUrl}/api/tasks/{taskId}")


def retryFailedTasks(webappBaseUrl: str) -> dict[str, object]:
  return callJsonApi(f"{webappBaseUrl}/api/tasks/retry-failed", method="POST")


def listTasks(webappBaseUrl: str) -> list[dict[str, object]]:
  return callListApi(f"{webappBaseUrl}/api/tasks")


def searchArtists(webappBaseUrl: str, term: str) -> list[dict[str, object]]:
  payload = callJsonApi(
    f"{webappBaseUrl}/api/subscriptions/search",
    method="POST",
    payload={"term": term},
  )
  results = payload.get("results", [])
  if not isinstance(results, list):
    return []
  return [item for item in results if isinstance(item, dict)]


def createArtistSubscription(webappBaseUrl: str, artistUrl: str) -> dict[str, object]:
  return callJsonApi(
    f"{webappBaseUrl}/api/subscriptions",
    method="POST",
    payload={"artistUrl": artistUrl},
  )


def listArtistSubscriptions(webappBaseUrl: str) -> list[dict[str, object]]:
  return callListApi(f"{webappBaseUrl}/api/subscriptions")


def findArtistSubscriptionByArtistId(webappBaseUrl: str, artistId: str) -> dict[str, object]:
  for subscription in listArtistSubscriptions(webappBaseUrl):
    if str(subscription.get("artistId", "")) == str(artistId):
      return subscription
  raise ValueError("subscription not found")


def deleteArtistSubscription(webappBaseUrl: str, artistId: str) -> dict[str, object]:
  quotedArtistId = parse.quote(artistId, safe="")
  return callJsonApi(
    f"{webappBaseUrl}/api/subscriptions/by-artist/{quotedArtistId}",
    method="DELETE",
  )


def scanArtistSubscriptions(webappBaseUrl: str) -> dict[str, object]:
  return callJsonApi(f"{webappBaseUrl}/api/subscriptions/scan", method="POST")


def updateArtistSubscriptionPolicy(webappBaseUrl: str, artistId: str, newAlbumPolicy: str) -> dict[str, object]:
  subscription = findArtistSubscriptionByArtistId(webappBaseUrl, artistId)
  subscriptionId = str(subscription.get("id", ""))
  if not subscriptionId:
    raise ValueError("subscription not found")
  return callJsonApi(
    f"{webappBaseUrl}/api/subscriptions/{parse.quote(subscriptionId, safe='')}",
    method="PATCH",
    payload={"newAlbumPolicy": newAlbumPolicy},
  )


def updateArtistSubscriptionAlbum(webappBaseUrl: str, artistId: str, albumId: str, action: str) -> dict[str, object]:
  subscription = findArtistSubscriptionByArtistId(webappBaseUrl, artistId)
  subscriptionId = str(subscription.get("id", ""))
  if not subscriptionId:
    raise ValueError("subscription not found")
  return callJsonApi(
    f"{webappBaseUrl}/api/subscriptions/{parse.quote(subscriptionId, safe='')}/albums/actions",
    method="POST",
    payload={"albumIds": [albumId], "action": action},
  )


def updateArtistSubscriptionAlbumBySubscriptionId(webappBaseUrl: str, subscriptionId: str, albumId: str, action: str) -> dict[str, object]:
  if not str(subscriptionId).strip():
    raise ValueError("subscription not found")
  return callJsonApi(
    f"{webappBaseUrl}/api/subscriptions/{parse.quote(str(subscriptionId), safe='')}/albums/actions",
    method="POST",
    payload={"albumIds": [albumId], "action": action},
  )


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


def sendTelegramMessage(
  botToken: str,
  chatId: int,
  text: str,
  replyToMessageId: int | None = None,
  replyMarkup: dict[str, object] | None = None,
) -> None:
  payload: dict[str, object] = {"chat_id": chatId, "text": text}
  if replyToMessageId is not None:
    payload["reply_to_message_id"] = replyToMessageId
  if replyMarkup is not None:
    payload["reply_markup"] = replyMarkup
  callTelegramApi(botToken, "sendMessage", payload)


def answerTelegramCallbackQuery(botToken: str, callbackQueryId: str, text: str = "") -> None:
  payload: dict[str, object] = {"callback_query_id": callbackQueryId}
  if text:
    payload["text"] = text
  callTelegramApi(botToken, "answerCallbackQuery", payload)


def editTelegramMessageReplyMarkup(
  botToken: str,
  chatId: int,
  messageId: int,
  replyMarkup: dict[str, object] | None = None,
) -> None:
  payload: dict[str, object] = {
    "chat_id": chatId,
    "message_id": messageId,
  }
  if replyMarkup is not None:
    payload["reply_markup"] = replyMarkup
  callTelegramApi(botToken, "editMessageReplyMarkup", payload)


def editTelegramMessageText(
  botToken: str,
  chatId: int,
  messageId: int,
  text: str,
  replyMarkup: dict[str, object] | None = None,
) -> None:
  payload: dict[str, object] = {
    "chat_id": chatId,
    "message_id": messageId,
    "text": text,
  }
  if replyMarkup is not None:
    payload["reply_markup"] = replyMarkup
  callTelegramApi(botToken, "editMessageText", payload)


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
  createTask: Callable[..., dict[str, object]],
  fetchTask: Callable[[str], dict[str, object]],
  retryTasks: Callable[[], dict[str, object]] | None = None,
  fetchTasks: Callable[[], list[dict[str, object]]] | None = None,
  searchArtists: Callable[[str], list[dict[str, object]]] | None = None,
  createSubscription: Callable[[str], dict[str, object]] | None = None,
  fetchSubscriptions: Callable[[], list[dict[str, object]]] | None = None,
  deleteSubscription: Callable[[str], dict[str, object]] | None = None,
  scanSubscriptions: Callable[[], dict[str, object]] | None = None,
  updateSubscriptionPolicy: Callable[[str, str], dict[str, object]] | None = None,
  updateSubscriptionAlbum: Callable[[str, str, str], dict[str, object]] | None = None,
  sendMessage: Callable[[int, str, int | None, dict[str, object] | None], None] | None = None,
  updateSubscriptionAlbumBySubscriptionId: Callable[[str, str, str], dict[str, object]] | None = None,
  answerCallback: Callable[[str, str], None] | None = None,
  editMessageReplyMarkup: Callable[[int, int, dict[str, object] | None], None] | None = None,
  editMessageText: Callable[[int, int, str, dict[str, object] | None], None] | None = None,
) -> int | None:
  if retryTasks is None:
    retryTasks = lambda: {}
  if fetchTasks is None:
    fetchTasks = lambda: []
  if sendMessage is None:
    sendMessage = lambda chatId, text, replyToMessageId=None, replyMarkup=None: None
  if updateSubscriptionAlbumBySubscriptionId is None:
    updateSubscriptionAlbumBySubscriptionId = lambda subscriptionId, albumId, action: {}
  if answerCallback is None:
    answerCallback = lambda callbackQueryId, text="": None
  if editMessageReplyMarkup is None:
    editMessageReplyMarkup = lambda chatId, messageId, replyMarkup=None: None
  if editMessageText is None:
    editMessageText = lambda chatId, messageId, text, replyMarkup=None: None

  nextOffset = offset
  updates = getUpdatesFn(offset, updatesTimeoutSeconds)
  for update in updates:
    updateId = int(update.get("update_id", 0))
    if store.hasProcessedUpdate(updateId):
      logBotMessage(f"duplicate update skipped: {updateId}")
      store.setLastUpdateId(updateId)
      nextOffset = updateId + 1
      continue
    handleUpdate(
      update=update,
      allowedChatId=allowedChatId,
      store=store,
      createTask=createTask,
      retryTasks=retryTasks,
      fetchTasks=fetchTasks,
      searchArtists=searchArtists,
      createSubscription=createSubscription,
      fetchSubscriptions=fetchSubscriptions,
      deleteSubscription=deleteSubscription,
      scanSubscriptions=scanSubscriptions,
      updateSubscriptionPolicy=updateSubscriptionPolicy,
      updateSubscriptionAlbum=updateSubscriptionAlbum,
      sendMessage=sendMessage,
      updateSubscriptionAlbumBySubscriptionId=updateSubscriptionAlbumBySubscriptionId,
      answerCallback=answerCallback,
      editMessageReplyMarkup=editMessageReplyMarkup,
      editMessageText=editMessageText,
    )
    store.markUpdateProcessed(updateId)
    store.pruneProcessedUpdates()
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
  try:
    initializeTelegramBot(config.botToken, config.allowedChatId)
  except Exception as exc:  # noqa: BLE001
    logBotMessage(f"failed to initialize bot: {exc}")
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
        createTask=lambda url, force=False: createDownloadTask(config.webappBaseUrl, url, force),
        fetchTask=lambda taskId: fetchTaskStatus(config.webappBaseUrl, taskId),
        retryTasks=lambda: retryFailedTasks(config.webappBaseUrl),
        fetchTasks=lambda: listTasks(config.webappBaseUrl),
        searchArtists=lambda term: searchArtists(config.webappBaseUrl, term),
        createSubscription=lambda artistUrl: createArtistSubscription(config.webappBaseUrl, artistUrl),
        fetchSubscriptions=lambda: listArtistSubscriptions(config.webappBaseUrl),
        deleteSubscription=lambda artistId: deleteArtistSubscription(config.webappBaseUrl, artistId),
        scanSubscriptions=lambda: scanArtistSubscriptions(config.webappBaseUrl),
        updateSubscriptionPolicy=lambda artistId, policy: updateArtistSubscriptionPolicy(config.webappBaseUrl, artistId, policy),
        updateSubscriptionAlbum=lambda artistId, albumId, action: updateArtistSubscriptionAlbum(config.webappBaseUrl, artistId, albumId, action),
        updateSubscriptionAlbumBySubscriptionId=lambda subscriptionId, albumId, action: updateArtistSubscriptionAlbumBySubscriptionId(config.webappBaseUrl, subscriptionId, albumId, action),
        answerCallback=lambda callbackQueryId, text="": answerTelegramCallbackQuery(config.botToken, callbackQueryId, text),
        editMessageReplyMarkup=lambda chatId, messageId, replyMarkup=None: editTelegramMessageReplyMarkup(config.botToken, chatId, messageId, replyMarkup),
        editMessageText=lambda chatId, messageId, text, replyMarkup=None: editTelegramMessageText(config.botToken, chatId, messageId, text, replyMarkup),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: sendTelegramMessage(
          config.botToken,
          chatId,
          text,
          replyToMessageId,
          replyMarkup,
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
