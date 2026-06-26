import difflib
import hashlib
import inspect
import json
import mimetypes
import re
import sqlite3
import subprocess
import threading
import time
import unicodedata
import uuid
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from flask import Flask, Response, jsonify, render_template, request, send_file

from webapp.apple_music import AppleMusicAlbum, AppleMusicArtist, AppleMusicClient, normalizeStorefront, parseAlbumIdFromUrl, parseArtistUrl
from webapp.config_loader import getConfigValue, resolveConfigPath


APPLE_MUSIC_URL_RE = re.compile(r"^https://music\.apple\.com/[a-z]{2}/")
DECRYPT_PROGRESS_RE = re.compile(r"Decrypting\.\.\.\s+(\d+)%")
DOWNLOAD_SUMMARY_RE = re.compile(r"Completed:\s+(\d+)/(\d+).*Errors:\s+(\d+)")
TRAILING_URL_PUNCTUATION = ".,;:!)]}>，。；：！）】》、"
APPLE_MUSIC_ARTWORK_SIZE_SEGMENT_RE = re.compile(r"/\d+x\d+bb(?:-[^/.]+)?\.[a-z0-9]+$", re.IGNORECASE)
NULL_FEATURE_RE = re.compile(r"[\[(]\s*(?:feat|ft)\.?\s*<null>\s*[\])]", re.IGNORECASE)
DEFAULT_COMPLETED_ROOT = Path("/downloads/completed")
DOWNLOAD_FORMAT_DIRS = {"ALAC", "AAC", "ATMOS", "Atmos"}
SUBSCRIPTION_SCAN_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_SUBSCRIPTION_STOREFRONT = "cn"
INTERACTIVE_RETRY_PROMPT = "Error detected, press Enter to try again..."
RETRY_PROMPT_LINES = {
  INTERACTIVE_RETRY_PROMPT,
  "Start trying again...",
}
FAILURE_REASON_MARKERS = (
  "Failed to run v2:",
  "Failed to rip album:",
  "Failed to dl ",
  "Failed ",
  "Error:",
  "Error ",
)
GENERIC_FAILURE_REASONS = {
  "download finished without any result",
  "download requested interactive retry after errors",
}
SUBSCRIPTION_POLICY_CONFIRM = "confirm"
SUBSCRIPTION_POLICY_AUTO = "auto"
SUBSCRIPTION_POLICIES = {SUBSCRIPTION_POLICY_CONFIRM, SUBSCRIPTION_POLICY_AUTO}
ALBUM_USER_STATE_PENDING = "pending"
ALBUM_USER_STATE_SUBSCRIBED = "subscribed"
ALBUM_USER_STATE_IGNORED = "ignored"
ALBUM_USER_STATE_IMPORTED = "imported"
ALBUM_USER_STATES = {
  ALBUM_USER_STATE_PENDING,
  ALBUM_USER_STATE_SUBSCRIBED,
  ALBUM_USER_STATE_IGNORED,
  ALBUM_USER_STATE_IMPORTED,
}
ALBUM_DETECTED_ACTIVE = {"queued", "running"}
ALBUM_DETECTED_NEEDS_CONFIRM = {"missing", "failed_history", "stale_history"}
ALBUM_DETECTED_STATUSES = ALBUM_DETECTED_ACTIVE | ALBUM_DETECTED_NEEDS_CONFIRM | {"completed", "seen"}
ARTWORK_CACHE_TIMEOUT_SECONDS = 15
ARTWORK_CACHE_MAX_BYTES = 5 * 1024 * 1024
ARTWORK_HOST_SUFFIX = "mzstatic.com"
ARTWORK_CONTENT_TYPE_EXTENSIONS = {
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/png": ".png",
  "image/webp": ".webp",
  "image/gif": ".gif",
  "image/avif": ".avif",
}
ARTWORK_EXTENSION_CONTENT_TYPES = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".avif": "image/avif",
}
DEFAULT_WRAPPER_RECOVERY_CONTAINER_NAME = "wrapper"
DEFAULT_WRAPPER_RECOVERY_TIMEOUT_SECONDS = 60.0
DEFAULT_WRAPPER_RECOVERY_COOLDOWN_SECONDS = 120.0
DEFAULT_WRAPPER_RECOVERY_MAX_RETRIES_PER_TASK = 1


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
  wrapperRecoveryAttempts: int = 0
  createdAt: float = field(default_factory=time.time)
  status: str = "pending"
  stage: str = "pending"
  progress: int = 0
  logs: list[str] = field(default_factory=list)
  result: list[dict[str, str]] = field(default_factory=list)
  error: str = ""
  failureReasonCandidate: str = ""
  events: list[TaskEvent] = field(default_factory=list)
  condition: threading.Condition = field(default_factory=threading.Condition)

  def toDict(self) -> dict[str, object]:
    return {
      "taskId": self.id,
      "url": self.url,
      "codec": self.codec,
      "source": self.source,
      "wrapperRecoveryAttempts": self.wrapperRecoveryAttempts,
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


@dataclass
class SubscriptionScanSummary:
  subscriptionId: int
  artistId: str
  artistName: str
  foundCount: int = 0
  queuedCount: int = 0
  pendingCount: int = 0
  skippedCompletedCount: int = 0
  skippedActiveCount: int = 0
  skippedIgnoredCount: int = 0
  skippedImportedCount: int = 0
  errorCount: int = 0
  queuedTaskIds: list[str] = field(default_factory=list)
  errors: list[str] = field(default_factory=list)

  def toDict(self) -> dict[str, object]:
    return {
      "subscriptionId": self.subscriptionId,
      "artistId": self.artistId,
      "artistName": self.artistName,
      "foundCount": self.foundCount,
      "queuedCount": self.queuedCount,
      "pendingCount": self.pendingCount,
      "skippedCompletedCount": self.skippedCompletedCount,
      "skippedActiveCount": self.skippedActiveCount,
      "skippedIgnoredCount": self.skippedIgnoredCount,
      "skippedImportedCount": self.skippedImportedCount,
      "errorCount": self.errorCount,
      "queuedTaskIds": list(self.queuedTaskIds),
      "errors": list(self.errors),
    }


def normalizeSubscriptionPolicy(value: object, default: str = SUBSCRIPTION_POLICY_CONFIRM) -> str:
  normalized = str(value or "").strip().lower()
  return normalized if normalized in SUBSCRIPTION_POLICIES else default


def normalizeAlbumUserState(value: object, default: str = ALBUM_USER_STATE_PENDING) -> str:
  normalized = str(value or "").strip().lower()
  return normalized if normalized in ALBUM_USER_STATES else default


def normalizeDetectedStatus(value: object, default: str = "seen") -> str:
  normalized = str(value or "").strip().lower()
  return normalized if normalized in ALBUM_DETECTED_STATUSES else default


def normalizeStrictStorefront(value: object, label: str = "storefront") -> str:
  storefront = str(value or "").strip().lower()
  if re.fullmatch(r"[a-z]{2}", storefront) is None:
    raise ValueError(f"{label} must be a two-letter Apple Music storefront code")
  return storefront


def getConfiguredSubscriptionStorefront(configPath: Path | None = None) -> str:
  resolvedConfigPath = configPath or resolveConfigPath()
  configured = getConfigValue(resolvedConfigPath, "subscription-storefront") or DEFAULT_SUBSCRIPTION_STOREFRONT
  return normalizeStrictStorefront(configured, "subscription-storefront")


def detectedStatusFromHistoryRecord(record: dict[str, str] | None) -> str:
  if record is None:
    return "missing"
  if hasUsableCompletedRecord(record):
    return "completed"
  status = str(record.get("status", "")).strip().lower()
  if status == "failed":
    return "failed_history"
  if status in {"queued", "running", "completed"}:
    return "stale_history"
  return "missing"


def rowStatusFromDetectedStatus(detectedStatus: str) -> str:
  normalized = normalizeDetectedStatus(detectedStatus, "seen")
  if normalized in {"queued", "running", "completed"}:
    return normalized
  if normalized in {"failed_history", "stale_history"}:
    return "failed"
  return "seen"


def detectedStatusFromRowStatus(status: str) -> str:
  normalized = str(status or "").strip().lower()
  if normalized in {"queued", "running", "completed"}:
    return normalized
  if normalized == "failed":
    return "failed_history"
  if normalized == "seen":
    return "seen"
  return normalizeDetectedStatus(normalized, "seen")


def canDownloadSubscriptionAlbum(userState: str, detectedStatus: str) -> bool:
  return normalizeAlbumUserState(userState) not in {ALBUM_USER_STATE_IGNORED, ALBUM_USER_STATE_IMPORTED} and (
    normalizeDetectedStatus(detectedStatus, "missing") in ALBUM_DETECTED_NEEDS_CONFIRM
  )


def getSeenAlbumMergePriority(row: sqlite3.Row) -> int:
  detectedStatus = normalizeDetectedStatus(row["detected_status"], detectedStatusFromRowStatus(row["status"]))
  userState = normalizeAlbumUserState(row["user_state"], ALBUM_USER_STATE_PENDING)
  status = str(row["status"] or "").strip().lower()
  if detectedStatus == "completed" or status == "completed":
    return 60
  if userState == ALBUM_USER_STATE_IMPORTED:
    return 50
  if userState == ALBUM_USER_STATE_IGNORED:
    return 40
  if detectedStatus in ALBUM_DETECTED_ACTIVE or status in ALBUM_DETECTED_ACTIVE:
    return 30
  if detectedStatus in {"failed_history", "stale_history"} or status == "failed":
    return 20
  return 10


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
          album_id TEXT NOT NULL DEFAULT '',
          task_id TEXT NOT NULL,
          status TEXT NOT NULL,
          codec TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'web',
          result_json TEXT NOT NULL DEFAULT '[]',
          error TEXT NOT NULL DEFAULT '',
          ever_completed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
      )
      try:
        connection.execute(
          "ALTER TABLE downloads ADD COLUMN ever_completed INTEGER NOT NULL DEFAULT 0"
        )
      except sqlite3.OperationalError:
        pass
      try:
        connection.execute(
          "ALTER TABLE downloads ADD COLUMN source TEXT NOT NULL DEFAULT 'web'"
        )
      except sqlite3.OperationalError:
        pass
      try:
        connection.execute(
          "ALTER TABLE downloads ADD COLUMN album_id TEXT NOT NULL DEFAULT ''"
        )
      except sqlite3.OperationalError:
        pass
      connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_downloads_album_id ON downloads(album_id) WHERE album_id != ''"
      )
      self._backfillAlbumIds(connection)
      connection.commit()

  def _backfillAlbumIds(self, connection: sqlite3.Connection) -> None:
    rows = connection.execute(
      "SELECT url, album_id FROM downloads"
    ).fetchall()
    for row in rows:
      albumId = extractAlbumIdFromUrl(str(row["url"]))
      currentAlbumId = str(row["album_id"] or "")
      if currentAlbumId == albumId:
        continue
      connection.execute(
        "UPDATE downloads SET album_id = ? WHERE url = ?",
        (albumId, row["url"]),
      )

  def getByUrl(self, url: str) -> dict[str, str] | None:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT url, album_id, task_id, status, codec, source, result_json, error, ever_completed, created_at, updated_at FROM downloads WHERE url = ?",
        (url,)
      ).fetchone()
    if row is None:
      return None
    return {key: str(row[key]) for key in row.keys()}

  def listByAlbumId(self, albumId: str) -> list[dict[str, str]]:
    if not albumId:
      return []
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT url, album_id, task_id, status, codec, source, result_json, error, ever_completed, created_at, updated_at
        FROM downloads
        WHERE album_id = ?
        ORDER BY
          CASE status WHEN 'completed' THEN 0 WHEN 'running' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
          updated_at DESC,
          rowid DESC
        """,
        (albumId,),
      ).fetchall()
    return [{key: str(row[key]) for key in row.keys()} for row in rows]

  def saveQueued(self, url: str, taskId: str, codec: str, source: str = "web", albumId: str | None = None) -> None:
    normalizedAlbumId = albumId if albumId is not None else extractAlbumIdFromUrl(url)
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO downloads (url, album_id, task_id, status, codec, source, result_json, error, created_at, updated_at)
        VALUES (?, ?, ?, 'queued', ?, ?, '[]', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          album_id = excluded.album_id,
          task_id = excluded.task_id,
          status = 'queued',
          codec = excluded.codec,
          source = excluded.source,
          result_json = '[]',
          error = '',
          updated_at = CURRENT_TIMESTAMP
        """,
        (url, normalizedAlbumId, taskId, codec, source)
      )
      connection.commit()

  def saveRunning(self, url: str, taskId: str, codec: str, source: str = "web", albumId: str | None = None) -> None:
    normalizedAlbumId = albumId if albumId is not None else extractAlbumIdFromUrl(url)
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO downloads (url, album_id, task_id, status, codec, source, result_json, error, created_at, updated_at)
        VALUES (?, ?, ?, 'running', ?, ?, '[]', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          album_id = excluded.album_id,
          task_id = excluded.task_id,
          status = 'running',
          codec = excluded.codec,
          source = excluded.source,
          result_json = '[]',
          error = '',
          updated_at = CURRENT_TIMESTAMP
        """,
        (url, normalizedAlbumId, taskId, codec, source)
      )
      connection.commit()

  def saveCompleted(self, url: str, taskId: str, codec: str, result: list[dict[str, str]], albumId: str | None = None) -> None:
    normalizedAlbumId = albumId if albumId is not None else extractAlbumIdFromUrl(url)
    with closing(self._connect()) as connection:
      connection.execute(
        """
        UPDATE downloads
        SET album_id = ?, task_id = ?, status = 'completed', codec = ?, result_json = ?, error = '', ever_completed = 1, updated_at = CURRENT_TIMESTAMP
        WHERE url = ?
        """,
        (normalizedAlbumId, taskId, codec, json.dumps(result, ensure_ascii=False), url)
      )
      connection.commit()

  def saveFailed(self, url: str, taskId: str, codec: str, error: str, source: str = "web", albumId: str | None = None) -> None:
    normalizedAlbumId = albumId if albumId is not None else extractAlbumIdFromUrl(url)
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO downloads (url, album_id, task_id, status, codec, source, result_json, error, created_at, updated_at)
        VALUES (?, ?, ?, 'failed', ?, ?, '[]', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          album_id = excluded.album_id,
          task_id = excluded.task_id,
          status = 'failed',
          codec = excluded.codec,
          source = excluded.source,
          error = excluded.error,
          updated_at = CURRENT_TIMESTAMP
        """,
        (url, normalizedAlbumId, taskId, codec, source, error)
      )
      connection.commit()

  def deleteFailedSubscriptionRecordsForStorefront(
    self,
    storefront: str,
    albumIds: list[str],
    urls: list[str],
  ) -> int:
    normalizedStorefront = normalizeStrictStorefront(storefront, "storefront")
    urlPrefix = f"https://music.apple.com/{normalizedStorefront}/%"
    deletedCount = 0
    with closing(self._connect()) as connection:
      for url in sorted({normalizeUrl(item) for item in urls if item}):
        cursor = connection.execute(
          "DELETE FROM downloads WHERE url = ? AND status = 'failed' AND source = 'subscription'",
          (url,),
        )
        deletedCount += cursor.rowcount
      for albumId in sorted({str(item or "").strip() for item in albumIds if str(item or "").strip()}):
        cursor = connection.execute(
          """
          DELETE FROM downloads
          WHERE album_id = ?
            AND status = 'failed'
            AND source = 'subscription'
            AND url LIKE ?
          """,
          (albumId, urlPrefix),
        )
        deletedCount += cursor.rowcount
      connection.commit()
    return deletedCount

  def saveCancelled(self, url: str, taskId: str, codec: str, source: str = "web", albumId: str | None = None) -> None:
    normalizedAlbumId = albumId if albumId is not None else extractAlbumIdFromUrl(url)
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO downloads (url, album_id, task_id, status, codec, source, result_json, error, created_at, updated_at)
        VALUES (?, ?, ?, 'cancelled', ?, ?, '[]', 'cancelled before start', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(url) DO UPDATE SET
          album_id = excluded.album_id,
          task_id = excluded.task_id,
          status = 'cancelled',
          codec = excluded.codec,
          source = excluded.source,
          result_json = '[]',
          error = excluded.error,
          updated_at = CURRENT_TIMESTAMP
        """,
        (url, normalizedAlbumId, taskId, codec, source)
      )
      connection.commit()

  def listAll(self) -> list[dict[str, str]]:
    with closing(self._connect()) as connection:
      rows = connection.execute(
        "SELECT url, album_id, task_id, status, codec, source, result_json, error, ever_completed, created_at, updated_at FROM downloads ORDER BY updated_at DESC"
      ).fetchall()
    return [{key: str(row[key]) for key in row.keys()} for row in rows]

  def listRecoverable(self) -> list[dict[str, str]]:
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT url, album_id, task_id, status, codec, source, result_json, error, ever_completed, created_at, updated_at
        FROM downloads
        WHERE status IN ('queued', 'running')
        ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, created_at ASC, updated_at ASC, rowid ASC
        """
      ).fetchall()
    return [{key: str(row[key]) for key in row.keys()} for row in rows]

  def hasEverCompleted(self, url: str) -> bool:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT 1 FROM downloads WHERE url = ? AND ever_completed = 1 LIMIT 1",
        (url,)
      ).fetchone()
    return row is not None


class ArtistSubscriptionStore:
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
        CREATE TABLE IF NOT EXISTS artist_subscriptions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          artist_id TEXT NOT NULL,
          storefront TEXT NOT NULL,
          artist_name TEXT NOT NULL,
          artist_url TEXT NOT NULL,
          artist_artwork_url TEXT NOT NULL DEFAULT '',
          enabled INTEGER NOT NULL DEFAULT 1,
          new_album_policy TEXT NOT NULL DEFAULT 'confirm',
          last_checked_at TEXT,
          last_error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(artist_id, storefront)
        )
        """
      )
      connection.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_seen_albums (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subscription_id INTEGER NOT NULL,
          album_id TEXT NOT NULL,
          album_url TEXT NOT NULL,
          album_name TEXT NOT NULL DEFAULT '',
          artwork_url TEXT NOT NULL DEFAULT '',
          release_date TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'seen',
          task_id TEXT NOT NULL DEFAULT '',
          user_state TEXT NOT NULL DEFAULT 'subscribed',
          detected_status TEXT NOT NULL DEFAULT 'seen',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(subscription_id, album_id)
        )
        """
      )
      try:
        connection.execute(
          "ALTER TABLE artist_subscriptions ADD COLUMN new_album_policy TEXT NOT NULL DEFAULT 'confirm'"
        )
      except sqlite3.OperationalError:
        pass
      try:
        connection.execute(
          "ALTER TABLE artist_subscriptions ADD COLUMN artist_artwork_url TEXT NOT NULL DEFAULT ''"
        )
      except sqlite3.OperationalError:
        pass
      try:
        connection.execute(
          "ALTER TABLE subscription_seen_albums ADD COLUMN user_state TEXT NOT NULL DEFAULT 'subscribed'"
        )
      except sqlite3.OperationalError:
        pass
      try:
        connection.execute(
          "ALTER TABLE subscription_seen_albums ADD COLUMN detected_status TEXT NOT NULL DEFAULT 'seen'"
        )
      except sqlite3.OperationalError:
        pass
      try:
        connection.execute(
          "ALTER TABLE subscription_seen_albums ADD COLUMN artwork_url TEXT NOT NULL DEFAULT ''"
        )
      except sqlite3.OperationalError:
        pass
      connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_artist_subscriptions_due ON artist_subscriptions(enabled, last_checked_at)"
      )
      connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_seen_albums_subscription ON subscription_seen_albums(subscription_id)"
      )
      connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_seen_albums_album_id ON subscription_seen_albums(album_id) WHERE album_id != ''"
      )
      connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_seen_albums_user_state ON subscription_seen_albums(user_state)"
      )
      connection.commit()

  def createOrEnable(self, artist: AppleMusicArtist) -> tuple[dict[str, object], bool]:
    with closing(self._connect()) as connection:
      existing = connection.execute(
        "SELECT id FROM artist_subscriptions WHERE artist_id = ? AND storefront = ?",
        (artist.artistId, artist.storefront),
      ).fetchone()
      created = existing is None
      connection.execute(
        """
        INSERT INTO artist_subscriptions (artist_id, storefront, artist_name, artist_url, artist_artwork_url, enabled, new_album_policy, last_error, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, 'confirm', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(artist_id, storefront) DO UPDATE SET
          artist_name = excluded.artist_name,
          artist_url = excluded.artist_url,
          artist_artwork_url = COALESCE(NULLIF(excluded.artist_artwork_url, ''), artist_subscriptions.artist_artwork_url),
          enabled = 1,
          last_error = '',
          updated_at = CURRENT_TIMESTAMP
        """,
        (artist.artistId, artist.storefront, artist.name, artist.url, artist.artworkUrl),
      )
      connection.commit()
      row = connection.execute(
        "SELECT * FROM artist_subscriptions WHERE artist_id = ? AND storefront = ?",
        (artist.artistId, artist.storefront),
      ).fetchone()
    assert row is not None
    return self._rowToSubscription(row), created

  def get(self, subscriptionId: int) -> dict[str, object] | None:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT * FROM artist_subscriptions WHERE id = ?",
        (subscriptionId,),
      ).fetchone()
    if row is None:
      return None
    return self._rowToSubscription(row)

  def getByArtistId(self, artistId: str) -> dict[str, object] | None:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT * FROM artist_subscriptions WHERE artist_id = ? ORDER BY id ASC LIMIT 1",
        (artistId,),
      ).fetchone()
    if row is None:
      return None
    return self._rowToSubscription(row)

  def listAll(self) -> list[dict[str, object]]:
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT
          s.*,
          COUNT(a.id) AS album_count,
          SUM(CASE WHEN a.user_state = 'pending' THEN 1 ELSE 0 END) AS pending_album_count,
          SUM(CASE WHEN a.detected_status IN ('queued', 'running') OR a.status IN ('queued', 'running') THEN 1 ELSE 0 END) AS active_album_count,
          SUM(CASE WHEN a.detected_status = 'completed' OR a.status = 'completed' THEN 1 ELSE 0 END) AS completed_album_count,
          SUM(CASE WHEN a.detected_status IN ('failed_history', 'stale_history') OR a.status = 'failed' THEN 1 ELSE 0 END) AS failed_album_count,
          SUM(CASE WHEN a.user_state = 'ignored' THEN 1 ELSE 0 END) AS ignored_album_count,
          SUM(CASE WHEN a.user_state = 'imported' THEN 1 ELSE 0 END) AS imported_album_count
        FROM artist_subscriptions s
        LEFT JOIN subscription_seen_albums a ON a.subscription_id = s.id
        GROUP BY s.id
        ORDER BY s.updated_at DESC, s.id DESC
        """
      ).fetchall()
    subscriptions = [self._rowToSubscription(row) for row in rows]
    for subscription in subscriptions:
      subscription["recentAlbums"] = self.listSeenAlbums(int(subscription["id"]))
    return subscriptions

  def listSeenAlbums(self, subscriptionId: int) -> list[dict[str, str]]:
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status, updated_at
        FROM subscription_seen_albums
        WHERE subscription_id = ?
        ORDER BY
          CASE WHEN release_date = '' THEN 1 ELSE 0 END,
          release_date DESC,
          updated_at DESC,
          id DESC
        """,
        (subscriptionId,),
      ).fetchall()
    return [self._rowToSeenAlbum(row) for row in rows]

  def getSeenAlbum(self, subscriptionId: int, albumId: str) -> dict[str, str] | None:
    if not albumId:
      return None
    with closing(self._connect()) as connection:
      row = connection.execute(
        """
        SELECT album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status, updated_at
        FROM subscription_seen_albums
        WHERE subscription_id = ? AND album_id = ?
        LIMIT 1
        """,
        (subscriptionId, albumId),
      ).fetchone()
    if row is None:
      return None
    return self._rowToSeenAlbum(row)

  def getSeenAlbumByAlbumId(self, albumId: str) -> dict[str, str] | None:
    if not albumId:
      return None
    with closing(self._connect()) as connection:
      row = connection.execute(
        """
        SELECT album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status, updated_at
        FROM subscription_seen_albums
        WHERE album_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (albumId,),
      ).fetchone()
    if row is None:
      return None
    return self._rowToSeenAlbum(row)

  def findEquivalentSeenAlbumForStorefront(
    self,
    subscriptionId: int,
    album: AppleMusicAlbum,
    storefront: str,
  ) -> dict[str, str] | None:
    normalizedStorefront = normalizeStorefront(storefront)
    targetUrlPrefix = f"https://music.apple.com/{normalizedStorefront}/%"
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status, updated_at
        FROM subscription_seen_albums
        WHERE subscription_id = ?
          AND album_id != ?
          AND album_url NOT LIKE ?
          AND (
            user_state IN ('imported', 'ignored')
            OR detected_status = 'completed'
            OR status = 'completed'
          )
        ORDER BY
          CASE
            WHEN detected_status = 'completed' OR status = 'completed' THEN 0
            WHEN user_state = 'imported' THEN 1
            WHEN user_state = 'ignored' THEN 2
            ELSE 3
          END,
          updated_at DESC,
          album_id ASC
        """,
        (subscriptionId, album.albumId, targetUrlPrefix),
      ).fetchall()
    for row in rows:
      seenAlbum = self._rowToSeenAlbum(row)
      if seenAlbumMatchesStorefrontAlbum(seenAlbum, album):
        return seenAlbum
    return None

  def listEnabled(self) -> list[dict[str, object]]:
    with closing(self._connect()) as connection:
      rows = connection.execute(
        "SELECT * FROM artist_subscriptions WHERE enabled = 1 ORDER BY id ASC"
      ).fetchall()
    return [self._rowToSubscription(row) for row in rows]

  def delete(self, subscriptionId: int) -> bool:
    with closing(self._connect()) as connection:
      row = connection.execute(
        "SELECT 1 FROM artist_subscriptions WHERE id = ?",
        (subscriptionId,),
      ).fetchone()
      if row is None:
        return False
      connection.execute(
        "DELETE FROM subscription_seen_albums WHERE subscription_id = ?",
        (subscriptionId,),
      )
      connection.execute(
        "DELETE FROM artist_subscriptions WHERE id = ?",
        (subscriptionId,),
      )
      connection.commit()
    return True

  def _mergeDuplicateSeenAlbumRows(
    self,
    connection: sqlite3.Connection,
    sourceRow: sqlite3.Row,
    targetRow: sqlite3.Row,
  ) -> None:
    if getSeenAlbumMergePriority(sourceRow) > getSeenAlbumMergePriority(targetRow):
      status = sourceRow["status"]
      taskId = sourceRow["task_id"]
      userState = sourceRow["user_state"]
      detectedStatus = sourceRow["detected_status"]
    else:
      status = targetRow["status"]
      taskId = targetRow["task_id"]
      userState = targetRow["user_state"]
      detectedStatus = targetRow["detected_status"]

    connection.execute(
      """
      UPDATE subscription_seen_albums
      SET
        album_url = COALESCE(NULLIF(album_url, ''), ?),
        album_name = COALESCE(NULLIF(album_name, ''), ?),
        artwork_url = COALESCE(NULLIF(artwork_url, ''), ?),
        release_date = COALESCE(NULLIF(release_date, ''), ?),
        status = ?,
        task_id = ?,
        user_state = ?,
        detected_status = ?,
        updated_at = CURRENT_TIMESTAMP
      WHERE id = ?
      """,
      (
        sourceRow["album_url"],
        sourceRow["album_name"],
        sourceRow["artwork_url"],
        sourceRow["release_date"],
        status,
        taskId,
        userState,
        detectedStatus,
        targetRow["id"],
      ),
    )

  def migrateSubscriptionsToStorefront(self, storefront: str) -> dict[str, int]:
    normalizedStorefront = normalizeStrictStorefront(storefront, "storefront")
    migratedCount = 0
    mergedCount = 0
    movedAlbumCount = 0
    deletedDuplicateAlbumCount = 0
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT id, artist_id, storefront, artist_name, artist_url
        FROM artist_subscriptions
        ORDER BY id ASC
        """
      ).fetchall()
      for row in rows:
        subscriptionId = int(row["id"])
        if str(row["storefront"]) == normalizedStorefront:
          continue
        targetRow = connection.execute(
          """
          SELECT id
          FROM artist_subscriptions
          WHERE artist_id = ? AND storefront = ?
          LIMIT 1
          """,
          (row["artist_id"], normalizedStorefront),
        ).fetchone()
        if targetRow is not None:
          targetSubscriptionId = int(targetRow["id"])
          seenRows = connection.execute(
            """
            SELECT id, album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status
            FROM subscription_seen_albums
            WHERE subscription_id = ?
            """,
            (subscriptionId,),
          ).fetchall()
          for seenRow in seenRows:
            duplicateRow = connection.execute(
              """
              SELECT id, album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status
              FROM subscription_seen_albums
              WHERE subscription_id = ? AND album_id = ?
              LIMIT 1
              """,
              (targetSubscriptionId, seenRow["album_id"]),
            ).fetchone()
            if duplicateRow is None:
              connection.execute(
                "UPDATE subscription_seen_albums SET subscription_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (targetSubscriptionId, seenRow["id"]),
              )
              movedAlbumCount += 1
            else:
              self._mergeDuplicateSeenAlbumRows(connection, seenRow, duplicateRow)
              connection.execute("DELETE FROM subscription_seen_albums WHERE id = ?", (seenRow["id"],))
              deletedDuplicateAlbumCount += 1
          connection.execute("DELETE FROM artist_subscriptions WHERE id = ?", (subscriptionId,))
          mergedCount += 1
          continue

        artistUrl = str(row["artist_url"] or "")
        rewrittenArtistUrl = rewriteAppleMusicUrlStorefront(artistUrl, normalizedStorefront) if artistUrl else ""
        if not rewrittenArtistUrl:
          rewrittenArtistUrl = buildArtistUrl(normalizedStorefront, str(row["artist_name"]), str(row["artist_id"]))
        connection.execute(
          """
          UPDATE artist_subscriptions
          SET storefront = ?, artist_url = ?, updated_at = CURRENT_TIMESTAMP
          WHERE id = ?
          """,
          (normalizedStorefront, rewrittenArtistUrl, subscriptionId),
        )
        migratedCount += 1
      connection.commit()
    return {
      "migratedSubscriptions": migratedCount,
      "mergedSubscriptions": mergedCount,
      "movedAlbums": movedAlbumCount,
      "deletedDuplicateAlbums": deletedDuplicateAlbumCount,
    }

  def listFailedSeenAlbumsForStorefront(self, storefront: str) -> list[dict[str, str]]:
    normalizedStorefront = normalizeStrictStorefront(storefront, "storefront")
    urlPrefix = f"https://music.apple.com/{normalizedStorefront}/%"
    with closing(self._connect()) as connection:
      rows = connection.execute(
        """
        SELECT id, subscription_id, album_id, album_url, album_name, status, task_id, user_state, detected_status
        FROM subscription_seen_albums
        WHERE album_url LIKE ?
          AND (
            status = 'failed'
            OR detected_status IN ('failed_history', 'stale_history')
          )
        ORDER BY subscription_id ASC, id ASC
        """,
        (urlPrefix,),
      ).fetchall()
    return [{key: str(row[key]) for key in row.keys()} for row in rows]

  def deleteFailedSeenAlbumsForStorefront(self, storefront: str) -> list[dict[str, str]]:
    rows = self.listFailedSeenAlbumsForStorefront(storefront)
    if not rows:
      return []
    rowIds = [int(row["id"]) for row in rows]
    with closing(self._connect()) as connection:
      for rowId in rowIds:
        connection.execute("DELETE FROM subscription_seen_albums WHERE id = ?", (rowId,))
      connection.commit()
    return rows

  def upsertSeenAlbum(
    self,
    subscriptionId: int,
    album: AppleMusicAlbum,
    status: str = "seen",
    taskId: str = "",
    userState: str | None = None,
    detectedStatus: str | None = None,
  ) -> None:
    existing = self.getSeenAlbum(subscriptionId, album.albumId)
    resolvedUserState = normalizeAlbumUserState(
      userState,
      normalizeAlbumUserState(existing["userState"], ALBUM_USER_STATE_SUBSCRIBED) if existing is not None else ALBUM_USER_STATE_SUBSCRIBED,
    )
    resolvedDetectedStatus = normalizeDetectedStatus(detectedStatus or status, detectedStatusFromRowStatus(status))
    with closing(self._connect()) as connection:
      connection.execute(
        """
        INSERT INTO subscription_seen_albums (subscription_id, album_id, album_url, album_name, artwork_url, release_date, status, task_id, user_state, detected_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(subscription_id, album_id) DO UPDATE SET
          album_url = excluded.album_url,
          album_name = excluded.album_name,
          artwork_url = COALESCE(NULLIF(excluded.artwork_url, ''), subscription_seen_albums.artwork_url),
          release_date = excluded.release_date,
          status = excluded.status,
          task_id = excluded.task_id,
          user_state = excluded.user_state,
          detected_status = excluded.detected_status,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
          subscriptionId,
          album.albumId,
          album.url,
          album.name,
          album.artworkUrl,
          album.releaseDate,
          rowStatusFromDetectedStatus(resolvedDetectedStatus),
          taskId,
          resolvedUserState,
          resolvedDetectedStatus,
        ),
      )
      connection.commit()

  def upsertSeenAlbumMetadata(self, subscriptionId: int, album: AppleMusicAlbum) -> tuple[dict[str, str], bool]:
    existing = self.getSeenAlbum(subscriptionId, album.albumId)
    created = existing is None
    if created:
      self.upsertSeenAlbum(
        subscriptionId,
        album,
        status="seen",
        taskId="",
        userState=ALBUM_USER_STATE_PENDING,
        detectedStatus="missing",
      )
    else:
      with closing(self._connect()) as connection:
        connection.execute(
          """
          UPDATE subscription_seen_albums
          SET album_url = ?, album_name = ?, artwork_url = COALESCE(NULLIF(?, ''), artwork_url), release_date = ?, updated_at = CURRENT_TIMESTAMP
          WHERE subscription_id = ? AND album_id = ?
          """,
          (album.url, album.name, album.artworkUrl, album.releaseDate, subscriptionId, album.albumId),
        )
        connection.commit()
    row = self.getSeenAlbum(subscriptionId, album.albumId)
    assert row is not None
    return row, created

  def updateSubscriptionPolicy(self, subscriptionId: int, newAlbumPolicy: str) -> bool:
    normalizedPolicy = normalizeSubscriptionPolicy(newAlbumPolicy)
    with closing(self._connect()) as connection:
      cursor = connection.execute(
        """
        UPDATE artist_subscriptions
        SET new_album_policy = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (normalizedPolicy, subscriptionId),
      )
      connection.commit()
      return cursor.rowcount > 0

  def updateSeenAlbumUserState(self, subscriptionId: int, albumId: str, userState: str) -> bool:
    normalizedUserState = normalizeAlbumUserState(userState)
    with closing(self._connect()) as connection:
      cursor = connection.execute(
        """
        UPDATE subscription_seen_albums
        SET user_state = ?, updated_at = CURRENT_TIMESTAMP
        WHERE subscription_id = ? AND album_id = ?
        """,
        (normalizedUserState, subscriptionId, albumId),
      )
      connection.commit()
      return cursor.rowcount > 0

  def updateSeenAlbumDetection(
    self,
    subscriptionId: int,
    albumId: str,
    detectedStatus: str,
    taskId: str = "",
    userState: str | None = None,
  ) -> None:
    normalizedDetectedStatus = normalizeDetectedStatus(detectedStatus, detectedStatusFromRowStatus(detectedStatus))
    status = rowStatusFromDetectedStatus(normalizedDetectedStatus)
    if userState is None:
      with closing(self._connect()) as connection:
        connection.execute(
          """
          UPDATE subscription_seen_albums
          SET status = ?, task_id = ?, detected_status = ?, updated_at = CURRENT_TIMESTAMP
          WHERE subscription_id = ? AND album_id = ?
          """,
          (status, taskId, normalizedDetectedStatus, subscriptionId, albumId),
        )
        connection.commit()
      return
    normalizedUserState = normalizeAlbumUserState(userState)
    with closing(self._connect()) as connection:
      connection.execute(
        """
        UPDATE subscription_seen_albums
        SET status = ?, task_id = ?, user_state = ?, detected_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE subscription_id = ? AND album_id = ?
        """,
        (status, taskId, normalizedUserState, normalizedDetectedStatus, subscriptionId, albumId),
      )
      connection.commit()

  def updateSeenAlbumStatus(self, subscriptionId: int, albumId: str, status: str, taskId: str = "") -> None:
    self.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatusFromRowStatus(status), taskId)

  def updateSeenAlbumStatusByAlbumId(self, albumId: str, status: str, taskId: str = "") -> int:
    if not albumId:
      return 0
    normalizedDetectedStatus = normalizeDetectedStatus(status, detectedStatusFromRowStatus(status))
    rowStatus = rowStatusFromDetectedStatus(normalizedDetectedStatus)
    with closing(self._connect()) as connection:
      cursor = connection.execute(
        """
        UPDATE subscription_seen_albums
        SET
          status = ?,
          task_id = ?,
          detected_status = ?,
          user_state = CASE
            WHEN ? IN ('queued', 'running', 'completed') AND user_state = 'pending' THEN 'subscribed'
            ELSE user_state
          END,
          updated_at = CURRENT_TIMESTAMP
        WHERE album_id = ?
        """,
        (rowStatus, taskId, normalizedDetectedStatus, normalizedDetectedStatus, albumId),
      )
      connection.commit()
      return cursor.rowcount

  def ignoreSeenAlbumAfterTaskCancellation(self, albumId: str, taskId: str) -> int:
    if not albumId or not taskId:
      return 0
    with closing(self._connect()) as connection:
      cursor = connection.execute(
        """
        UPDATE subscription_seen_albums
        SET
          status = 'seen',
          task_id = '',
          detected_status = 'missing',
          user_state = CASE
            WHEN user_state = 'imported' THEN 'imported'
            ELSE 'ignored'
          END,
          updated_at = CURRENT_TIMESTAMP
        WHERE album_id = ?
          AND task_id = ?
          AND (detected_status IN ('queued', 'running') OR status IN ('queued', 'running'))
        """,
        (albumId, taskId),
      )
      connection.commit()
      return cursor.rowcount

  def markChecked(self, subscriptionId: int, error: str = "") -> None:
    with closing(self._connect()) as connection:
      connection.execute(
        """
        UPDATE artist_subscriptions
        SET last_checked_at = CURRENT_TIMESTAMP, last_error = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (error, subscriptionId),
      )
      connection.commit()

  def _rowToSubscription(self, row: sqlite3.Row) -> dict[str, object]:
    keys = set(row.keys())
    return {
      "id": int(row["id"]),
      "artistId": str(row["artist_id"]),
      "storefront": str(row["storefront"]),
      "artistName": str(row["artist_name"]),
      "artistUrl": str(row["artist_url"]),
      "artistArtworkUrl": str(row["artist_artwork_url"]) if "artist_artwork_url" in keys else "",
      "enabled": bool(row["enabled"]),
      "newAlbumPolicy": normalizeSubscriptionPolicy(row["new_album_policy"] if "new_album_policy" in keys else "", SUBSCRIPTION_POLICY_CONFIRM),
      "lastCheckedAt": row["last_checked_at"],
      "lastError": str(row["last_error"]),
      "createdAt": str(row["created_at"]),
      "updatedAt": str(row["updated_at"]),
      "albumCount": int(row["album_count"]) if "album_count" in keys and row["album_count"] is not None else 0,
      "pendingAlbumCount": int(row["pending_album_count"]) if "pending_album_count" in keys and row["pending_album_count"] is not None else 0,
      "activeAlbumCount": int(row["active_album_count"]) if "active_album_count" in keys and row["active_album_count"] is not None else 0,
      "completedAlbumCount": int(row["completed_album_count"]) if "completed_album_count" in keys and row["completed_album_count"] is not None else 0,
      "failedAlbumCount": int(row["failed_album_count"]) if "failed_album_count" in keys and row["failed_album_count"] is not None else 0,
      "ignoredAlbumCount": int(row["ignored_album_count"]) if "ignored_album_count" in keys and row["ignored_album_count"] is not None else 0,
      "importedAlbumCount": int(row["imported_album_count"]) if "imported_album_count" in keys and row["imported_album_count"] is not None else 0,
    }

  def _rowToSeenAlbum(self, row: sqlite3.Row) -> dict[str, str]:
    keys = set(row.keys())
    userState = normalizeAlbumUserState(row["user_state"] if "user_state" in keys else "", ALBUM_USER_STATE_SUBSCRIBED)
    detectedStatus = normalizeDetectedStatus(
      row["detected_status"] if "detected_status" in keys else "",
      detectedStatusFromRowStatus(str(row["status"])),
    )
    return {
      "albumId": str(row["album_id"]),
      "albumUrl": str(row["album_url"]),
      "albumName": str(row["album_name"]),
      "artworkUrl": str(row["artwork_url"]) if "artwork_url" in keys else "",
      "releaseDate": str(row["release_date"]),
      "status": str(row["status"]),
      "taskId": str(row["task_id"]),
      "userState": userState,
      "detectedStatus": detectedStatus,
      "canDownload": canDownloadSubscriptionAlbum(userState, detectedStatus),
      "canIgnore": userState != ALBUM_USER_STATE_IGNORED,
      "canMarkImported": userState != ALBUM_USER_STATE_IMPORTED,
      "updatedAt": str(row["updated_at"]),
    }


@dataclass
class WrapperRecoveryConfig:
  enabled: bool = False
  containerName: str = DEFAULT_WRAPPER_RECOVERY_CONTAINER_NAME
  dockerContext: str = ""
  timeoutSeconds: float = DEFAULT_WRAPPER_RECOVERY_TIMEOUT_SECONDS
  cooldownSeconds: float = DEFAULT_WRAPPER_RECOVERY_COOLDOWN_SECONDS
  maxRetriesPerTask: int = DEFAULT_WRAPPER_RECOVERY_MAX_RETRIES_PER_TASK
  decryptM3u8Port: str = ""


class TaskStore:
  def __init__(self) -> None:
    self._tasks: dict[str, DownloadTask] = {}
    self._lock = threading.Lock()

  def createTask(
    self,
    url: str,
    codec: str,
    source: str = "web",
    taskId: str | None = None,
    wrapperRecoveryAttempts: int = 0,
  ) -> DownloadTask:
    candidateTaskId = taskId or uuid.uuid4().hex
    with self._lock:
      if candidateTaskId in self._tasks:
        candidateTaskId = uuid.uuid4().hex
      task = DownloadTask(
        id=candidateTaskId,
        url=url,
        codec=codec,
        source=source,
        wrapperRecoveryAttempts=max(0, wrapperRecoveryAttempts),
      )
      self._tasks[task.id] = task
    return task

  def getTask(self, taskId: str) -> DownloadTask | None:
    with self._lock:
      return self._tasks.get(taskId)

  def listTasks(self) -> list[DownloadTask]:
    with self._lock:
      return list(self._tasks.values())


class SerialTaskQueue:
  def __init__(self) -> None:
    self._activeTaskId: str | None = None
    self._pending: deque[tuple[str, Callable[[], None]]] = deque()
    self._lock = threading.Lock()

  def submit(self, taskId: str, launchTask: Callable[[], None]) -> bool:
    with self._lock:
      if self._activeTaskId is None:
        self._activeTaskId = taskId
        shouldStart = True
      else:
        self._pending.append((taskId, launchTask))
        shouldStart = False
    return shouldStart

  def complete(self, taskId: str) -> None:
    nextLaunch: Callable[[], None] | None = None
    with self._lock:
      if self._activeTaskId == taskId:
        self._activeTaskId = None
      if self._activeTaskId is None and self._pending:
        nextTaskId, nextLaunch = self._pending.popleft()
        self._activeTaskId = nextTaskId
    if nextLaunch is not None:
      nextLaunch()

  def cancelPending(self, taskId: str) -> bool:
    with self._lock:
      for index, (pendingTaskId, _launchTask) in enumerate(self._pending):
        if pendingTaskId != taskId:
          continue
        del self._pending[index]
        return True
    return False


class WrapperRecoveryManager:
  def __init__(self, config: WrapperRecoveryConfig) -> None:
    self.config = config
    self._lastRecoveryAttemptAt = 0.0
    self._lock = threading.Lock()

  def restartForTask(self, task: DownloadTask) -> bool:
    if not self.config.enabled:
      return False
    reason = task.error or task.failureReasonCandidate
    if not isRecoverableWrapperError(reason, self.config.decryptM3u8Port):
      return False
    if task.wrapperRecoveryAttempts >= self.config.maxRetriesPerTask:
      task.appendLog(
        "Wrapper recovery: automatic retry limit reached "
        f"({task.wrapperRecoveryAttempts}/{self.config.maxRetriesPerTask})."
      )
      return False
    now = time.time()
    with self._lock:
      if self._lastRecoveryAttemptAt > 0 and now - self._lastRecoveryAttemptAt < self.config.cooldownSeconds:
        remainingSeconds = self.config.cooldownSeconds - (now - self._lastRecoveryAttemptAt)
        task.appendLog(f"Wrapper recovery: cooldown active, skipping restart for {remainingSeconds:.0f}s.")
        return False
      self._lastRecoveryAttemptAt = now

    command = buildWrapperRestartCommand(self.config)
    task.appendLog("Wrapper recovery: detected recoverable decrypt service error.")
    task.appendLog(f"Wrapper recovery: restarting Docker container {self.config.containerName}.")
    try:
      completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=self.config.timeoutSeconds,
        check=False,
      )
    except subprocess.TimeoutExpired:
      task.appendLog(f"Wrapper recovery: restart timed out after {self.config.timeoutSeconds:g}s.")
      return False
    except Exception as exc:  # noqa: BLE001
      task.appendLog(f"Wrapper recovery: restart failed: {exc}")
      return False

    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
      if output:
        task.appendLog(f"Wrapper recovery: restart failed: {output}")
      else:
        task.appendLog(f"Wrapper recovery: restart failed with code {completed.returncode}.")
      return False
    if output:
      task.appendLog(f"Wrapper recovery: restart output: {output}")
    task.appendLog("Wrapper recovery: restart succeeded.")
    return True


class DownloaderRunner:
  def __call__(self, task: DownloadTask, url: str, codec: str) -> None:
    command = buildCommand(url, codec)
    process = subprocess.Popen(
      command,
      stdin=subprocess.DEVNULL,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
      bufsize=0,
    )
    assert process.stdout is not None
    task.setStatus("running")
    lastOutputLine = ""
    terminatedForRetryPrompt = False
    for line in iterOutput(process.stdout):
      task.appendLog(line)
      lastOutputLine = line.strip() or lastOutputLine
      updateTaskFromLine(task, line)
      if line.strip() == INTERACTIVE_RETRY_PROMPT:
        terminatedForRetryPrompt = True
        markTaskFailed(task, "download requested interactive retry after errors")
        terminateProcess(process)
        terminateDownloaderContainerProcess(url)
        break
    returnCode = process.wait()
    if returnCode != 0 and task.status != "failed" and not terminatedForRetryPrompt:
      markTaskFailed(task, task.failureReasonCandidate or lastOutputLine or f"download process exited with code {returnCode}")


def terminateProcess(process: subprocess.Popen[str]) -> None:
  if process.poll() is not None:
    return
  process.terminate()
  try:
    process.wait(timeout=5)
    return
  except subprocess.TimeoutExpired:
    pass
  process.kill()
  process.wait()


def terminateDownloaderContainerProcess(url: str) -> None:
  script = r"""
target=${APPLE_MUSIC_DL_TARGET_URL:-}
[ -n "$target" ] || exit 0
for p in /proc/[0-9]*; do
  pid=${p##*/}
  cmd=$(tr '\000' ' ' < "$p/cmdline" 2>/dev/null || true)
  case "$cmd" in
    *apple-music-dl*"$target"*) kill -TERM "$pid" 2>/dev/null || true ;;
  esac
done
"""
  try:
    subprocess.run(
      [
        "docker",
        "exec",
        "-e",
        f"APPLE_MUSIC_DL_TARGET_URL={url}",
        "applemusic_download",
        "sh",
        "-lc",
        script,
      ],
      stdin=subprocess.DEVNULL,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
      timeout=5,
      check=False,
    )
  except Exception:
    pass


def findFileInAlbumDir(albumDir: Path, filename: str) -> Path | None:
  directCandidate = albumDir / filename
  if safePathIsFile(directCandidate):
    return directCandidate
  if not safePathIsDir(albumDir):
    return None
  matches: list[Path] = []
  try:
    for candidate in albumDir.rglob("*"):
      if candidate.name == filename and safePathIsFile(candidate):
        matches.append(candidate)
        if len(matches) > 1:
          return None
  except OSError:
    return None
  return matches[0] if len(matches) == 1 else None


def appendCandidateAlbumDir(candidates: list[Path], seen: set[Path], candidate: Path) -> None:
  if candidate in seen:
    return
  seen.add(candidate)
  candidates.append(candidate)


def appendCompletedAlbumNameCandidates(
  candidates: list[Path],
  seen: set[Path],
  completedRoot: Path,
  albumName: str,
) -> None:
  if not albumName.strip() or not safePathIsDir(completedRoot):
    return
  sanitizedAlbumName = sanitizePathComponent(albumName.strip())
  try:
    artistDirs = list(completedRoot.iterdir())
  except OSError:
    return
  for artistDir in artistDirs:
    if safePathIsDir(artistDir):
      appendCandidateAlbumDir(candidates, seen, artistDir / sanitizedAlbumName)


def resultPathWithinAlbumDir(path: Path, albumDir: Path) -> bool:
  try:
    path.relative_to(albumDir)
    return True
  except ValueError:
    return False


def findResultPathAfterNfo(
  item: dict[str, str],
  originalPath: Path,
  sourceAlbumDirs: list[Path],
  completedRoot: Path,
) -> Path | None:
  if safePathIsFile(originalPath):
    return originalPath
  if not originalPath.name:
    return None

  candidates: list[Path] = []
  seen: set[Path] = set()
  for albumDir in getCompletedAlbumCandidateDirs(item, originalPath, completedRoot):
    appendCandidateAlbumDir(candidates, seen, albumDir)

  for sourceAlbumDir in sourceAlbumDirs:
    if resultPathWithinAlbumDir(originalPath, sourceAlbumDir):
      appendCandidateAlbumDir(candidates, seen, sourceAlbumDir)
    appendCompletedAlbumNameCandidates(candidates, seen, completedRoot, sourceAlbumDir.name)

  itemAlbum = item.get("album", "").strip()
  if itemAlbum:
    appendCompletedAlbumNameCandidates(candidates, seen, completedRoot, itemAlbum)

  parts = originalPath.parts
  for index, part in enumerate(parts):
    if part in DOWNLOAD_FORMAT_DIRS and index + 2 < len(parts):
      appendCompletedAlbumNameCandidates(candidates, seen, completedRoot, parts[index + 2])
      break

  for albumDir in candidates:
    foundPath = findFileInAlbumDir(albumDir, originalPath.name)
    if foundPath is not None:
      return foundPath

  if not safePathIsDir(completedRoot):
    return None
  matches: list[Path] = []
  try:
    for candidate in completedRoot.rglob("*"):
      if candidate.name == originalPath.name and safePathIsFile(candidate):
        matches.append(candidate)
        if len(matches) > 1:
          return None
  except OSError:
    return None
  return matches[0] if len(matches) == 1 else None


def refreshResultPathsAfterNfo(task: DownloadTask, sourceAlbumDirs: list[Path]) -> None:
  completedRoot = getCompletedRoot()
  updatedResult: list[dict[str, str]] = []
  changed = False
  for item in task.result:
    rawPath = item.get("path", "").strip()
    if not rawPath:
      updatedResult.append(item)
      continue
    movedPath = findResultPathAfterNfo(item, Path(rawPath), sourceAlbumDirs, completedRoot)
    if movedPath is None or str(movedPath) == rawPath:
      updatedResult.append(item)
      continue
    updatedResult.append({**item, "path": str(movedPath)})
    changed = True

  if changed:
    task.setResult(updatedResult)
    task.publishEvent("result", result=updatedResult)


class PipelineRunner:
  def __init__(self, downloadRunner: Callable[[DownloadTask, str, str], None]) -> None:
    self.downloadRunner = downloadRunner

  def __call__(self, task: DownloadTask, url: str, codec: str) -> None:
    task.setStatus("running")
    self.downloadRunner(task, url, codec)
    if task.status == "failed":
      return
    if not task.result:
      markTaskFailed(task, "download finished without any result")
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
    removeOriginal = shouldRemoveOriginalAfterConvert()
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
      if not safePathExists(Path(sourcePath)):
        markTaskFailed(
          task,
          f"Source file not accessible: {sourcePath}. Mount the downloads directory into the webapp container."
        )
        return
      command = ["python", "-m", "tools.convert_to_flac", sourcePath]
      if removeOriginal:
        command.append("--remove-original")
      output = self._runScript(task, command)
      flacPath = Path(sourcePath).with_suffix(".flac")
      if output is None:
        return
      finalPath = flacPath
      outputPath = Path(output.strip()) if output.strip() else None
      if outputPath is not None and safePathExists(outputPath):
        finalPath = outputPath
      if not safePathExists(finalPath):
        markTaskFailed(task, f"Converted file not found: {finalPath}")
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
    sourceAlbumDirs = [Path(directory) for directory in sorted(directories)]
    for directory in sourceAlbumDirs:
      self._runScript(task, ["python", "-m", "tools.build_nfo", str(directory)])
      if task.status == "failed":
        return
    refreshResultPathsAfterNfo(task, sourceAlbumDirs)

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
      scriptName = command[2] if len(command) > 2 and command[1] == "-m" else Path(command[1]).name if len(command) > 1 else "script"
      markTaskFailed(task, f"{scriptName} exited with code {returnCode}")
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


def isGenericFailureReason(reason: str) -> bool:
  return (
    reason.startswith("download summary reported ")
    or reason.startswith("download process exited with code ")
    or reason in GENERIC_FAILURE_REASONS
  )


def setFailureReason(task: DownloadTask, reason: str) -> None:
  cleaned = reason.strip()
  if not cleaned:
    return
  if not task.error or isGenericFailureReason(task.error):
    task.setError(cleaned)


def setFailureReasonCandidate(task: DownloadTask, reason: str) -> None:
  cleaned = reason.strip()
  if not cleaned:
    return
  if not task.failureReasonCandidate or isGenericFailureReason(task.failureReasonCandidate):
    task.failureReasonCandidate = cleaned


def extractFailureReasonFromLine(line: str) -> str:
  cleaned = line.strip()
  if not cleaned or cleaned in RETRY_PROMPT_LINES:
    return ""
  for marker in FAILURE_REASON_MARKERS:
    markerIndex = cleaned.find(marker)
    if markerIndex >= 0:
      return cleaned[markerIndex:].strip()
  return ""


def markTaskFailed(task: DownloadTask, reason: str = "") -> None:
  cleanedReason = reason.strip()
  if task.failureReasonCandidate and (not cleanedReason or isGenericFailureReason(cleanedReason)):
    cleanedReason = task.failureReasonCandidate
  setFailureReason(task, cleanedReason)
  task.setStage("failed")
  task.setStatus("failed")


def updateTaskFromLine(task: DownloadTask, line: str) -> None:
  if line in RETRY_PROMPT_LINES:
    return
  if line.startswith("=======  [✔ ] Completed:"):
    summaryMatch = DOWNLOAD_SUMMARY_RE.search(line)
    if summaryMatch:
      completedCount = int(summaryMatch.group(1))
      totalCount = int(summaryMatch.group(2))
      errorCount = int(summaryMatch.group(3))
      if errorCount > 0 or totalCount <= 0 or completedCount < totalCount:
        if errorCount > 0:
          reason = f"download summary reported {errorCount} errors"
        elif completedCount < totalCount:
          reason = f"download summary reported incomplete tracks ({completedCount}/{totalCount} completed)"
        else:
          reason = "download summary reported zero completed tracks"
        markTaskFailed(task, reason)
        return
    task.setStage("completed")
    task.setStatus("completed")
    task.setProgress(100)
    return
  if line.startswith("["):
    try:
      parsed = json.loads(line)
    except json.JSONDecodeError:
      parsed = None
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
  failureReason = extractFailureReasonFromLine(line)
  if failureReason:
    setFailureReasonCandidate(task, failureReason)
    return
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


def normalizeUrl(url: str) -> str:
  return url.strip().rstrip(TRAILING_URL_PUNCTUATION)


def extractAlbumIdFromUrl(url: str) -> str:
  return parseAlbumIdFromUrl(normalizeUrl(url))


def normalizeAlbumTitleForStorefrontMatch(title: str) -> str:
  normalized = unicodedata.normalize("NFKC", str(title or "")).casefold()
  normalized = NULL_FEATURE_RE.sub(" ", normalized)
  normalized = normalized.replace("<null>", " ")
  normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
  return " ".join(normalized.split())


def getArtworkIdentity(url: str) -> str:
  rawUrl = str(url or "").strip()
  if not rawUrl:
    return ""
  try:
    parsed = urllib_parse.urlparse(rawUrl)
  except ValueError:
    return ""
  hostname = (parsed.hostname or "").lower()
  path = urllib_parse.unquote(parsed.path or "")
  if not hostname or not path:
    return ""
  return f"{hostname}{APPLE_MUSIC_ARTWORK_SIZE_SEGMENT_RE.sub('', path).lower()}"


def releaseDatesMatch(left: str, right: str) -> bool:
  leftDate = str(left or "").strip()
  rightDate = str(right or "").strip()
  return bool(leftDate and rightDate and leftDate == rightDate)


def seenAlbumMatchesStorefrontAlbum(seenAlbum: dict[str, str], album: AppleMusicAlbum) -> bool:
  hasSameReleaseDate = releaseDatesMatch(seenAlbum.get("releaseDate", ""), album.releaseDate)
  seenArtworkIdentity = getArtworkIdentity(seenAlbum.get("artworkUrl", ""))
  albumArtworkIdentity = getArtworkIdentity(album.artworkUrl)
  if hasSameReleaseDate and seenArtworkIdentity and seenArtworkIdentity == albumArtworkIdentity:
    return True

  seenTitle = normalizeAlbumTitleForStorefrontMatch(seenAlbum.get("albumName", ""))
  albumTitle = normalizeAlbumTitleForStorefrontMatch(album.name)
  if not seenTitle or not albumTitle:
    return False
  if hasSameReleaseDate and seenTitle == albumTitle:
    return True
  if hasSameReleaseDate and min(len(seenTitle), len(albumTitle)) >= 8:
    return difflib.SequenceMatcher(None, seenTitle, albumTitle).ratio() >= 0.92
  return False


def rewriteAppleMusicUrlStorefront(url: str, storefront: str) -> str:
  normalizedUrl = normalizeUrl(url)
  normalizedStorefront = normalizeStorefront(storefront)
  return re.sub(r"^(https://music\.apple\.com/)[a-z]{2}(/)", rf"\g<1>{normalizedStorefront}\2", normalizedUrl, count=1)


def buildArtistUrl(storefront: str, artistName: str, artistId: str) -> str:
  normalizedStorefront = normalizeStorefront(storefront)
  slug = urllib_parse.quote(str(artistName or artistId).strip().lower().replace(" ", "-"))
  return f"https://music.apple.com/{normalizedStorefront}/artist/{slug}/{artistId}"


def getSubscriptionStorefront(app: Flask) -> str:
  configured = str(app.config.get("SUBSCRIPTION_STOREFRONT", "") or "").strip()
  if configured:
    return normalizeStrictStorefront(configured, "subscription-storefront")
  return getConfiguredSubscriptionStorefront()


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


def sanitizePathComponent(value: str) -> str:
  cleaned = re.sub(r'[<>:"/\\|?*]', '_', value).strip()
  cleaned = cleaned.rstrip('. ')
  return cleaned or "Unknown"


def safePathExists(path: Path) -> bool:
  try:
    return path.exists()
  except OSError:
    return False


def safePathIsFile(path: Path) -> bool:
  try:
    return path.is_file()
  except OSError:
    return False


def safePathIsDir(path: Path) -> bool:
  try:
    return path.is_dir()
  except OSError:
    return False


def getCompletedRoot() -> Path:
  configuredPath = getConfigValue(resolveConfigPath(), "completed-root-folder")
  if not configuredPath:
    return DEFAULT_COMPLETED_ROOT
  return Path(configuredPath).expanduser()


def getCompletedAlbumCandidateDirs(item: dict[str, str], originalPath: Path, completedRoot: Path) -> list[Path]:
  candidates: list[Path] = []
  parts = originalPath.parts
  for index, part in enumerate(parts):
    if part in DOWNLOAD_FORMAT_DIRS and index + 2 < len(parts):
      candidates.append(completedRoot / parts[index + 1] / parts[index + 2])
      break

  artist = item.get("artist", "").strip()
  album = item.get("album", "").strip()
  if artist and album:
    candidates.append(completedRoot / sanitizePathComponent(artist) / sanitizePathComponent(album))

  uniqueCandidates: list[Path] = []
  seen: set[Path] = set()
  for candidate in candidates:
    if candidate in seen:
      continue
    seen.add(candidate)
    uniqueCandidates.append(candidate)
  return uniqueCandidates


def resultItemFileExists(item: dict[str, str], completedRoot: Path) -> bool:
  rawPath = item.get("path", "").strip()
  if not rawPath:
    return False
  originalPath = Path(rawPath)
  if safePathIsFile(originalPath):
    return True
  if originalPath.is_absolute() and len(originalPath.parts) > 1 and originalPath.parts[1] == "downloads" and not safePathExists(Path("/downloads")):
    return True
  if not originalPath.name:
    return False
  for albumDir in getCompletedAlbumCandidateDirs(item, originalPath, completedRoot):
    directCandidate = albumDir / originalPath.name
    if safePathIsFile(directCandidate):
      return True
    if safePathIsDir(albumDir):
      try:
        if any(safePathIsFile(candidate) for candidate in albumDir.rglob(originalPath.name)):
          return True
      except OSError:
        continue
  return False


def storedResultFilesExist(result: list[dict[str, str]]) -> bool:
  itemsWithPath = [item for item in result if item.get("path", "").strip()]
  if not itemsWithPath:
    return False
  completedRoot = getCompletedRoot()
  return all(resultItemFileExists(item, completedRoot) for item in itemsWithPath)


def hasUsableCompletedRecord(record: dict[str, str] | None) -> bool:
  if record is None:
    return False
  if str(record.get("ever_completed", "0")) != "1" and record.get("status") != "completed":
    return False
  return storedResultFilesExist(parseStoredResult(record.get("result_json", "[]")))


def parseBoolConfigValue(rawValue: object, default: bool) -> bool:
  if rawValue is None:
    return default
  if isinstance(rawValue, bool):
    return rawValue
  normalized = str(rawValue).strip().lower()
  if normalized in {"true", "1", "yes", "on"}:
    return True
  if normalized in {"false", "0", "no", "off"}:
    return False
  return default


def shouldRemoveOriginalAfterConvert() -> bool:
  configPath = resolveConfigPath()
  keepOriginal = parseBoolConfigValue(getConfigValue(configPath, "convert-keep-original"), default=True)
  return not keepOriginal


def parseFloatConfigValue(rawValue: object, default: float) -> float:
  if rawValue is None:
    return default
  try:
    return float(str(rawValue).strip())
  except ValueError:
    return default


def parseIntConfigValue(rawValue: object, default: int) -> int:
  if rawValue is None:
    return default
  try:
    return int(str(rawValue).strip())
  except ValueError:
    return default


def loadWrapperRecoveryConfig(configPath: Path | None = None) -> WrapperRecoveryConfig:
  resolvedConfigPath = configPath or resolveConfigPath()
  containerName = (
    getConfigValue(resolvedConfigPath, "wrapper-recovery-container-name")
    or DEFAULT_WRAPPER_RECOVERY_CONTAINER_NAME
  ).strip()
  timeoutSeconds = parseFloatConfigValue(
    getConfigValue(resolvedConfigPath, "wrapper-recovery-timeout-seconds"),
    DEFAULT_WRAPPER_RECOVERY_TIMEOUT_SECONDS,
  )
  cooldownSeconds = parseFloatConfigValue(
    getConfigValue(resolvedConfigPath, "wrapper-recovery-cooldown-seconds"),
    DEFAULT_WRAPPER_RECOVERY_COOLDOWN_SECONDS,
  )
  maxRetriesPerTask = parseIntConfigValue(
    getConfigValue(resolvedConfigPath, "wrapper-recovery-max-retries-per-task"),
    DEFAULT_WRAPPER_RECOVERY_MAX_RETRIES_PER_TASK,
  )
  return WrapperRecoveryConfig(
    enabled=parseBoolConfigValue(getConfigValue(resolvedConfigPath, "wrapper-recovery-enabled"), default=False),
    containerName=containerName or DEFAULT_WRAPPER_RECOVERY_CONTAINER_NAME,
    dockerContext=(getConfigValue(resolvedConfigPath, "wrapper-recovery-docker-context") or "").strip(),
    timeoutSeconds=max(1.0, timeoutSeconds),
    cooldownSeconds=max(0.0, cooldownSeconds),
    maxRetriesPerTask=max(0, maxRetriesPerTask),
    decryptM3u8Port=(getConfigValue(resolvedConfigPath, "decrypt-m3u8-port") or "").strip(),
  )


def buildWrapperRestartCommand(config: WrapperRecoveryConfig) -> list[str]:
  command = ["docker"]
  if config.dockerContext:
    command.extend(["--context", config.dockerContext])
  command.extend(["restart", config.containerName])
  return command


def isRecoverableWrapperError(reason: str, decryptM3u8Port: str = "") -> bool:
  normalizedReason = str(reason or "").strip()
  if "Failed to run v2:" not in normalizedReason:
    return False
  if "connection reset by peer" not in normalizedReason.lower():
    return False
  normalizedPort = str(decryptM3u8Port or "").strip()
  if not normalizedPort:
    return False
  if normalizedPort in normalizedReason:
    return True
  if ":" in normalizedPort:
    return False
  portPart = normalizedPort.rsplit(":", 1)[-1]
  return bool(portPart and f":{portPart}" in normalizedReason)


def getRecoverPendingMaxAgeHours() -> float:
  configPath = resolveConfigPath()
  return parseFloatConfigValue(getConfigValue(configPath, "recover-pending-max-age-hours"), default=12.0)


def parseSqliteTimestamp(rawValue: str) -> datetime | None:
  try:
    return datetime.strptime(rawValue, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
  except ValueError:
    return None


def isRecoverableRecordFresh(record: dict[str, str], maxAgeHours: float) -> bool:
  if maxAgeHours <= 0:
    return True
  updatedAt = parseSqliteTimestamp(record.get("updated_at", ""))
  if updatedAt is None:
    return True
  ageSeconds = (datetime.now(timezone.utc) - updatedAt).total_seconds()
  return ageSeconds <= maxAgeHours * 3600


def maybeRecoverWrapperFailure(
  app: Flask,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  historyStore: DownloadHistoryStore,
  task: DownloadTask,
  albumId: str,
) -> None:
  recoveryManager = app.config.get("WRAPPER_RECOVERY_MANAGER")
  if not isinstance(recoveryManager, WrapperRecoveryManager):
    return
  if not recoveryManager.restartForTask(task):
    return
  try:
    responsePayload = startTask(
      app,
      taskStore,
      taskQueue,
      historyStore,
      task.url,
      task.codec,
      task.source,
      wrapperRecoveryAttempts=task.wrapperRecoveryAttempts + 1,
    )
  except Exception as exc:  # noqa: BLE001
    task.appendLog(f"Wrapper recovery: failed to queue automatic retry: {exc}")
    return
  retryTaskId = str(responsePayload["taskId"])
  task.appendLog(f"Wrapper recovery: queued automatic retry task {retryTaskId}.")
  syncSubscriptionAlbumStatus(app, albumId, "queued", retryTaskId)


def startTask(
  app: Flask,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  historyStore: DownloadHistoryStore,
  url: str,
  codec: str,
  source: str = "web",
  taskId: str | None = None,
  wrapperRecoveryAttempts: int = 0,
) -> dict[str, object]:
  albumId = extractAlbumIdFromUrl(url)
  task = taskStore.createTask(url, codec, source, taskId, wrapperRecoveryAttempts)
  runner = app.config["RUNNER_FACTORY"]()

  def runTask() -> None:
    try:
      runner(task, url, codec)
      if task.status == "completed" and task.result:
        historyStore.saveCompleted(url, task.id, codec, task.result, albumId)
        syncSubscriptionAlbumStatus(app, albumId, "completed", task.id)
      elif task.status == "completed" and not task.result:
        markTaskFailed(task, "download finished without any result")
        historyStore.saveFailed(url, task.id, codec, task.error or "download failed", source, albumId)
        syncSubscriptionAlbumStatus(app, albumId, "failed", task.id)
      elif task.status == "failed":
        if not task.error:
          markTaskFailed(task, "download finished without any result")
        historyStore.saveFailed(url, task.id, codec, task.error or "download failed", source, albumId)
        syncSubscriptionAlbumStatus(app, albumId, "failed", task.id)
        maybeRecoverWrapperFailure(app, taskStore, taskQueue, historyStore, task, albumId)
    except Exception as exc:  # noqa: BLE001
      markTaskFailed(task, str(exc))
      historyStore.saveFailed(url, task.id, codec, str(exc), source, albumId)
      syncSubscriptionAlbumStatus(app, albumId, "failed", task.id)
      maybeRecoverWrapperFailure(app, taskStore, taskQueue, historyStore, task, albumId)
    finally:
      taskQueue.complete(task.id)

  def launchTask() -> None:
    task.setStatus("running")
    historyStore.saveRunning(url, task.id, codec, source, albumId)
    if app.testing:
      runTask()
      return
    thread = threading.Thread(target=runTask, daemon=True)
    thread.start()

  task.setStage("queued")
  task.setStatus("queued")
  historyStore.saveQueued(url, task.id, codec, source, albumId)
  startedImmediately = taskQueue.submit(task.id, launchTask)

  if startedImmediately:
    task.setStatus("running")
    responsePayload = task.toDict()
    historyStore.saveRunning(url, task.id, codec, source, albumId)
    if app.testing:
      runTask()
    else:
      thread = threading.Thread(target=runTask, daemon=True)
      thread.start()
  else:
    task.setStage("queued")
    task.setStatus("queued")
    responsePayload = task.toDict()

  return responsePayload


def cancelQueuedTask(
  app: Flask,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  historyStore: DownloadHistoryStore,
  taskId: str,
) -> tuple[dict[str, object], int]:
  task = taskStore.getTask(taskId)
  if task is None:
    return {"error": "task not found"}, 404
  if task.status != "queued":
    return {"error": "only queued tasks can be cancelled"}, 409
  if not taskQueue.cancelPending(taskId):
    return {"error": "task is no longer queued"}, 409

  albumId = extractAlbumIdFromUrl(task.url)
  task.setError("cancelled before start")
  task.setStage("cancelled")
  task.setStatus("cancelled")
  task.appendLog("queued task cancelled before start")
  historyStore.saveCancelled(task.url, task.id, task.codec, task.source, albumId)

  subscriptionStore = app.config.get("SUBSCRIPTION_STORE")
  if isinstance(subscriptionStore, ArtistSubscriptionStore):
    subscriptionStore.ignoreSeenAlbumAfterTaskCancellation(albumId, task.id)

  return {"cancelled": True, "task": task.toDict()}, 200


def createTaskResponse(
  app: Flask,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  historyStore: DownloadHistoryStore,
  url: str,
  codec: str,
  source: str = "web",
):
  return jsonify(startTask(app, taskStore, taskQueue, historyStore, url, codec, source)), 202


def hasRunningTaskForUrl(taskStore: TaskStore, url: str) -> bool:
  return any(task.url == url and task.status == "running" for task in taskStore.listTasks())


def getActiveTaskForUrl(taskStore: TaskStore, url: str) -> DownloadTask | None:
  matchingTasks = [
    task for task in taskStore.listTasks()
    if task.url == url and task.status in {"queued", "running"}
  ]
  if not matchingTasks:
    return None
  return max(matchingTasks, key=lambda task: task.createdAt)


def getActiveTaskForUrlOrAlbumId(taskStore: TaskStore, url: str, albumId: str = "") -> DownloadTask | None:
  normalizedAlbumId = albumId or extractAlbumIdFromUrl(url)
  matchingTasks = []
  for task in taskStore.listTasks():
    if task.status not in {"queued", "running"}:
      continue
    if task.url == url:
      matchingTasks.append(task)
      continue
    if normalizedAlbumId and extractAlbumIdFromUrl(task.url) == normalizedAlbumId:
      matchingTasks.append(task)
  if not matchingTasks:
    return None
  return max(matchingTasks, key=lambda task: task.createdAt)


def hasActiveTaskForUrl(taskStore: TaskStore, url: str) -> bool:
  return getActiveTaskForUrl(taskStore, url) is not None


def findDownloadRecord(historyStore: DownloadHistoryStore, url: str, albumId: str = "") -> dict[str, str] | None:
  exactRecord = historyStore.getByUrl(url)
  if exactRecord is not None and hasUsableCompletedRecord(exactRecord):
    return exactRecord

  normalizedAlbumId = albumId or extractAlbumIdFromUrl(url)
  albumRecords = historyStore.listByAlbumId(normalizedAlbumId)
  for record in albumRecords:
    if hasUsableCompletedRecord(record):
      return record
  return exactRecord or (albumRecords[0] if albumRecords else None)


def syncSubscriptionAlbumStatus(app: Flask, albumId: str, status: str, taskId: str) -> None:
  if not albumId:
    return
  subscriptionStore = app.config.get("SUBSCRIPTION_STORE")
  if not isinstance(subscriptionStore, ArtistSubscriptionStore):
    return
  subscriptionStore.updateSeenAlbumStatusByAlbumId(albumId, status, taskId)


def retryFailedTasks(app: Flask, taskStore: TaskStore, historyStore: DownloadHistoryStore) -> dict[str, object]:
  retriedTaskIds: list[str] = []
  skippedCompletedUrls: list[str] = []
  skippedRunningUrls: list[str] = []
  seenKeys: set[str] = set()

  for task in taskStore.listTasks():
    albumId = extractAlbumIdFromUrl(task.url)
    seenKey = f"album:{albumId}" if albumId else f"url:{task.url}"
    if task.status != "failed" or seenKey in seenKeys:
      continue
    seenKeys.add(seenKey)

    existing = findDownloadRecord(historyStore, task.url, albumId)
    if hasUsableCompletedRecord(existing):
      skippedCompletedUrls.append(task.url)
      continue

    if getActiveTaskForUrlOrAlbumId(taskStore, task.url, albumId) is not None:
      skippedRunningUrls.append(task.url)
      continue

    responsePayload = startTask(
      app,
      taskStore,
      app.config["TASK_QUEUE"],
      historyStore,
      task.url,
      task.codec,
      task.source,
    )
    retriedTaskIds.append(str(responsePayload["taskId"]))

  return {
    "retriedCount": len(retriedTaskIds),
    "skippedCompletedCount": len(skippedCompletedUrls),
    "skippedRunningCount": len(skippedRunningUrls),
    "retriedTaskIds": retriedTaskIds,
    "skippedCompletedUrls": skippedCompletedUrls,
    "skippedRunningUrls": skippedRunningUrls,
  }


def retryHistoryFailed(
  app: Flask,
  taskStore: TaskStore,
  historyStore: DownloadHistoryStore,
) -> dict[str, object]:
  retriedTaskIds: list[str] = []
  skippedCompletedUrls: list[str] = []
  skippedRunningUrls: list[str] = []
  seenKeys: set[str] = set()

  for record in historyStore.listAll():
    url = record["url"]
    albumId = record.get("album_id", "") or extractAlbumIdFromUrl(url)
    seenKey = f"album:{albumId}" if albumId else f"url:{url}"
    if record["status"] != "failed" or seenKey in seenKeys:
      continue
    seenKeys.add(seenKey)

    existing = findDownloadRecord(historyStore, url, albumId)
    if hasUsableCompletedRecord(existing):
      skippedCompletedUrls.append(url)
      continue

    if getActiveTaskForUrlOrAlbumId(taskStore, url, albumId) is not None:
      skippedRunningUrls.append(url)
      continue

    codec = record["codec"] or "alac"
    source = record.get("source", "web") or "web"
    responsePayload = startTask(app, taskStore, app.config["TASK_QUEUE"], historyStore, url, codec, source)
    retriedTaskIds.append(str(responsePayload["taskId"]))

  return {
    "retriedCount": len(retriedTaskIds),
    "skippedCompletedCount": len(skippedCompletedUrls),
    "skippedRunningCount": len(skippedRunningUrls),
    "retriedTaskIds": retriedTaskIds,
    "skippedCompletedUrls": skippedCompletedUrls,
    "skippedRunningUrls": skippedRunningUrls,
  }


def recoverPendingTasks(
  app: Flask,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  historyStore: DownloadHistoryStore,
) -> int:
  recoveredCount = 0
  maxAgeHours = getRecoverPendingMaxAgeHours()
  for record in historyStore.listRecoverable():
    url = record["url"]
    codec = record.get("codec", "alac") or "alac"
    source = record.get("source", "web") or "web"
    taskId = record.get("task_id", "").strip() or None
    if not isRecoverableRecordFresh(record, maxAgeHours):
      historyStore.saveFailed(
        url,
        taskId or uuid.uuid4().hex,
        codec,
        f"pending task expired during startup recovery after {maxAgeHours:g} hours",
        source,
      )
      continue
    if hasActiveTaskForUrl(taskStore, url):
      continue
    responsePayload = startTask(
      app,
      taskStore,
      taskQueue,
      historyStore,
      url,
      codec,
      source,
      taskId=taskId,
    )
    recoveredTask = taskStore.getTask(str(responsePayload["taskId"]))
    if recoveredTask is not None:
      recoveredTask.appendLog("Recovered pending task after webapp restart")
    recoveredCount += 1
  return recoveredCount


def serializeArtist(artist: AppleMusicArtist) -> dict[str, str]:
  return {
    "artistId": artist.artistId,
    "storefront": artist.storefront,
    "artistName": artist.name,
    "artistUrl": artist.url,
    "artistArtworkUrl": artist.artworkUrl,
  }


def callableAcceptsKeyword(callableObject: Callable[..., object], keyword: str) -> bool:
  try:
    signature = inspect.signature(callableObject)
  except (TypeError, ValueError):
    return True
  for parameter in signature.parameters.values():
    if parameter.kind == inspect.Parameter.VAR_KEYWORD:
      return True
    if parameter.name == keyword and parameter.kind in {
      inspect.Parameter.POSITIONAL_OR_KEYWORD,
      inspect.Parameter.KEYWORD_ONLY,
    }:
      return True
  return False


def createAppleMusicClient(app: Flask, storefront: str | None = None) -> AppleMusicClient:
  factory = app.config["APPLE_MUSIC_CLIENT_FACTORY"]
  if storefront is None:
    return factory()
  if callableAcceptsKeyword(factory, "storefront"):
    return factory(storefront=storefront)
  return factory()


def getStaticVersion(app: Flask) -> str:
  staticPath = Path(app.static_folder or "")
  mtimes: list[int] = []
  for filename in ("app.css", "app.js"):
    filePath = staticPath / filename
    if filePath.exists():
      mtimes.append(int(filePath.stat().st_mtime))
  return str(max(mtimes)) if mtimes else "1"


def normalizeArtworkUrl(value: str) -> str:
  rawUrl = str(value or "").strip()
  if not rawUrl:
    return ""
  try:
    parsed = urllib_parse.urlparse(rawUrl)
    hostname = (parsed.hostname or "").lower()
  except ValueError:
    return ""
  if parsed.scheme.lower() not in {"http", "https"}:
    return ""
  if not hostname or (hostname != ARTWORK_HOST_SUFFIX and not hostname.endswith(f".{ARTWORK_HOST_SUFFIX}")):
    return ""
  if not parsed.path:
    return ""
  return rawUrl


def getArtworkCacheDir(app: Flask) -> Path:
  configuredPath = app.config.get("ARTWORK_CACHE_DIR")
  cacheDir = Path(configuredPath) if configuredPath else Path("data/artwork_cache")
  return cacheDir if cacheDir.is_absolute() else cacheDir.resolve()


def getArtworkCacheKey(url: str) -> str:
  return hashlib.sha256(url.encode("utf-8")).hexdigest()


def getCachedArtworkPath(cacheDir: Path, cacheKey: str) -> Path | None:
  for path in cacheDir.glob(f"{cacheKey}.*"):
    if path.is_file():
      return path
  return None


def normalizeArtworkContentType(value: str) -> str:
  return str(value or "").split(";", 1)[0].strip().lower()


def guessArtworkContentType(path: Path) -> str:
  suffix = path.suffix.lower()
  if suffix in ARTWORK_EXTENSION_CONTENT_TYPES:
    return ARTWORK_EXTENSION_CONTENT_TYPES[suffix]
  return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def fetchRemoteArtwork(url: str) -> tuple[bytes, str]:
  requestHeaders = {
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5",
    "User-Agent": "apple-music-webapp/1.0",
  }
  upstreamRequest = urllib_request.Request(url, headers=requestHeaders)
  with urllib_request.urlopen(upstreamRequest, timeout=ARTWORK_CACHE_TIMEOUT_SECONDS) as response:
    contentType = normalizeArtworkContentType(response.headers.get("Content-Type", ""))
    contentLength = response.headers.get("Content-Length")
    if contentLength:
      try:
        contentLengthBytes = int(contentLength)
      except ValueError:
        contentLengthBytes = 0
      if contentLengthBytes > ARTWORK_CACHE_MAX_BYTES:
        raise ValueError("artwork image is too large")

    chunks: list[bytes] = []
    totalBytes = 0
    while True:
      chunk = response.read(64 * 1024)
      if not chunk:
        break
      totalBytes += len(chunk)
      if totalBytes > ARTWORK_CACHE_MAX_BYTES:
        raise ValueError("artwork image is too large")
      chunks.append(chunk)
  return b"".join(chunks), contentType


def getOrFetchArtwork(app: Flask, url: str) -> tuple[Path, str]:
  cacheDir = getArtworkCacheDir(app)
  cacheKey = getArtworkCacheKey(url)
  cachedPath = getCachedArtworkPath(cacheDir, cacheKey)
  if cachedPath is not None:
    return cachedPath, guessArtworkContentType(cachedPath)

  fetcher = app.config.get("ARTWORK_FETCHER", fetchRemoteArtwork)
  imageBytes, rawContentType = fetcher(url)
  contentType = normalizeArtworkContentType(rawContentType)
  extension = ARTWORK_CONTENT_TYPE_EXTENSIONS.get(contentType)
  if extension is None:
    raise ValueError("upstream did not return a supported image")
  if not imageBytes:
    raise ValueError("upstream returned an empty image")

  cacheDir.mkdir(parents=True, exist_ok=True)
  cachePath = cacheDir / f"{cacheKey}{extension}"
  temporaryPath = cacheDir / f"{cacheKey}.{uuid.uuid4().hex}.tmp"
  try:
    temporaryPath.write_bytes(imageBytes)
    temporaryPath.replace(cachePath)
  finally:
    if temporaryPath.exists():
      temporaryPath.unlink()
  return cachePath, contentType


def detectSubscriptionAlbumStatus(
  historyStore: DownloadHistoryStore,
  taskStore: TaskStore,
  album: AppleMusicAlbum,
) -> tuple[str, str]:
  activeTask = getActiveTaskForUrlOrAlbumId(taskStore, album.url, album.albumId)
  if activeTask is not None:
    return activeTask.status, activeTask.id

  existing = findDownloadRecord(historyStore, album.url, album.albumId)
  detectedStatus = detectedStatusFromHistoryRecord(existing)
  taskId = str(existing.get("task_id", "")) if existing is not None else ""
  return detectedStatus, taskId


def inheritEquivalentSeenAlbumState(
  subscriptionStore: ArtistSubscriptionStore,
  historyStore: DownloadHistoryStore,
  subscriptionId: int,
  album: AppleMusicAlbum,
  storefront: str,
  detectedStatus: str,
  taskId: str,
  summary: SubscriptionScanSummary,
) -> bool:
  equivalentSeenAlbum = subscriptionStore.findEquivalentSeenAlbumForStorefront(subscriptionId, album, storefront)
  if equivalentSeenAlbum is None:
    return False

  equivalentDetectedStatus = normalizeDetectedStatus(
    equivalentSeenAlbum.get("detectedStatus", ""),
    detectedStatusFromRowStatus(equivalentSeenAlbum.get("status", "")),
  )
  equivalentUserState = normalizeAlbumUserState(equivalentSeenAlbum.get("userState", ""), ALBUM_USER_STATE_SUBSCRIBED)
  equivalentTaskId = equivalentSeenAlbum.get("taskId", "")

  if equivalentDetectedStatus == "completed" or equivalentSeenAlbum.get("status") == "completed":
    equivalentRecord = findDownloadRecord(
      historyStore,
      equivalentSeenAlbum.get("albumUrl", ""),
      equivalentSeenAlbum.get("albumId", ""),
    )
    if not hasUsableCompletedRecord(equivalentRecord):
      return False
    subscriptionStore.updateSeenAlbumDetection(
      subscriptionId,
      album.albumId,
      "completed",
      equivalentTaskId or taskId,
      ALBUM_USER_STATE_SUBSCRIBED,
    )
    summary.skippedCompletedCount += 1
    return True

  if equivalentUserState == ALBUM_USER_STATE_IMPORTED:
    subscriptionStore.updateSeenAlbumDetection(
      subscriptionId,
      album.albumId,
      detectedStatus,
      taskId,
      ALBUM_USER_STATE_IMPORTED,
    )
    summary.skippedImportedCount += 1
    return True

  if equivalentUserState == ALBUM_USER_STATE_IGNORED:
    subscriptionStore.updateSeenAlbumDetection(
      subscriptionId,
      album.albumId,
      detectedStatus,
      taskId,
      ALBUM_USER_STATE_IGNORED,
    )
    summary.skippedIgnoredCount += 1
    return True

  return False


def queueSubscriptionAlbumDownload(
  app: Flask,
  subscriptionStore: ArtistSubscriptionStore,
  historyStore: DownloadHistoryStore,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  subscriptionId: int,
  album: AppleMusicAlbum,
  summary: SubscriptionScanSummary,
) -> None:
  try:
    responsePayload = startTask(
      app,
      taskStore,
      taskQueue,
      historyStore,
      album.url,
      "alac",
      "subscription",
    )
  except Exception as exc:  # noqa: BLE001
    summary.errorCount += 1
    summary.errors.append(f"{album.name}: {exc}")
    subscriptionStore.updateSeenAlbumDetection(
      subscriptionId,
      album.albumId,
      "failed_history",
      "",
      ALBUM_USER_STATE_PENDING,
    )
    return

  taskId = str(responsePayload.get("taskId", ""))
  task = taskStore.getTask(taskId)
  status = task.status if task is not None else str(responsePayload.get("status", "queued")) or "queued"
  summary.queuedCount += 1
  if taskId:
    summary.queuedTaskIds.append(taskId)
  subscriptionStore.updateSeenAlbumDetection(
    subscriptionId,
    album.albumId,
    status,
    taskId,
    ALBUM_USER_STATE_SUBSCRIBED,
  )


def scanSubscriptionUnlocked(
  app: Flask,
  subscriptionStore: ArtistSubscriptionStore,
  historyStore: DownloadHistoryStore,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  subscription: dict[str, object],
) -> SubscriptionScanSummary:
  subscriptionId = int(subscription["id"])
  artistId = str(subscription["artistId"])
  artistName = str(subscription["artistName"])
  storefront = getSubscriptionStorefront(app)
  newAlbumPolicy = normalizeSubscriptionPolicy(subscription.get("newAlbumPolicy"), SUBSCRIPTION_POLICY_CONFIRM)
  summary = SubscriptionScanSummary(
    subscriptionId=subscriptionId,
    artistId=artistId,
    artistName=artistName,
  )

  try:
    albums = createAppleMusicClient(app, storefront).listArtistAlbums(storefront, artistId)
    summary.foundCount = len(albums)
    for album in albums:
      albumId = album.albumId or extractAlbumIdFromUrl(album.url)
      if not albumId or not album.url:
        summary.errorCount += 1
        summary.errors.append(f"专辑信息不完整: {album.name or album.url}")
        continue
      normalizedAlbum = AppleMusicAlbum(
        albumId=albumId,
        name=album.name,
        url=normalizeUrl(album.url),
        releaseDate=album.releaseDate,
        artworkUrl=album.artworkUrl,
      )
      seenAlbum, _created = subscriptionStore.upsertSeenAlbumMetadata(subscriptionId, normalizedAlbum)
      currentUserState = normalizeAlbumUserState(seenAlbum.get("userState"), ALBUM_USER_STATE_PENDING)
      storedDetectedStatus = normalizeDetectedStatus(
        seenAlbum.get("detectedStatus", ""),
        detectedStatusFromRowStatus(seenAlbum.get("status", "")),
      )
      detectedStatus, taskId = detectSubscriptionAlbumStatus(historyStore, taskStore, normalizedAlbum)

      if detectedStatus in ALBUM_DETECTED_ACTIVE:
        summary.skippedActiveCount += 1
        userState = currentUserState if currentUserState in {ALBUM_USER_STATE_IGNORED, ALBUM_USER_STATE_IMPORTED} else ALBUM_USER_STATE_SUBSCRIBED
        subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId, userState)
        continue

      if detectedStatus == "completed":
        summary.skippedCompletedCount += 1
        userState = currentUserState if currentUserState in {ALBUM_USER_STATE_IGNORED, ALBUM_USER_STATE_IMPORTED} else ALBUM_USER_STATE_SUBSCRIBED
        subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId, userState)
        continue

      if storedDetectedStatus == "completed" and detectedStatus in {"missing", "stale_history"}:
        completedRecord = findDownloadRecord(historyStore, normalizedAlbum.url, albumId)
        if not hasUsableCompletedRecord(completedRecord):
          if currentUserState == ALBUM_USER_STATE_SUBSCRIBED:
            currentUserState = ALBUM_USER_STATE_PENDING
        else:
          summary.skippedCompletedCount += 1
          subscriptionStore.updateSeenAlbumDetection(
            subscriptionId,
            albumId,
            "completed",
            seenAlbum.get("taskId", "") or taskId,
            ALBUM_USER_STATE_SUBSCRIBED,
          )
          continue

      if inheritEquivalentSeenAlbumState(
        subscriptionStore,
        historyStore,
        subscriptionId,
        normalizedAlbum,
        storefront,
        detectedStatus,
        taskId,
        summary,
      ):
        continue

      if currentUserState == ALBUM_USER_STATE_IGNORED:
        summary.skippedIgnoredCount += 1
        subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId, currentUserState)
        continue

      if currentUserState == ALBUM_USER_STATE_IMPORTED:
        summary.skippedImportedCount += 1
        subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId, currentUserState)
        continue

      if detectedStatus in ALBUM_DETECTED_NEEDS_CONFIRM and newAlbumPolicy == SUBSCRIPTION_POLICY_AUTO:
        subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId, ALBUM_USER_STATE_SUBSCRIBED)
        queueSubscriptionAlbumDownload(
          app,
          subscriptionStore,
          historyStore,
          taskStore,
          taskQueue,
          subscriptionId,
          normalizedAlbum,
          summary,
        )
        continue

      if detectedStatus in ALBUM_DETECTED_NEEDS_CONFIRM:
        summary.pendingCount += 1
        subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId, ALBUM_USER_STATE_PENDING)
        continue

      subscriptionStore.updateSeenAlbumDetection(subscriptionId, albumId, detectedStatus, taskId)

    subscriptionStore.markChecked(subscriptionId)
  except Exception as exc:  # noqa: BLE001
    message = str(exc)
    summary.errorCount += 1
    summary.errors.append(message)
    subscriptionStore.markChecked(subscriptionId, message)
  return summary


def scanSubscription(app: Flask, subscriptionId: int, blocking: bool = False) -> tuple[SubscriptionScanSummary | None, str]:
  scanLock: threading.Lock = app.config["SUBSCRIPTION_SCAN_LOCK"]
  acquired = scanLock.acquire(blocking=blocking)
  if not acquired:
    return None, "subscription scan already running"
  try:
    subscriptionStore: ArtistSubscriptionStore = app.config["SUBSCRIPTION_STORE"]
    subscription = subscriptionStore.get(subscriptionId)
    if subscription is None:
      return None, "subscription not found"
    summary = scanSubscriptionUnlocked(
      app,
      subscriptionStore,
      app.config["HISTORY_STORE"],
      app.config["TASK_STORE"],
      app.config["TASK_QUEUE"],
      subscription,
    )
    return summary, ""
  finally:
    scanLock.release()


def scanAllSubscriptions(app: Flask, blocking: bool = False) -> tuple[dict[str, object] | None, str]:
  scanLock: threading.Lock = app.config["SUBSCRIPTION_SCAN_LOCK"]
  acquired = scanLock.acquire(blocking=blocking)
  if not acquired:
    return None, "subscription scan already running"
  try:
    subscriptionStore: ArtistSubscriptionStore = app.config["SUBSCRIPTION_STORE"]
    summaries: list[SubscriptionScanSummary] = []
    for subscription in subscriptionStore.listEnabled():
      summaries.append(
        scanSubscriptionUnlocked(
          app,
          subscriptionStore,
          app.config["HISTORY_STORE"],
          app.config["TASK_STORE"],
          app.config["TASK_QUEUE"],
          subscription,
        )
      )
    return aggregateSubscriptionSummaries(summaries), ""
  finally:
    scanLock.release()


def aggregateSubscriptionSummaries(summaries: list[SubscriptionScanSummary]) -> dict[str, object]:
  summaryPayloads = [summary.toDict() for summary in summaries]
  return {
    "scannedCount": len(summaries),
    "foundCount": sum(summary.foundCount for summary in summaries),
    "queuedCount": sum(summary.queuedCount for summary in summaries),
    "pendingCount": sum(summary.pendingCount for summary in summaries),
    "skippedCompletedCount": sum(summary.skippedCompletedCount for summary in summaries),
    "skippedActiveCount": sum(summary.skippedActiveCount for summary in summaries),
    "skippedIgnoredCount": sum(summary.skippedIgnoredCount for summary in summaries),
    "skippedImportedCount": sum(summary.skippedImportedCount for summary in summaries),
    "errorCount": sum(summary.errorCount for summary in summaries),
    "summaries": summaryPayloads,
  }


def normalizeAlbumAction(value: object) -> str:
  normalized = str(value or "").strip().lower().replace("-", "_")
  aliases = {
    "completed": "mark_completed",
    "complete": "mark_completed",
    "imported": "mark_imported",
    "import": "mark_imported",
    "restore": "pending",
  }
  return aliases.get(normalized, normalized)


def applySubscriptionAlbumAction(
  app: Flask,
  subscriptionStore: ArtistSubscriptionStore,
  historyStore: DownloadHistoryStore,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  subscription: dict[str, object],
  albumIds: list[str],
  action: str,
) -> dict[str, object]:
  subscriptionId = int(subscription["id"])
  normalizedAction = normalizeAlbumAction(action)
  summary = SubscriptionScanSummary(
    subscriptionId=subscriptionId,
    artistId=str(subscription["artistId"]),
    artistName=str(subscription["artistName"]),
  )
  updatedAlbumIds: list[str] = []

  if normalizedAction not in {"download", "ignore", "mark_imported", "mark_completed", "pending"}:
    return {"error": "unsupported album action"}

  for albumId in albumIds:
    normalizedAlbumId = str(albumId or "").strip()
    if not normalizedAlbumId:
      continue
    seenAlbum = subscriptionStore.getSeenAlbum(subscriptionId, normalizedAlbumId)
    if seenAlbum is None:
      summary.errorCount += 1
      summary.errors.append(f"专辑不存在: {normalizedAlbumId}")
      continue

    if normalizedAction == "mark_completed":
      subscriptionStore.updateSeenAlbumDetection(
        subscriptionId,
        normalizedAlbumId,
        "completed",
        "",
        ALBUM_USER_STATE_SUBSCRIBED,
      )
      updatedAlbumIds.append(normalizedAlbumId)
      summary.skippedCompletedCount += 1
      continue

    if normalizedAction != "download":
      targetState = {
        "ignore": ALBUM_USER_STATE_IGNORED,
        "mark_imported": ALBUM_USER_STATE_IMPORTED,
        "pending": ALBUM_USER_STATE_PENDING,
      }[normalizedAction]
      if subscriptionStore.updateSeenAlbumUserState(subscriptionId, normalizedAlbumId, targetState):
        updatedAlbumIds.append(normalizedAlbumId)
      continue

    album = AppleMusicAlbum(
      albumId=normalizedAlbumId,
      name=str(seenAlbum.get("albumName", "")),
      url=normalizeUrl(str(seenAlbum.get("albumUrl", ""))),
      releaseDate=str(seenAlbum.get("releaseDate", "")),
    )
    if not album.url:
      summary.errorCount += 1
      summary.errors.append(f"专辑缺少链接: {normalizedAlbumId}")
      continue

    detectedStatus, taskId = detectSubscriptionAlbumStatus(historyStore, taskStore, album)
    if detectedStatus in ALBUM_DETECTED_ACTIVE:
      summary.skippedActiveCount += 1
      subscriptionStore.updateSeenAlbumDetection(subscriptionId, normalizedAlbumId, detectedStatus, taskId, ALBUM_USER_STATE_SUBSCRIBED)
      updatedAlbumIds.append(normalizedAlbumId)
      continue
    if detectedStatus == "completed":
      summary.skippedCompletedCount += 1
      subscriptionStore.updateSeenAlbumDetection(subscriptionId, normalizedAlbumId, detectedStatus, taskId, ALBUM_USER_STATE_SUBSCRIBED)
      updatedAlbumIds.append(normalizedAlbumId)
      continue
    if detectedStatus not in ALBUM_DETECTED_NEEDS_CONFIRM:
      subscriptionStore.updateSeenAlbumDetection(subscriptionId, normalizedAlbumId, detectedStatus, taskId, ALBUM_USER_STATE_SUBSCRIBED)
      updatedAlbumIds.append(normalizedAlbumId)
      continue

    subscriptionStore.updateSeenAlbumDetection(subscriptionId, normalizedAlbumId, detectedStatus, taskId, ALBUM_USER_STATE_SUBSCRIBED)
    beforeQueuedCount = summary.queuedCount
    queueSubscriptionAlbumDownload(
      app,
      subscriptionStore,
      historyStore,
      taskStore,
      taskQueue,
      subscriptionId,
      album,
      summary,
    )
    if summary.queuedCount > beforeQueuedCount:
      updatedAlbumIds.append(normalizedAlbumId)

  payload = summary.toDict()
  payload.update({
    "action": normalizedAction,
    "updatedCount": len(updatedAlbumIds),
    "updatedAlbumIds": updatedAlbumIds,
  })
  return payload


def runAutomaticSubscriptionScan(app: Flask) -> None:
  payload, error = scanAllSubscriptions(app, blocking=False)
  if error:
    print(f"[subscriptions] skipped automatic scan: {error}", flush=True)
    return
  if payload is None:
    return
  print(f"[subscriptions] automatic scan completed: {payload}", flush=True)
  notifyTelegramSubscriptionSummary(payload)


def startSubscriptionScheduler(app: Flask) -> None:
  if app.config.get("SUBSCRIPTION_SCHEDULER_STARTED"):
    return
  app.config["SUBSCRIPTION_SCHEDULER_STARTED"] = True

  def runLoop() -> None:
    runAutomaticSubscriptionScan(app)
    while True:
      time.sleep(SUBSCRIPTION_SCAN_INTERVAL_SECONDS)
      runAutomaticSubscriptionScan(app)

  thread = threading.Thread(target=runLoop, daemon=True)
  thread.start()


def notifyTelegramSubscriptionSummary(payload: dict[str, object]) -> None:
  configPath = resolveConfigPath()
  botToken = getConfigValue(configPath, "telegram-bot-token") or ""
  allowedChatId = getConfigValue(configPath, "telegram-allowed-chat-id") or ""
  if not botToken or botToken == "your-telegram-bot-token" or not allowedChatId:
    return
  try:
    from webapp.telegram_bot import formatSubscriptionScanSummaryMessage, sendTelegramMessage

    sendTelegramMessage(
      botToken,
      int(allowedChatId),
      formatSubscriptionScanSummaryMessage(payload),
    )
  except Exception as exc:  # noqa: BLE001
    print(f"[subscriptions] failed to send Telegram summary: {exc}", flush=True)


def createApp(
  runnerFactory: Callable[[], Callable[[DownloadTask, str, str], None]] | None = None,
  dbPath: str | None = None,
  recoverPending: bool = True,
  appleMusicClientFactory: Callable[[], AppleMusicClient] | None = None,
  startScheduler: bool = False,
) -> Flask:
  app = Flask(__name__, template_folder="templates", static_folder="static")
  taskStore = TaskStore()
  taskQueue = SerialTaskQueue()
  historyDbPath = dbPath or str(Path("data/downloads.db"))
  configPath = resolveConfigPath()
  historyStore = DownloadHistoryStore(historyDbPath)
  subscriptionStore = ArtistSubscriptionStore(historyDbPath)
  app.config["TASK_STORE"] = taskStore
  app.config["TASK_QUEUE"] = taskQueue
  app.config["HISTORY_STORE"] = historyStore
  app.config["SUBSCRIPTION_STORE"] = subscriptionStore
  app.config["SUBSCRIPTION_SCAN_LOCK"] = threading.Lock()
  app.config["RUNNER_FACTORY"] = runnerFactory or (lambda: PipelineRunner(DownloaderRunner()))
  app.config["APPLE_MUSIC_CLIENT_FACTORY"] = appleMusicClientFactory or (lambda storefront=None: AppleMusicClient(storefront=storefront))
  app.config["SUBSCRIPTION_STOREFRONT"] = getConfiguredSubscriptionStorefront(configPath)
  subscriptionStore.migrateSubscriptionsToStorefront(app.config["SUBSCRIPTION_STOREFRONT"])
  app.config["ARTWORK_CACHE_DIR"] = Path(historyDbPath).parent / "artwork_cache"
  app.config["WRAPPER_RECOVERY_MANAGER"] = WrapperRecoveryManager(loadWrapperRecoveryConfig(configPath))

  @app.get("/")
  def index() -> str:
    return render_template("index.html", static_version=getStaticVersion(app))

  @app.get("/api/artwork")
  def artworkRoute():
    artworkUrl = normalizeArtworkUrl(str(request.args.get("url", "")))
    if not artworkUrl:
      return jsonify({"error": "invalid artwork URL"}), 400
    try:
      cachePath, contentType = getOrFetchArtwork(app, artworkUrl)
    except ValueError as exc:
      return jsonify({"error": str(exc)}), 502
    except urllib_error.URLError as exc:
      return jsonify({"error": str(exc)}), 502
    except TimeoutError:
      return jsonify({"error": "artwork request timed out"}), 502
    return send_file(cachePath, mimetype=contentType, conditional=True, max_age=7 * 24 * 60 * 60)

  @app.post("/api/tasks")
  def createTaskRoute():
    payload = request.get_json(silent=True) or {}
    url = normalizeUrl(str(payload.get("url", "")))
    codec = str(payload.get("codec", "alac")).strip() or "alac"
    if not APPLE_MUSIC_URL_RE.match(url):
      return jsonify({"error": "invalid Apple Music URL"}), 400
    if codec not in {"alac", "aac", "atmos"}:
      return jsonify({"error": "unsupported codec"}), 400

    return createTaskResponse(app, taskStore, taskQueue, historyStore, url, codec)

  @app.post("/api/downloads")
  def createDownloadRoute():
    payload = request.get_json(silent=True) or {}
    url = normalizeUrl(str(payload.get("url", "")))
    force = parseBoolConfigValue(payload.get("force", False), default=False)
    source = str(payload.get("source", "web")).strip().lower() or "web"
    if not APPLE_MUSIC_URL_RE.match(url):
      return jsonify({"error": "invalid Apple Music URL"}), 400
    codec = "alac"
    if source not in {"web", "telegram", "subscription"}:
      source = "web"
    albumId = extractAlbumIdFromUrl(url)

    if not force:
      activeTask = getActiveTaskForUrlOrAlbumId(taskStore, url, albumId)
      if activeTask is not None:
        return jsonify({
          "taskId": activeTask.id,
          "status": activeTask.status,
          "message": "download already in progress" if activeTask.status == "running" else "download already queued",
        }), 200
      existing = findDownloadRecord(historyStore, url, albumId)
      if existing is not None:
        storedResult = parseStoredResult(existing["result_json"])
        if existing["status"] == "completed":
          if not hasUsableCompletedRecord(existing):
            return createTaskResponse(app, taskStore, taskQueue, historyStore, url, codec, source)
          return jsonify({
            "taskId": existing["task_id"],
            "status": "completed",
            "message": "already downloaded",
            "result": storedResult,
          }), 200
        if existing["status"] == "running":
          activeTask = taskStore.getTask(existing["task_id"])
          if activeTask is None or activeTask.status != "running":
            return createTaskResponse(app, taskStore, taskQueue, historyStore, url, codec, source)
          return jsonify({
            "taskId": existing["task_id"],
            "status": "running",
            "message": "download already in progress",
          }), 200

    return createTaskResponse(app, taskStore, taskQueue, historyStore, url, codec, source)

  @app.post("/api/subscriptions/search")
  def searchSubscriptionsRoute():
    payload = request.get_json(silent=True) or {}
    term = str(payload.get("term", payload.get("query", ""))).strip()
    if not term:
      return jsonify({"error": "search term is required"}), 400
    try:
      subscriptionStorefront = getSubscriptionStorefront(app)
      artists = createAppleMusicClient(app, subscriptionStorefront).searchArtists(term)
    except Exception as exc:  # noqa: BLE001
      return jsonify({"error": str(exc)}), 502
    return jsonify({"results": [serializeArtist(artist) for artist in artists]})

  @app.post("/api/subscriptions")
  def createSubscriptionRoute():
    payload = request.get_json(silent=True) or {}
    artistUrl = normalizeUrl(str(payload.get("artistUrl", payload.get("artist_url", payload.get("url", "")))))
    artistId = str(payload.get("artistId", payload.get("artist_id", ""))).strip()
    subscriptionStorefront = getSubscriptionStorefront(app)
    artistName = str(payload.get("artistName", payload.get("artist_name", ""))).strip()
    artistArtworkUrl = str(payload.get("artistArtworkUrl", payload.get("artist_artwork_url", ""))).strip()

    try:
      if artistUrl:
        parsed = parseArtistUrl(artistUrl)
        if parsed is None:
          return jsonify({"error": "invalid Apple Music artist URL"}), 400
        parsedStorefront, parsedArtistId = parsed
        if artistId and artistName:
          resolvedArtistId = artistId or parsedArtistId
          resolvedArtistUrl = artistUrl if parsedStorefront == subscriptionStorefront else buildArtistUrl(subscriptionStorefront, artistName, resolvedArtistId)
          artist = AppleMusicArtist(
            artistId=resolvedArtistId,
            storefront=subscriptionStorefront,
            name=artistName,
            url=resolvedArtistUrl,
            artworkUrl=artistArtworkUrl,
          )
        else:
          artist = createAppleMusicClient(app, subscriptionStorefront).getArtist(subscriptionStorefront, parsedArtistId)
      else:
        if not artistId or not artistName:
          return jsonify({"error": "artistUrl or artistId/artistName is required"}), 400
        rawArtistUrl = str(payload.get("artistUrl", payload.get("artist_url", ""))).strip()
        artistUrl = rewriteAppleMusicUrlStorefront(rawArtistUrl, subscriptionStorefront) if rawArtistUrl else ""
        artist = AppleMusicArtist(
          artistId=artistId,
          storefront=subscriptionStorefront,
          name=artistName,
          url=artistUrl or buildArtistUrl(subscriptionStorefront, artistName, artistId),
          artworkUrl=artistArtworkUrl,
        )
    except Exception as exc:  # noqa: BLE001
      return jsonify({"error": str(exc)}), 502

    subscription, created = subscriptionStore.createOrEnable(artist)
    summary, errorMessage = scanSubscription(app, int(subscription["id"]), blocking=True)
    if summary is None:
      return jsonify({
        "subscription": subscription,
        "created": created,
        "scan": None,
        "error": errorMessage,
      }), 409
    return jsonify({
      "subscription": subscriptionStore.get(int(subscription["id"])),
      "created": created,
      "scan": summary.toDict(),
    }), 201 if created else 200

  @app.get("/api/subscriptions")
  def listSubscriptionsRoute():
    return jsonify(subscriptionStore.listAll())

  @app.post("/api/subscriptions/scan")
  def scanSubscriptionsRoute():
    payload, errorMessage = scanAllSubscriptions(app, blocking=False)
    if payload is None:
      return jsonify({"error": errorMessage}), 409
    return jsonify(payload)

  @app.post("/api/subscriptions/<int:subscriptionId>/scan")
  def scanSubscriptionRoute(subscriptionId: int):
    summary, errorMessage = scanSubscription(app, subscriptionId, blocking=False)
    if summary is None:
      statusCode = 404 if errorMessage == "subscription not found" else 409
      return jsonify({"error": errorMessage}), statusCode
    return jsonify(summary.toDict())

  @app.patch("/api/subscriptions/<int:subscriptionId>")
  def updateSubscriptionRoute(subscriptionId: int):
    payload = request.get_json(silent=True) or {}
    newAlbumPolicy = normalizeSubscriptionPolicy(payload.get("newAlbumPolicy", payload.get("new_album_policy", "")), "")
    if not newAlbumPolicy:
      return jsonify({"error": "newAlbumPolicy must be confirm or auto"}), 400
    updated = subscriptionStore.updateSubscriptionPolicy(subscriptionId, newAlbumPolicy)
    if not updated:
      return jsonify({"error": "subscription not found"}), 404
    subscription = subscriptionStore.get(subscriptionId)
    return jsonify({"subscription": subscription})

  @app.post("/api/subscriptions/<int:subscriptionId>/albums/actions")
  def subscriptionAlbumActionRoute(subscriptionId: int):
    subscription = subscriptionStore.get(subscriptionId)
    if subscription is None:
      return jsonify({"error": "subscription not found"}), 404
    payload = request.get_json(silent=True) or {}
    rawAlbumIds = payload.get("albumIds", payload.get("album_ids", payload.get("albumId", payload.get("album_id", []))))
    if isinstance(rawAlbumIds, str):
      albumIds = [rawAlbumIds]
    elif isinstance(rawAlbumIds, list):
      albumIds = [str(item) for item in rawAlbumIds]
    else:
      albumIds = []
    if not albumIds:
      return jsonify({"error": "albumIds is required"}), 400
    action = normalizeAlbumAction(payload.get("action", ""))
    result = applySubscriptionAlbumAction(
      app,
      subscriptionStore,
      historyStore,
      taskStore,
      taskQueue,
      subscription,
      albumIds,
      action,
    )
    if "error" in result:
      return jsonify(result), 400
    return jsonify(result)

  @app.delete("/api/subscriptions/<int:subscriptionId>")
  def deleteSubscriptionRoute(subscriptionId: int):
    deleted = subscriptionStore.delete(subscriptionId)
    if not deleted:
      return jsonify({"error": "subscription not found"}), 404
    return jsonify({"deleted": True, "subscriptionId": subscriptionId})

  @app.delete("/api/subscriptions/by-artist/<artistId>")
  def deleteSubscriptionByArtistRoute(artistId: str):
    subscription = subscriptionStore.getByArtistId(artistId)
    if subscription is None:
      return jsonify({"error": "subscription not found"}), 404
    subscriptionId = int(subscription["id"])
    subscriptionStore.delete(subscriptionId)
    return jsonify({"deleted": True, "subscriptionId": subscriptionId, "artistId": artistId})

  @app.post("/api/tasks/retry-failed")
  def retryFailedTasksRoute():
    return jsonify(retryFailedTasks(app, taskStore, historyStore))

  @app.get("/api/tasks")
  def listTasksRoute():
    tasks = sorted(taskStore.listTasks(), key=lambda task: task.createdAt, reverse=True)
    taskPayloads: list[dict[str, object]] = []
    for task in tasks:
      albumId = extractAlbumIdFromUrl(task.url)
      seenAlbum = subscriptionStore.getSeenAlbumByAlbumId(albumId)
      payload: dict[str, object] = {
        "taskId": task.id,
        "url": task.url,
        "albumId": albumId,
        "albumName": seenAlbum["albumName"] if seenAlbum is not None else "",
        "status": task.status,
        "stage": task.stage,
        "progress": task.progress,
        "source": task.source,
        "wrapperRecoveryAttempts": task.wrapperRecoveryAttempts,
        "createdAt": task.createdAt,
      }
      taskPayloads.append(payload)
    return jsonify(taskPayloads)

  @app.get("/api/tasks/<taskId>")
  def getTaskRoute(taskId: str):
    task = taskStore.getTask(taskId)
    if task is None:
      return jsonify({"error": "task not found"}), 404
    return jsonify(task.toDict())

  @app.post("/api/tasks/<taskId>/cancel")
  def cancelTaskRoute(taskId: str):
    payload, statusCode = cancelQueuedTask(app, taskStore, taskQueue, historyStore, taskId)
    return jsonify(payload), statusCode

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
          while eventIndex >= len(task.events) and task.status not in {"completed", "failed", "cancelled"}:
            task.condition.wait(timeout=1.0)
            yield formatSse("snapshot", task.toDict())
          events = task.events[eventIndex:]
          eventIndex = len(task.events)
        for event in events:
          yield formatSse(event.eventType, event.payload)
        if task.status in {"completed", "failed", "cancelled"} and eventIndex >= len(task.events):
          yield formatSse("snapshot", task.toDict())
          break

    return Response(eventStream(), mimetype="text/event-stream")

  @app.get("/api/summary")
  def summaryRoute():
    tasks = taskStore.listTasks()
    summary = {
      "queueCount": sum(1 for task in tasks if task.status == "queued"),
      "successCount": sum(1 for task in tasks if task.status == "completed"),
      "failureCount": sum(1 for task in tasks if task.status == "failed"),
      "activeTaskCount": sum(1 for task in tasks if task.status == "running"),
    }
    return jsonify(summary)

  @app.get("/api/history")
  def listHistoryRoute():
    records = historyStore.listAll()
    return jsonify([
      {
        "url": record["url"],
        "status": record["status"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "task_id": record["task_id"],
        "source": record.get("source", "web"),
        "album_id": record.get("album_id", ""),
        "error": record.get("error", ""),
      }
      for record in records
    ])

  @app.post("/api/history/retry-failed")
  def retryHistoryFailedRoute():
    return jsonify(retryHistoryFailed(app, taskStore, historyStore))

  @app.post("/api/history/retry")
  def retrySingleHistoryRoute():
    payload = request.get_json(silent=True) or {}
    url = normalizeUrl(str(payload.get("url", "")))
    if not url:
      return jsonify({"error": "url is required"}), 400

    existing = historyStore.getByUrl(url)
    if existing is None:
      return jsonify({"error": "history record not found"}), 404
    if existing["status"] != "failed":
      return jsonify({"error": "only failed records can be retried"}), 400

    albumId = existing.get("album_id", "") or extractAlbumIdFromUrl(url)
    matchedRecord = findDownloadRecord(historyStore, url, albumId)
    if hasUsableCompletedRecord(matchedRecord):
      return jsonify({"error": "this URL has already been completed before"}), 400

    activeTask = getActiveTaskForUrlOrAlbumId(taskStore, url, albumId)
    if activeTask is not None:
      errorMessage = "a task for this URL is already running" if activeTask.status == "running" else "a task for this URL is already queued"
      return jsonify({"error": errorMessage}), 409

    codec = existing["codec"] or "alac"
    source = existing.get("source", "web") or "web"
    responsePayload = startTask(app, taskStore, taskQueue, historyStore, url, codec, source)
    return jsonify(responsePayload), 202

  if recoverPending:
    recoverPendingTasks(app, taskStore, taskQueue, historyStore)

  if startScheduler and not app.testing:
    startSubscriptionScheduler(app)

  return app


def formatSse(eventType: str, payload: dict[str, object]) -> str:
  return f"event: {eventType}\ndata: {json.dumps({'type': eventType, **payload}, ensure_ascii=False)}\n\n"


if __name__ == "__main__":
  createApp(startScheduler=True).run(host="0.0.0.0", port=5000, debug=False, threaded=True)
