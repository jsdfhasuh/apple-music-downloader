import json
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator

from flask import Flask, Response, jsonify, render_template, request

from webapp.apple_music import AppleMusicAlbum, AppleMusicArtist, AppleMusicClient, parseAlbumIdFromUrl, parseArtistUrl
from webapp.config_loader import getConfigValue, resolveConfigPath


APPLE_MUSIC_URL_RE = re.compile(r"^https://music\.apple\.com/[a-z]{2}/")
DECRYPT_PROGRESS_RE = re.compile(r"Decrypting\.\.\.\s+(\d+)%")
TRAILING_URL_PUNCTUATION = ".,;:!)]}>，。；：！）】》、"
DEFAULT_COMPLETED_ROOT = Path("/downloads/completed")
DOWNLOAD_FORMAT_DIRS = {"ALAC", "AAC", "ATMOS", "Atmos"}
SUBSCRIPTION_SCAN_INTERVAL_SECONDS = 24 * 60 * 60
RETRY_PROMPT_LINES = {
  "Error detected, press Enter to try again...",
  "Start trying again...",
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
          artist_artwork_url = excluded.artist_artwork_url,
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
          artwork_url = excluded.artwork_url,
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
          SET album_url = ?, album_name = ?, artwork_url = ?, release_date = ?, updated_at = CURRENT_TIMESTAMP
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

  def resetSeenAlbumAfterTaskCancellation(self, albumId: str, taskId: str) -> int:
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
            WHEN user_state = 'subscribed' THEN 'pending'
            ELSE user_state
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


class TaskStore:
  def __init__(self) -> None:
    self._tasks: dict[str, DownloadTask] = {}
    self._lock = threading.Lock()

  def createTask(self, url: str, codec: str, source: str = "web", taskId: str | None = None) -> DownloadTask:
    candidateTaskId = taskId or uuid.uuid4().hex
    with self._lock:
      if candidateTaskId in self._tasks:
        candidateTaskId = uuid.uuid4().hex
      task = DownloadTask(id=candidateTaskId, url=url, codec=codec, source=source)
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
    lastOutputLine = ""
    for line in iterOutput(process.stdout):
      task.appendLog(line)
      lastOutputLine = line.strip() or lastOutputLine
      updateTaskFromLine(task, line)
    returnCode = process.wait()
    if returnCode != 0 and task.status != "failed":
      task.setStage("failed")
      task.setStatus("failed")
      task.setError(lastOutputLine or f"download process exited with code {returnCode}")


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
        task.setStage("failed")
        task.setStatus("failed")
        task.setError(
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
      self._runScript(task, ["python", "-m", "tools.build_nfo", directory])

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
      scriptName = command[2] if len(command) > 2 and command[1] == "-m" else Path(command[1]).name if len(command) > 1 else "script"
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
  if line in RETRY_PROMPT_LINES:
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
  return url.strip().rstrip(TRAILING_URL_PUNCTUATION)


def extractAlbumIdFromUrl(url: str) -> str:
  return parseAlbumIdFromUrl(normalizeUrl(url))


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


def startTask(
  app: Flask,
  taskStore: TaskStore,
  taskQueue: SerialTaskQueue,
  historyStore: DownloadHistoryStore,
  url: str,
  codec: str,
  source: str = "web",
  taskId: str | None = None,
) -> dict[str, object]:
  albumId = extractAlbumIdFromUrl(url)
  task = taskStore.createTask(url, codec, source, taskId)
  runner = app.config["RUNNER_FACTORY"]()

  def runTask() -> None:
    try:
      runner(task, url, codec)
      if task.status == "completed" and task.result:
        historyStore.saveCompleted(url, task.id, codec, task.result, albumId)
        syncSubscriptionAlbumStatus(app, albumId, "completed", task.id)
      elif task.status == "failed":
        historyStore.saveFailed(url, task.id, codec, task.error or "download failed", source, albumId)
        syncSubscriptionAlbumStatus(app, albumId, "failed", task.id)
    except Exception as exc:  # noqa: BLE001
      task.setStage("failed")
      task.setStatus("failed")
      task.setError(str(exc))
      historyStore.saveFailed(url, task.id, codec, str(exc), source, albumId)
      syncSubscriptionAlbumStatus(app, albumId, "failed", task.id)
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
  task.setStage("cancelled")
  task.setStatus("cancelled")
  task.setError("cancelled before start")
  task.appendLog("queued task cancelled before start")
  historyStore.saveCancelled(task.url, task.id, task.codec, task.source, albumId)

  subscriptionStore = app.config.get("SUBSCRIPTION_STORE")
  if isinstance(subscriptionStore, ArtistSubscriptionStore):
    subscriptionStore.resetSeenAlbumAfterTaskCancellation(albumId, task.id)

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


def createAppleMusicClient(app: Flask) -> AppleMusicClient:
  return app.config["APPLE_MUSIC_CLIENT_FACTORY"]()


def getStaticVersion(app: Flask) -> str:
  staticPath = Path(app.static_folder or "")
  mtimes: list[int] = []
  for filename in ("app.css", "app.js"):
    filePath = staticPath / filename
    if filePath.exists():
      mtimes.append(int(filePath.stat().st_mtime))
  return str(max(mtimes)) if mtimes else "1"


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
  storefront = str(subscription["storefront"])
  newAlbumPolicy = normalizeSubscriptionPolicy(subscription.get("newAlbumPolicy"), SUBSCRIPTION_POLICY_CONFIRM)
  summary = SubscriptionScanSummary(
    subscriptionId=subscriptionId,
    artistId=artistId,
    artistName=artistName,
  )

  try:
    albums = createAppleMusicClient(app).listArtistAlbums(storefront, artistId)
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


def startSubscriptionScheduler(app: Flask) -> None:
  if app.config.get("SUBSCRIPTION_SCHEDULER_STARTED"):
    return
  app.config["SUBSCRIPTION_SCHEDULER_STARTED"] = True

  def runLoop() -> None:
    while True:
      time.sleep(SUBSCRIPTION_SCAN_INTERVAL_SECONDS)
      payload, error = scanAllSubscriptions(app, blocking=False)
      if error:
        print(f"[subscriptions] skipped automatic scan: {error}", flush=True)
        continue
      if payload is None:
        continue
      print(f"[subscriptions] automatic scan completed: {payload}", flush=True)
      notifyTelegramSubscriptionSummary(payload)

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
  historyStore = DownloadHistoryStore(historyDbPath)
  subscriptionStore = ArtistSubscriptionStore(historyDbPath)
  app.config["TASK_STORE"] = taskStore
  app.config["TASK_QUEUE"] = taskQueue
  app.config["HISTORY_STORE"] = historyStore
  app.config["SUBSCRIPTION_STORE"] = subscriptionStore
  app.config["SUBSCRIPTION_SCAN_LOCK"] = threading.Lock()
  app.config["RUNNER_FACTORY"] = runnerFactory or (lambda: PipelineRunner(DownloaderRunner()))
  app.config["APPLE_MUSIC_CLIENT_FACTORY"] = appleMusicClientFactory or (lambda: AppleMusicClient())

  @app.get("/")
  def index() -> str:
    return render_template("index.html", static_version=getStaticVersion(app))

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
      artists = createAppleMusicClient(app).searchArtists(term)
    except Exception as exc:  # noqa: BLE001
      return jsonify({"error": str(exc)}), 502
    return jsonify({"results": [serializeArtist(artist) for artist in artists]})

  @app.post("/api/subscriptions")
  def createSubscriptionRoute():
    payload = request.get_json(silent=True) or {}
    artistUrl = normalizeUrl(str(payload.get("artistUrl", payload.get("artist_url", payload.get("url", "")))))
    artistId = str(payload.get("artistId", payload.get("artist_id", ""))).strip()
    storefront = str(payload.get("storefront", "")).strip().lower()
    artistName = str(payload.get("artistName", payload.get("artist_name", ""))).strip()
    artistArtworkUrl = str(payload.get("artistArtworkUrl", payload.get("artist_artwork_url", ""))).strip()

    try:
      if artistUrl:
        parsed = parseArtistUrl(artistUrl)
        if parsed is None:
          return jsonify({"error": "invalid Apple Music artist URL"}), 400
        parsedStorefront, parsedArtistId = parsed
        if artistId and artistName:
          artist = AppleMusicArtist(
            artistId=artistId,
            storefront=storefront or parsedStorefront,
            name=artistName,
            url=artistUrl,
            artworkUrl=artistArtworkUrl,
          )
        else:
          artist = createAppleMusicClient(app).getArtist(parsedStorefront, parsedArtistId)
      else:
        if not artistId or not storefront or not artistName:
          return jsonify({"error": "artistUrl or artistId/storefront/artistName is required"}), 400
        artist = AppleMusicArtist(
          artistId=artistId,
          storefront=storefront,
          name=artistName,
          url=str(payload.get("artistUrl", payload.get("artist_url", ""))).strip(),
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
