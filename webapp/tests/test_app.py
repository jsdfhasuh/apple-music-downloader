import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


from webapp.apple_music import AppleMusicAlbum, AppleMusicArtist, formatArtworkUrl
from webapp.app import ArtistSubscriptionStore, DownloadHistoryStore, DownloadTask, DownloaderRunner, PipelineRunner, createApp, updateTaskFromLine


class FakeRunner:
  def __init__(self, resultPath="/downloads/ALAC/example.flac"):
    self.calls = []
    self.autoComplete = True
    self.resultPath = resultPath

  def __call__(self, task, url, codec):
    self.calls.append((task.id, url, codec))
    task.appendLog("Queue 1 of 1: Album")
    task.setStage("queued")
    if not self.autoComplete:
      return
    task.appendLog("Decrypting... 49% (13/25 MB, 5.1 MB/s)")
    task.setStage("decrypting")
    task.setProgress(49)
    task.setResult([
      {
        "path": self.resultPath,
        "artist": "Example Artist",
        "album": "Example Album",
        "song": "Example Song"
      }
    ])
    task.publishEvent("result", result=task.result)
    task.setStage("completed")
    task.setStatus("completed")
    task.setProgress(100)


class FakeAppleMusicClient:
  def __init__(self):
    self.searchResults = [
      AppleMusicArtist(
        artistId="12345",
        storefront="cn",
        name="Example Artist",
        url="https://music.apple.com/cn/artist/example-artist/12345",
        artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/artist-example/512x512bb.jpg",
      )
    ]
    self.artist = self.searchResults[0]
    self.albums: list[AppleMusicAlbum] = []

  def searchArtists(self, term):
    return self.searchResults

  def getArtist(self, storefront, artistId):
    return AppleMusicArtist(
      artistId=artistId,
      storefront=storefront,
      name=self.artist.name,
      url=f"https://music.apple.com/{storefront}/artist/example-artist/{artistId}",
      artworkUrl=self.artist.artworkUrl,
    )

  def listArtistAlbums(self, storefront, artistId):
    return self.albums


class FlaskDashboardTest(unittest.TestCase):
  def setUp(self):
    self.tempDir = tempfile.TemporaryDirectory()
    self.resultPath = Path(self.tempDir.name) / "downloads" / "ALAC" / "example.flac"
    self.resultPath.parent.mkdir(parents=True)
    self.resultPath.write_text("fake flac", encoding="utf-8")
    self.runner = FakeRunner(str(self.resultPath))
    app = createApp(
      runnerFactory=lambda: self.runner,
      dbPath=f"{self.tempDir.name}/downloads.db"
    )
    app.config["TESTING"] = True
    self.client = app.test_client()

  def tearDown(self):
    self.tempDir.cleanup()

  def testIndexPageLoads(self):
    response = self.client.get("/")

    self.assertEqual(response.status_code, 200)
    self.assertIn(b"Apple Music Downloader", response.data)

  def testCreateAppDoesNotStartSubscriptionSchedulerByDefault(self):
    with patch("webapp.app.startSubscriptionScheduler") as startScheduler:
      createApp(
        dbPath=f"{self.tempDir.name}/no-scheduler-downloads.db",
        recoverPending=False,
      )

    self.assertFalse(startScheduler.called)

  def testCreateAppStartsSubscriptionSchedulerWhenRequested(self):
    with patch("webapp.app.startSubscriptionScheduler") as startScheduler:
      createApp(
        dbPath=f"{self.tempDir.name}/scheduler-downloads.db",
        recoverPending=False,
        startScheduler=True,
      )

    self.assertEqual(startScheduler.call_count, 1)

  def testCreateTaskRejectsInvalidUrl(self):
    response = self.client.post(
      "/api/tasks",
      data=json.dumps({"url": "https://example.com/not-apple"}),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 400)

  def testCreateTaskRunsRunnerAndStoresResult(self):
    response = self.client.post(
      "/api/tasks",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
        "codec": "alac"
      }),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 202)
    payload = response.get_json()
    self.assertIsNotNone(payload)
    self.assertEqual(payload["status"], "running")
    self.assertEqual(len(self.runner.calls), 1)

    taskResponse = self.client.get(f"/api/tasks/{payload['taskId']}")
    taskPayload = taskResponse.get_json()
    self.assertEqual(taskPayload["status"], "completed")
    self.assertEqual(taskPayload["progress"], 100)
    self.assertEqual(taskPayload["result"][0]["path"], str(self.resultPath))

  def testCreateDownloadShortcutUsesDefaultCodec(self):
    response = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 202)
    payload = response.get_json()
    self.assertIsNotNone(payload)
    self.assertEqual(payload["status"], "running")
    self.assertTrue(payload["taskId"])
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(self.runner.calls[0][2], "alac")

  def testCreateDownloadShortcutIgnoresCodecOverride(self):
    response = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
        "codec": "aac"
      }),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 202)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(self.runner.calls[0][2], "alac")

  def testListTasksReturnsSourceAndNewestFirst(self):
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
        "source": "telegram"
      }),
      content_type="application/json"
    )
    second = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/happier-than-ever/1564530719",
        "source": "web",
        "force": True
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()
    secondPayload = second.get_json()

    response = self.client.get("/api/tasks")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(len(payload), 2)
    self.assertEqual(payload[0]["taskId"], secondPayload["taskId"])
    self.assertEqual(payload[0]["source"], "web")
    self.assertIsInstance(payload[0]["createdAt"], (int, float))
    self.assertEqual(payload[1]["taskId"], firstPayload["taskId"])
    self.assertEqual(payload[1]["source"], "telegram")

  def testSummaryCountsQueuedAndRunningSeparately(self):
    taskStore = self.client.application.config["TASK_STORE"]
    queuedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/queued/111",
      "alac",
      "telegram",
    )
    queuedTask.setStatus("queued")
    runningTask = taskStore.createTask(
      "https://music.apple.com/cn/album/running/222",
      "alac",
      "web",
    )
    runningTask.setStatus("running")

    response = self.client.get("/api/summary")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["queueCount"], 1)
    self.assertEqual(payload["activeTaskCount"], 1)

  def testTaskStreamEmitsProgressAndResult(self):
    response = self.client.post(
      "/api/tasks",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    payload = response.get_json()

    streamResponse = self.client.get(f"/api/tasks/{payload['taskId']}/stream")
    body = b"".join(streamResponse.response)

    self.assertEqual(streamResponse.status_code, 200)
    self.assertIn(b'"type": "snapshot"', body)
    self.assertIn(b'"progress": 49', body)
    self.assertIn(json.dumps(str(self.resultPath)).encode("utf-8"), body)

  def testDownloadShortcutReusesCompletedUrl(self):
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    taskResponse = self.client.get(f"/api/tasks/{firstPayload['taskId']}")
    self.assertEqual(taskResponse.get_json()["status"], "completed")

    second = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 200)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(secondPayload["taskId"], firstPayload["taskId"])
    self.assertEqual(secondPayload["status"], "completed")
    self.assertEqual(secondPayload["message"], "already downloaded")

  def testDownloadShortcutRedownloadsCompletedUrlWhenFilesAreMissing(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/missing-completed/111",
      "old-task",
      "alac",
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/missing-completed/111",
      "old-task",
      "alac",
      [{
        "path": f"{self.tempDir.name}/ALAC/Artist/Album/missing.flac",
        "artist": "Artist",
        "album": "Album",
        "song": "Missing",
      }],
    )

    response = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/missing-completed/111"
      }),
      content_type="application/json"
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 202)
    self.assertEqual(payload["status"], "running")
    self.assertNotEqual(payload["taskId"], "old-task")
    self.assertEqual(len(self.runner.calls), 1)

  def testDownloadShortcutReusesCompletedUrlMovedToCompletedRoot(self):
    configPath = Path(self.tempDir.name) / "config.yaml"
    completedFile = Path(self.tempDir.name) / "completed" / "Artist" / "Album" / "track.flac"
    completedFile.parent.mkdir(parents=True)
    completedFile.write_text("fake flac", encoding="utf-8")
    configPath.write_text(
      f'completed-root-folder: "{Path(self.tempDir.name) / "completed"}"\n',
      encoding="utf-8",
    )
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/moved-completed/222",
      "old-task",
      "alac",
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/moved-completed/222",
      "old-task",
      "alac",
      [{
        "path": f"{self.tempDir.name}/ALAC/Artist/Album/track.flac",
        "artist": "Artist",
        "album": "Album",
        "song": "Track",
      }],
    )

    originalConfigPath = os.environ.get("WEBAPP_CONFIG_PATH")
    try:
      os.environ["WEBAPP_CONFIG_PATH"] = str(configPath)
      response = self.client.post(
        "/api/downloads",
        data=json.dumps({
          "url": "https://music.apple.com/cn/album/moved-completed/222"
        }),
        content_type="application/json"
      )
    finally:
      if originalConfigPath is None:
        os.environ.pop("WEBAPP_CONFIG_PATH", None)
      else:
        os.environ["WEBAPP_CONFIG_PATH"] = originalConfigPath
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["status"], "completed")
    self.assertEqual(payload["taskId"], "old-task")
    self.assertEqual(len(self.runner.calls), 0)

  def testDownloadShortcutForceCreatesNewTask(self):
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    second = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
        "force": True
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 202)
    self.assertEqual(len(self.runner.calls), 2)
    self.assertNotEqual(secondPayload["taskId"], firstPayload["taskId"])
    self.assertEqual(secondPayload["status"], "running")

  def testDownloadShortcutTreatsFalseStringAsNotForced(self):
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    second = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
        "force": "false"
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 200)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(secondPayload["taskId"], firstPayload["taskId"])
    self.assertEqual(secondPayload["status"], "completed")

  def testDownloadShortcutNormalizesTrailingUrlPunctuation(self):
    response = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347）。"
      }),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 202)
    self.assertEqual(
      self.runner.calls[0][1],
      "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
    )

  def testDownloadHistoryBackfillsAlbumIdFromExistingUrls(self):
    dbPath = f"{self.tempDir.name}/backfill-downloads.db"
    connection = sqlite3.connect(dbPath)
    try:
      connection.execute(
        """
        CREATE TABLE downloads (
          url TEXT PRIMARY KEY,
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
      connection.execute(
        """
        INSERT INTO downloads (url, task_id, status, codec)
        VALUES ('https://music.apple.com/cn/album/example/123456?l=en', 'task-old', 'failed', 'alac')
        """
      )
      connection.commit()
    finally:
      connection.close()

    store = DownloadHistoryStore(dbPath)
    record = store.getByUrl("https://music.apple.com/cn/album/example/123456?l=en")

    self.assertIsNotNone(record)
    self.assertEqual(record["album_id"], "123456")

  def testDownloadHistoryClearsAlbumIdForSongUrlsDuringMigration(self):
    dbPath = f"{self.tempDir.name}/song-backfill-downloads.db"
    songUrl = "https://music.apple.com/cn/album/example/123456?i=999999"
    connection = sqlite3.connect(dbPath)
    try:
      connection.execute(
        """
        CREATE TABLE downloads (
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
      connection.execute(
        """
        INSERT INTO downloads (url, album_id, task_id, status, codec)
        VALUES (?, '123456', 'task-song', 'failed', 'alac')
        """,
        (songUrl,),
      )
      connection.commit()
    finally:
      connection.close()

    store = DownloadHistoryStore(dbPath)
    record = store.getByUrl(songUrl)

    self.assertIsNotNone(record)
    self.assertEqual(record["album_id"], "")

  def testDownloadShortcutDeduplicatesDifferentUrlsForSameAlbumId(self):
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/example-one/1895089347"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    second = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/example-two/1895089347?l=en"
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 200)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(secondPayload["taskId"], firstPayload["taskId"])
    self.assertEqual(secondPayload["status"], "completed")

  def testDownloadShortcutDoesNotReuseCompletedSongAsWholeAlbum(self):
    songResponse = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/example-song/1895089347?i=1895089348"
      }),
      content_type="application/json"
    )
    songPayload = songResponse.get_json()

    albumResponse = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/example-album/1895089347"
      }),
      content_type="application/json"
    )
    albumPayload = albumResponse.get_json()

    self.assertEqual(songResponse.status_code, 202)
    self.assertEqual(albumResponse.status_code, 202)
    self.assertNotEqual(albumPayload["taskId"], songPayload["taskId"])
    self.assertEqual(len(self.runner.calls), 2)

  def testDownloadShortcutReturnsExistingRunningTask(self):
    self.runner.autoComplete = False
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    second = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 200)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(secondPayload["taskId"], firstPayload["taskId"])
    self.assertEqual(secondPayload["status"], "running")
    self.assertEqual(secondPayload["message"], "download already in progress")

  def testSecondTaskStaysQueuedUntilFirstTaskFinishes(self):
    startedUrls: list[str] = []
    firstTaskStarted = threading.Event()
    releaseFirstTask = threading.Event()

    def waitForStatus(client, taskId, expectedStatus, timeout=2.0):
      deadline = time.time() + timeout
      while time.time() < deadline:
        payload = client.get(f"/api/tasks/{taskId}").get_json()
        if payload["status"] == expectedStatus:
          return payload
        time.sleep(0.01)
      self.fail(f"task {taskId} did not reach status {expectedStatus}")

    def waitForHistoryStatus(historyStore, url, expectedStatus, timeout=2.0):
      deadline = time.time() + timeout
      while time.time() < deadline:
        record = historyStore.getByUrl(url)
        if record is not None and record["status"] == expectedStatus:
          return record
        time.sleep(0.01)
      self.fail(f"history {url} did not reach status {expectedStatus}")

    def fakeRunner(task, url, codec):
      startedUrls.append(url)
      task.setStage("downloading")
      task.setStatus("running")
      if len(startedUrls) == 1:
        firstTaskStarted.set()
        releaseFirstTask.wait(timeout=2.0)
      task.setResult([
        {
          "path": f"/downloads/{len(startedUrls)}.flac",
          "artist": "Example Artist",
          "album": "Example Album",
          "song": "Example Song",
        }
      ])
      task.setStage("completed")
      task.setStatus("completed")
      task.setProgress(100)

    app = createApp(
      runnerFactory=lambda: fakeRunner,
      dbPath=f"{self.tempDir.name}/queue-downloads.db"
    )
    app.config["TESTING"] = False
    client = app.test_client()
    historyStore = app.config["HISTORY_STORE"]

    first = client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/first/111"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    self.assertTrue(firstTaskStarted.wait(timeout=2.0))
    self.assertEqual(firstPayload["status"], "running")

    second = client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/second/222"
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 202)
    self.assertEqual(secondPayload["status"], "queued")
    self.assertEqual(len(startedUrls), 1)

    queuedTask = waitForStatus(client, secondPayload["taskId"], "queued")
    self.assertEqual(queuedTask["stage"], "queued")

    releaseFirstTask.set()

    waitForStatus(client, firstPayload["taskId"], "completed")
    completedSecondTask = waitForStatus(client, secondPayload["taskId"], "completed")
    waitForHistoryStatus(historyStore, "https://music.apple.com/cn/album/first/111", "completed")
    waitForHistoryStatus(historyStore, "https://music.apple.com/cn/album/second/222", "completed")

    self.assertEqual(len(startedUrls), 2)
    self.assertEqual(completedSecondTask["result"][0]["path"], "/downloads/2.flac")

  def testQueuedTaskCanBeCancelledBeforeItStarts(self):
    startedUrls: list[str] = []
    firstTaskStarted = threading.Event()
    releaseFirstTask = threading.Event()

    def waitForStatus(client, taskId, expectedStatus, timeout=2.0):
      deadline = time.time() + timeout
      while time.time() < deadline:
        payload = client.get(f"/api/tasks/{taskId}").get_json()
        if payload["status"] == expectedStatus:
          return payload
        time.sleep(0.01)
      self.fail(f"task {taskId} did not reach status {expectedStatus}")

    def fakeRunner(task, url, codec):
      startedUrls.append(url)
      task.setStage("downloading")
      task.setStatus("running")
      if len(startedUrls) == 1:
        firstTaskStarted.set()
        releaseFirstTask.wait(timeout=2.0)
      task.setResult([{
        "path": f"/downloads/{len(startedUrls)}.flac",
        "artist": "Example Artist",
        "album": "Example Album",
        "song": "Example Song",
      }])
      task.setStage("completed")
      task.setStatus("completed")
      task.setProgress(100)

    app = createApp(
      runnerFactory=lambda: fakeRunner,
      dbPath=f"{self.tempDir.name}/cancel-downloads.db"
    )
    app.config["TESTING"] = False
    client = app.test_client()
    historyStore = app.config["HISTORY_STORE"]

    first = client.post(
      "/api/downloads",
      data=json.dumps({"url": "https://music.apple.com/cn/album/first/111"}),
      content_type="application/json",
    )
    firstPayload = first.get_json()
    self.assertTrue(firstTaskStarted.wait(timeout=2.0))

    second = client.post(
      "/api/downloads",
      data=json.dumps({"url": "https://music.apple.com/cn/album/second/222"}),
      content_type="application/json",
    )
    secondPayload = second.get_json()
    self.assertEqual(secondPayload["status"], "queued")

    cancelResponse = client.post(f"/api/tasks/{secondPayload['taskId']}/cancel")
    cancelPayload = cancelResponse.get_json()

    self.assertEqual(cancelResponse.status_code, 200)
    self.assertTrue(cancelPayload["cancelled"])
    self.assertEqual(cancelPayload["task"]["status"], "cancelled")
    self.assertEqual(waitForStatus(client, secondPayload["taskId"], "cancelled")["stage"], "cancelled")
    self.assertEqual(historyStore.getByUrl("https://music.apple.com/cn/album/second/222")["status"], "cancelled")

    releaseFirstTask.set()
    waitForStatus(client, firstPayload["taskId"], "completed")
    time.sleep(0.05)

    self.assertEqual(startedUrls, ["https://music.apple.com/cn/album/first/111"])

  def testRecoverPendingHistoryRestoresRunningAndQueuedTasks(self):
    dbPath = f"{self.tempDir.name}/recover-downloads.db"
    historyStore = DownloadHistoryStore(dbPath)
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/recover-running/111",
      "recover-running-task",
      "alac",
      "telegram",
    )
    historyStore.saveQueued(
      "https://music.apple.com/cn/album/recover-queued/222",
      "recover-queued-task",
      "alac",
      "telegram",
    )
    startedUrls: list[str] = []
    firstTaskStarted = threading.Event()
    releaseFirstTask = threading.Event()

    def waitForHistoryStatus(store, url, expectedStatus, timeout=2.0):
      deadline = time.time() + timeout
      while time.time() < deadline:
        record = store.getByUrl(url)
        if record is not None and record["status"] == expectedStatus:
          return record
        time.sleep(0.01)
      self.fail(f"history {url} did not reach status {expectedStatus}")

    def recoveredRunner(task, url, codec):
      startedUrls.append(url)
      task.setStage("downloading")
      task.setStatus("running")
      if url.endswith("/recover-running/111"):
        firstTaskStarted.set()
        releaseFirstTask.wait(timeout=2.0)
      task.setResult([
        {
          "path": f"/downloads/{task.id}.flac",
          "artist": "Recovered Artist",
          "album": "Recovered Album",
          "song": "Recovered Song",
        }
      ])
      task.setStage("completed")
      task.setStatus("completed")
      task.setProgress(100)

    recoveredApp = createApp(
      runnerFactory=lambda: recoveredRunner,
      dbPath=dbPath,
    )
    recoveredClient = recoveredApp.test_client()
    recoveredHistoryStore = recoveredApp.config["HISTORY_STORE"]

    self.assertTrue(firstTaskStarted.wait(timeout=2.0))
    tasks = recoveredClient.get("/api/tasks").get_json()
    tasksById = {task["taskId"]: task for task in tasks}

    self.assertEqual(tasksById["recover-running-task"]["status"], "running")
    self.assertEqual(tasksById["recover-queued-task"]["status"], "queued")
    self.assertEqual(tasksById["recover-running-task"]["source"], "telegram")
    self.assertEqual(tasksById["recover-queued-task"]["source"], "telegram")

    releaseFirstTask.set()
    waitForHistoryStatus(
      recoveredHistoryStore,
      "https://music.apple.com/cn/album/recover-running/111",
      "completed",
    )
    waitForHistoryStatus(
      recoveredHistoryStore,
      "https://music.apple.com/cn/album/recover-queued/222",
      "completed",
    )

    self.assertEqual(startedUrls, [
      "https://music.apple.com/cn/album/recover-running/111",
      "https://music.apple.com/cn/album/recover-queued/222",
    ])

  def testRecoverPendingHistoryExpiresOldRunningTasks(self):
    dbPath = f"{self.tempDir.name}/recover-expired-downloads.db"
    configPath = Path(self.tempDir.name) / "recover-config.yaml"
    configPath.write_text("recover-pending-max-age-hours: 1\n", encoding="utf-8")
    historyStore = DownloadHistoryStore(dbPath)
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/recover-expired/333",
      "recover-expired-task",
      "alac",
      "telegram",
    )
    connection = sqlite3.connect(dbPath)
    try:
      connection.execute(
        "UPDATE downloads SET updated_at = '2000-01-01 00:00:00' WHERE url = ?",
        ("https://music.apple.com/cn/album/recover-expired/333",),
      )
      connection.commit()
    finally:
      connection.close()
    calls = []
    originalConfigPath = os.environ.get("WEBAPP_CONFIG_PATH")
    try:
      os.environ["WEBAPP_CONFIG_PATH"] = str(configPath)
      recoveredApp = createApp(
        runnerFactory=lambda: lambda task, url, codec: calls.append(url),
        dbPath=dbPath,
      )
    finally:
      if originalConfigPath is None:
        os.environ.pop("WEBAPP_CONFIG_PATH", None)
      else:
        os.environ["WEBAPP_CONFIG_PATH"] = originalConfigPath

    record = recoveredApp.config["HISTORY_STORE"].getByUrl("https://music.apple.com/cn/album/recover-expired/333")

    self.assertEqual(calls, [])
    self.assertIsNotNone(record)
    self.assertEqual(record["status"], "failed")
    self.assertIn("expired", record["error"])

  def testDownloadShortcutReplacesStaleRunningTask(self):
    self.runner.autoComplete = False
    first = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    firstPayload = first.get_json()

    restartedResultPath = Path(self.tempDir.name) / "downloads" / "ALAC" / "restarted-example.flac"
    restartedResultPath.parent.mkdir(parents=True, exist_ok=True)
    restartedResultPath.write_text("fake flac", encoding="utf-8")
    restartedRunner = FakeRunner(str(restartedResultPath))
    restartedApp = createApp(
      runnerFactory=lambda: restartedRunner,
      dbPath=f"{self.tempDir.name}/downloads.db",
      recoverPending=False,
    )
    restartedApp.config["TESTING"] = True
    restartedClient = restartedApp.test_client()

    second = restartedClient.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    secondPayload = second.get_json()

    self.assertEqual(second.status_code, 202)
    self.assertEqual(len(restartedRunner.calls), 1)
    self.assertNotEqual(secondPayload["taskId"], firstPayload["taskId"])

  def testRetryFailedTasksReturnsZeroWhenNoFailures(self):
    response = self.client.post("/api/tasks/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedCompletedCount"], 0)
    self.assertEqual(payload["skippedRunningCount"], 0)

  def testRetryFailedTasksSkipsCompletedUrls(self):
    completed = self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    completedPayload = completed.get_json()
    taskStore = self.client.application.config["TASK_STORE"]
    failedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347",
      "alac",
      "telegram"
    )
    failedTask.setStage("failed")
    failedTask.setStatus("failed")
    failedTask.setError("download failed")

    response = self.client.post("/api/tasks/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedCompletedCount"], 1)
    self.assertEqual(payload["skippedCompletedUrls"], [failedTask.url])
    self.assertEqual(payload["retriedTaskIds"], [])
    self.assertEqual(completedPayload["taskId"], self.client.get(f"/api/tasks/{completedPayload['taskId']}").get_json()["taskId"])

  def testRetryFailedTasksSkipsUrlsThatAreAlreadyRunning(self):
    taskStore = self.client.application.config["TASK_STORE"]
    failedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/happier-than-ever/1564530719",
      "alac",
      "web"
    )
    failedTask.setStage("failed")
    failedTask.setStatus("failed")
    failedTask.setError("download failed")

    runningTask = taskStore.createTask(
      "https://music.apple.com/cn/album/happier-than-ever/1564530719",
      "alac",
      "telegram"
    )
    runningTask.setStage("queued")
    runningTask.setStatus("running")

    response = self.client.post("/api/tasks/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedRunningCount"], 1)
    self.assertEqual(payload["skippedRunningUrls"], [failedTask.url])
    self.assertEqual(len(self.runner.calls), 0)

  def testRetryFailedTasksDeduplicatesFailedUrlsAndPreservesSource(self):
    taskStore = self.client.application.config["TASK_STORE"]
    firstFailedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/hit-me-hard-and-soft/1739659134",
      "alac",
      "telegram"
    )
    firstFailedTask.setStage("failed")
    firstFailedTask.setStatus("failed")
    firstFailedTask.setError("download failed")

    secondFailedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/hit-me-hard-and-soft/1739659134",
      "alac",
      "web"
    )
    secondFailedTask.setStage("failed")
    secondFailedTask.setStatus("failed")
    secondFailedTask.setError("download failed")

    response = self.client.post("/api/tasks/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 1)
    self.assertEqual(payload["skippedCompletedCount"], 0)
    self.assertEqual(payload["skippedRunningCount"], 0)
    self.assertEqual(len(self.runner.calls), 1)

    taskResponse = self.client.get(f"/api/tasks/{payload['retriedTaskIds'][0]}")
    taskPayload = taskResponse.get_json()
    self.assertEqual(taskPayload["source"], "telegram")

  def testRetryFailedTasksSkipsUrlThatIsAlreadyQueued(self):
    taskStore = self.client.application.config["TASK_STORE"]
    failedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/queued-retry/777",
      "alac",
      "telegram"
    )
    failedTask.setStage("failed")
    failedTask.setStatus("failed")
    failedTask.setError("download failed")

    queuedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/queued-retry/777",
      "alac",
      "telegram"
    )
    queuedTask.setStage("queued")
    queuedTask.setStatus("queued")

    response = self.client.post("/api/tasks/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedRunningCount"], 1)
    self.assertEqual(payload["skippedRunningUrls"], [failedTask.url])
    self.assertEqual(len(self.runner.calls), 0)

  def testRetryFailedTasksPreservesOriginalCodec(self):
    taskStore = self.client.application.config["TASK_STORE"]
    failedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/aac-retry/888",
      "aac",
      "telegram"
    )
    failedTask.setStage("failed")
    failedTask.setStatus("failed")
    failedTask.setError("download failed")

    response = self.client.post("/api/tasks/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 1)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(self.runner.calls[0][2], "aac")

  def testHistoryRetryFailedSkipsUrlThatIsAlreadyQueued(self):
    taskStore = self.client.application.config["TASK_STORE"]
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/history-queued/555",
      "old-task",
      "alac",
      "error"
    )
    queuedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/history-queued/555",
      "alac",
      "web"
    )
    queuedTask.setStage("queued")
    queuedTask.setStatus("queued")

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedRunningCount"], 1)
    self.assertIn("https://music.apple.com/cn/album/history-queued/555", payload["skippedRunningUrls"])

  def testHistoryRetryFailedPreservesStoredCodec(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/history-aac/556",
      "old-task",
      "aac",
      "error"
    )

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 1)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(self.runner.calls[0][2], "aac")

  def testPipelineCallsPostProcessing(self):
    scriptCalls: list[list[str]] = []
    sourcePath = f"{self.tempDir.name}/example.m4a"
    outputPath = f"{self.tempDir.name}/example.flac"
    with open(sourcePath, "wb") as handle:
      handle.write(b"fake m4a")
    with open(outputPath, "wb") as handle:
      handle.write(b"fake flac")

    def fakeDownloadRunner(task, url, codec):
      self.runner.calls.append((task.id, url, codec))
      task.setResult([
        {
          "path": sourcePath,
          "artist": "Example Artist",
          "album": "Example Album",
          "song": "Example Song"
        }
      ])
      task.setStatus("completed")

    class RecordingPipelineRunner(PipelineRunner):
      def __init__(self, downloadRunner):
        super().__init__(downloadRunner)

      def _runScript(self, task, command):
        scriptCalls.append(command)
        if command[1].endswith("convert_to_flac.py"):
          return outputPath
        return ""

    pipelineRunner = RecordingPipelineRunner(fakeDownloadRunner)

    app = createApp(
      runnerFactory=lambda: pipelineRunner,
      dbPath=f"{self.tempDir.name}/downloads.db"
    )
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 202)
    self.assertEqual(len(scriptCalls), 2)
    self.assertEqual(scriptCalls[0][:3], ["python", "-m", "tools.convert_to_flac"])
    self.assertTrue(scriptCalls[0][3].endswith("example.m4a"))
    self.assertEqual(scriptCalls[1][:3], ["python", "-m", "tools.build_nfo"])
    self.assertEqual(scriptCalls[1][3], self.tempDir.name)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(self.runner.calls[0][2], "alac")

  def testPipelineRemovesOriginalWhenConfigDisablesKeepingIt(self):
    scriptCalls: list[list[str]] = []
    sourcePath = f"{self.tempDir.name}/example.m4a"
    outputPath = f"{self.tempDir.name}/example.flac"
    configPath = f"{self.tempDir.name}/config.yaml"
    with open(sourcePath, "wb") as handle:
      handle.write(b"fake m4a")
    with open(outputPath, "wb") as handle:
      handle.write(b"fake flac")
    with open(configPath, "w", encoding="utf-8") as handle:
      handle.write('convert-keep-original: false\n')

    def fakeDownloadRunner(task, url, codec):
      task.setResult([
        {
          "path": sourcePath,
          "artist": "Example Artist",
          "album": "Example Album",
          "song": "Example Song"
        }
      ])
      task.setStatus("completed")

    class RecordingPipelineRunner(PipelineRunner):
      def __init__(self, downloadRunner):
        super().__init__(downloadRunner)

      def _runScript(self, task, command):
        scriptCalls.append(command)
        if command[1].endswith("convert_to_flac.py"):
          return outputPath
        return ""

    originalConfigPath = os.environ.get("WEBAPP_CONFIG_PATH")
    try:
      os.environ["WEBAPP_CONFIG_PATH"] = configPath
      app = createApp(
        runnerFactory=lambda: RecordingPipelineRunner(fakeDownloadRunner),
        dbPath=f"{self.tempDir.name}/downloads.db"
      )
      app.config["TESTING"] = True
      client = app.test_client()

      response = client.post(
        "/api/downloads",
        data=json.dumps({
          "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
        }),
        content_type="application/json"
      )

      self.assertEqual(response.status_code, 202)
      self.assertIn("--remove-original", scriptCalls[0])
    finally:
      if originalConfigPath is None:
        os.environ.pop("WEBAPP_CONFIG_PATH", None)
      else:
        os.environ["WEBAPP_CONFIG_PATH"] = originalConfigPath

  def testPipelineUsesDerivedFlacPathForNfo(self):
    sourcePath = f"{self.tempDir.name}/example.m4a"
    derivedFlacPath = f"{self.tempDir.name}/example.flac"
    with open(sourcePath, "wb") as handle:
      handle.write(b"fake m4a")
    with open(derivedFlacPath, "wb") as handle:
      handle.write(b"fake flac")

    def fakeDownloadRunner(task, url, codec):
      task.setResult([
        {
          "path": sourcePath,
          "artist": "Example Artist",
          "album": "Example Album",
          "song": "Example Song"
        }
      ])
      task.setStatus("completed")

    scriptCalls: list[list[str]] = []

    class LoggingPipelineRunner(PipelineRunner):
      def __init__(self, downloadRunner):
        super().__init__(downloadRunner)

      def _runScript(self, task, command):
        scriptCalls.append(command)
        if command[:3] == ["python", "-m", "tools.convert_to_flac"]:
          return "2026-04-29 20:45:31 | INFO | 所有文件转换成功!"
        return ""

    app = createApp(
      runnerFactory=lambda: LoggingPipelineRunner(fakeDownloadRunner),
      dbPath=f"{self.tempDir.name}/downloads.db"
    )
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    payload = response.get_json()

    taskResponse = client.get(f"/api/tasks/{payload['taskId']}")
    taskPayload = taskResponse.get_json()

    self.assertEqual(taskPayload["status"], "completed")
    self.assertEqual(Path(taskPayload["result"][0]["path"]), Path(derivedFlacPath))
    self.assertEqual(scriptCalls[1][3], self.tempDir.name)

  def testPipelineFailsWhenSourceFileMissing(self):
    def fakeDownloadRunner(task, url, codec):
      task.setResult([
        {
          "path": "/downloads/ALAC/missing-track.m4a",
          "artist": "Example Artist",
          "album": "Example Album",
          "song": "Example Song"
        }
      ])
      task.setStatus("completed")

    app = createApp(
      runnerFactory=lambda: PipelineRunner(fakeDownloadRunner),
      dbPath=f"{self.tempDir.name}/downloads.db"
    )
    app.config["TESTING"] = True
    client = app.test_client()

    response = client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )
    payload = response.get_json()

    taskResponse = client.get(f"/api/tasks/{payload['taskId']}")
    taskPayload = taskResponse.get_json()

    self.assertEqual(taskPayload["status"], "failed")
    self.assertIn("Source file not accessible", taskPayload["error"])

  def testHistoryReturnsDownloadRecords(self):
    self.client.post(
      "/api/downloads",
      data=json.dumps({
        "url": "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347"
      }),
      content_type="application/json"
    )

    response = self.client.get("/api/history")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertIsInstance(payload, list)
    self.assertEqual(len(payload), 1)
    self.assertEqual(payload[0]["url"], "https://music.apple.com/cn/album/intro-hit-me-hard-and-soft-tour-single/1895089347")
    self.assertEqual(payload[0]["status"], "completed")
    self.assertIn("created_at", payload[0])
    self.assertIn("updated_at", payload[0])

  def testHistoryRetryFailedRetriesFailedNeverCompletedUrl(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/never-completed/111",
      "old-task-id",
      "alac",
      "download error"
    )

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 1)
    self.assertEqual(len(payload["retriedTaskIds"]), 1)
    self.assertEqual(payload["skippedCompletedCount"], 0)
    self.assertEqual(len(self.runner.calls), 1)

  def testHistoryRetryFailedSkipsUrlThatHasCompleted(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    completedPath = Path(self.tempDir.name) / "completed-example.flac"
    completedPath.write_text("fake flac", encoding="utf-8")
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/has-success/222",
      "running-task",
      "alac"
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/has-success/222",
      "completed-task",
      "alac",
      [{"path": str(completedPath)}]
    )
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/has-success/222",
      "failed-task",
      "alac",
      "later error"
    )

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedCompletedCount"], 1)
    self.assertIn("https://music.apple.com/cn/album/has-success/222", payload["skippedCompletedUrls"])

  def testHistoryRetryFailedSkipsDifferentUrlForCompletedAlbumId(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    completedPath = Path(self.tempDir.name) / "completed-album-id-example.flac"
    completedPath.write_text("fake flac", encoding="utf-8")
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/new-slug/999",
      "completed-task",
      "alac",
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/new-slug/999",
      "completed-task",
      "alac",
      [{"path": str(completedPath)}],
    )
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/old-slug/999?l=en",
      "failed-task",
      "alac",
      "old failure",
    )

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedCompletedCount"], 1)
    self.assertIn("https://music.apple.com/cn/album/old-slug/999?l=en", payload["skippedCompletedUrls"])
    self.assertEqual(len(self.runner.calls), 0)

  def testHistoryRetryFailedDeduplicatesUrls(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/dup-url/333",
      "task-a",
      "alac",
      "first error"
    )
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/dup-url/333",
      "task-b",
      "alac",
      "second error"
    )

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 1)
    self.assertEqual(len(self.runner.calls), 1)

  def testHistoryRetryFailedSkipsUrlWithRunningTask(self):
    taskStore = self.client.application.config["TASK_STORE"]
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/running-url/444",
      "old-task",
      "alac",
      "error"
    )
    runningTask = taskStore.createTask(
      "https://music.apple.com/cn/album/running-url/444",
      "alac",
      "web"
    )
    runningTask.setStatus("running")

    response = self.client.post("/api/history/retry-failed")
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["retriedCount"], 0)
    self.assertEqual(payload["skippedRunningCount"], 1)
    self.assertIn("https://music.apple.com/cn/album/running-url/444", payload["skippedRunningUrls"])

  def testHistorySingleRetryAllowsFailedNeverCompletedUrl(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/single-retry/111",
      "task-single",
      "alac",
      "download failed"
    )

    response = self.client.post(
      "/api/history/retry",
      data=json.dumps({"url": "https://music.apple.com/cn/album/single-retry/111"}),
      content_type="application/json"
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 202)
    self.assertEqual(payload["status"], "running")
    self.assertEqual(len(self.runner.calls), 1)

  def testHistorySingleRetryRejectsUrlThatIsAlreadyQueued(self):
    taskStore = self.client.application.config["TASK_STORE"]
    historyStore = self.client.application.config["HISTORY_STORE"]
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/single-queued/333",
      "task-single",
      "alac",
      "download failed"
    )
    queuedTask = taskStore.createTask(
      "https://music.apple.com/cn/album/single-queued/333",
      "alac",
      "web"
    )
    queuedTask.setStage("queued")
    queuedTask.setStatus("queued")

    response = self.client.post(
      "/api/history/retry",
      data=json.dumps({"url": "https://music.apple.com/cn/album/single-queued/333"}),
      content_type="application/json"
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 409)
    self.assertEqual(payload["error"], "a task for this URL is already queued")
    self.assertEqual(len(self.runner.calls), 0)

  def testHistorySingleRetryRejectsUrlThatHasCompletedBefore(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    completedPath = Path(self.tempDir.name) / "single-completed-example.flac"
    completedPath.write_text("fake flac", encoding="utf-8")
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/single-retry-completed/222",
      "running-task",
      "alac"
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/single-retry-completed/222",
      "completed-task",
      "alac",
      [{"path": str(completedPath)}]
    )
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/single-retry-completed/222",
      "failed-task",
      "alac",
      "later failure"
    )

    response = self.client.post(
      "/api/history/retry",
      data=json.dumps({"url": "https://music.apple.com/cn/album/single-retry-completed/222"}),
      content_type="application/json"
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 400)
    self.assertEqual(len(self.runner.calls), 0)

  def testHistorySingleRetryRejectsDifferentUrlForCompletedAlbumId(self):
    historyStore = self.client.application.config["HISTORY_STORE"]
    completedPath = Path(self.tempDir.name) / "single-completed-album-id.flac"
    completedPath.write_text("fake flac", encoding="utf-8")
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/new-single-slug/999",
      "completed-task",
      "alac",
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/new-single-slug/999",
      "completed-task",
      "alac",
      [{"path": str(completedPath)}],
    )
    failedUrl = "https://music.apple.com/cn/album/old-single-slug/999?l=en"
    historyStore.saveFailed(
      failedUrl,
      "failed-task",
      "alac",
      "old failure",
    )

    response = self.client.post(
      "/api/history/retry",
      data=json.dumps({"url": failedUrl}),
      content_type="application/json"
    )

    self.assertEqual(response.status_code, 400)
    self.assertEqual(len(self.runner.calls), 0)


  def testSubscriptionSearchReturnsArtistResults(self):
    fakeAppleMusic = FakeAppleMusicClient()
    self.client.application.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic

    response = self.client.post(
      "/api/subscriptions/search",
      data=json.dumps({"term": "Example"}),
      content_type="application/json",
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["results"][0]["artistId"], "12345")
    self.assertEqual(payload["results"][0]["artistName"], "Example Artist")
    self.assertEqual(payload["results"][0]["artistArtworkUrl"], "https://is1-ssl.mzstatic.com/image/thumb/artist-example/512x512bb.jpg")

  def testFormatArtworkUrlSubstitutesTemplateAndRejectsUnsafeUrls(self):
    self.assertEqual(
      formatArtworkUrl({"url": "https://is1-ssl.mzstatic.com/image/thumb/demo/{w}x{h}bb.{f}"}, 256),
      "https://is1-ssl.mzstatic.com/image/thumb/demo/256x256bb.jpg",
    )
    self.assertEqual(formatArtworkUrl({"url": "javascript:alert(1)"}), "")
    self.assertEqual(formatArtworkUrl({"url": "https://[bad"}), "")
    self.assertEqual(formatArtworkUrl(None), "")

  def testLegacySubscriptionMigrationDefaultsPolicyToConfirm(self):
    dbPath = Path(self.tempDir.name) / "legacy-subscriptions.db"
    connection = sqlite3.connect(dbPath)
    try:
      connection.execute(
        """
        CREATE TABLE artist_subscriptions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          artist_id TEXT NOT NULL,
          storefront TEXT NOT NULL,
          artist_name TEXT NOT NULL,
          artist_url TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
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
        INSERT INTO artist_subscriptions (artist_id, storefront, artist_name, artist_url)
        VALUES ('12345', 'cn', 'Example Artist', 'https://music.apple.com/cn/artist/example/12345')
        """
      )
      connection.execute(
        """
        CREATE TABLE subscription_seen_albums (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subscription_id INTEGER NOT NULL,
          album_id TEXT NOT NULL,
          album_url TEXT NOT NULL,
          album_name TEXT NOT NULL DEFAULT '',
          release_date TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'seen',
          task_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(subscription_id, album_id)
        )
        """
      )
      connection.execute(
        """
        INSERT INTO subscription_seen_albums (subscription_id, album_id, album_url, album_name, release_date)
        VALUES (1, 'legacy-album', 'https://music.apple.com/cn/album/legacy/1', 'Legacy Album', '2024-01-01')
        """
      )
      connection.commit()
    finally:
      connection.close()

    store = ArtistSubscriptionStore(str(dbPath))
    subscriptions = store.listAll()

    self.assertEqual(subscriptions[0]["newAlbumPolicy"], "confirm")
    self.assertEqual(subscriptions[0]["artistArtworkUrl"], "")
    self.assertEqual(subscriptions[0]["recentAlbums"][0]["artworkUrl"], "")

  def testSubscriptionArtworkIsNotClearedByMissingArtworkRefresh(self):
    subscriptionStore = self.client.application.config["SUBSCRIPTION_STORE"]
    artist = AppleMusicArtist(
      artistId="artwork-refresh",
      storefront="cn",
      name="Artwork Refresh Artist",
      url="https://music.apple.com/cn/artist/artwork-refresh/123",
      artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/artist-refresh/512x512bb.jpg",
    )
    subscription, _created = subscriptionStore.createOrEnable(artist)
    subscriptionId = int(subscription["id"])

    subscriptionStore.createOrEnable(AppleMusicArtist(
      artistId="artwork-refresh",
      storefront="cn",
      name="Artwork Refresh Artist",
      url="https://music.apple.com/cn/artist/artwork-refresh/123",
      artworkUrl="",
    ))
    self.assertEqual(
      subscriptionStore.get(subscriptionId)["artistArtworkUrl"],
      "https://is1-ssl.mzstatic.com/image/thumb/artist-refresh/512x512bb.jpg",
    )

    metadataAlbum = AppleMusicAlbum(
      albumId="metadata-artwork",
      name="Metadata Artwork",
      url="https://music.apple.com/cn/album/metadata-artwork/456",
      releaseDate="2026-01-01",
      artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/metadata-album/512x512bb.jpg",
    )
    subscriptionStore.upsertSeenAlbumMetadata(subscriptionId, metadataAlbum)
    subscriptionStore.upsertSeenAlbumMetadata(subscriptionId, AppleMusicAlbum(
      albumId="metadata-artwork",
      name="Metadata Artwork",
      url="https://music.apple.com/cn/album/metadata-artwork/456",
      releaseDate="2026-01-01",
      artworkUrl="",
    ))
    self.assertEqual(
      subscriptionStore.getSeenAlbum(subscriptionId, "metadata-artwork")["artworkUrl"],
      "https://is1-ssl.mzstatic.com/image/thumb/metadata-album/512x512bb.jpg",
    )

    upsertAlbum = AppleMusicAlbum(
      albumId="upsert-artwork",
      name="Upsert Artwork",
      url="https://music.apple.com/cn/album/upsert-artwork/789",
      releaseDate="2026-02-01",
      artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/upsert-album/512x512bb.jpg",
    )
    subscriptionStore.upsertSeenAlbum(subscriptionId, upsertAlbum, status="seen")
    subscriptionStore.upsertSeenAlbum(subscriptionId, AppleMusicAlbum(
      albumId="upsert-artwork",
      name="Upsert Artwork",
      url="https://music.apple.com/cn/album/upsert-artwork/789",
      releaseDate="2026-02-01",
      artworkUrl="",
    ), status="seen")
    self.assertEqual(
      subscriptionStore.getSeenAlbum(subscriptionId, "upsert-artwork")["artworkUrl"],
      "https://is1-ssl.mzstatic.com/image/thumb/upsert-album/512x512bb.jpg",
    )

  def testCreateSubscriptionScansAndPendsMissingAlbums(self):
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = [
      AppleMusicAlbum(
        albumId="111",
        name="Already Done",
        url="https://music.apple.com/cn/album/already-done/111",
        releaseDate="2024-01-01",
        artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/album-111/512x512bb.jpg",
      ),
      AppleMusicAlbum(
        albumId="222",
        name="Failed Before",
        url="https://music.apple.com/cn/album/failed-before/222",
        releaseDate="2024-02-01",
        artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/album-222/512x512bb.jpg",
      ),
      AppleMusicAlbum(
        albumId="333",
        name="New Album",
        url="https://music.apple.com/cn/album/new-album/333",
        releaseDate="2024-03-01",
        artworkUrl="https://is1-ssl.mzstatic.com/image/thumb/album-333/512x512bb.jpg",
      ),
    ]
    self.client.application.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic
    historyStore = self.client.application.config["HISTORY_STORE"]
    completedPath = Path(self.tempDir.name) / "completed-subscription.flac"
    completedPath.write_text("fake flac", encoding="utf-8")
    historyStore.saveRunning(
      "https://music.apple.com/cn/album/old-slug/111?l=en",
      "completed-task",
      "alac",
      "web",
    )
    historyStore.saveCompleted(
      "https://music.apple.com/cn/album/old-slug/111?l=en",
      "completed-task",
      "alac",
      [{"path": str(completedPath)}],
    )
    historyStore.saveFailed(
      "https://music.apple.com/cn/album/failed-before/222",
      "failed-task",
      "alac",
      "error",
    )

    response = self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 201)
    self.assertEqual(payload["scan"]["foundCount"], 3)
    self.assertEqual(payload["scan"]["queuedCount"], 0)
    self.assertEqual(payload["scan"]["pendingCount"], 2)
    self.assertEqual(payload["scan"]["skippedCompletedCount"], 1)
    self.assertEqual(len(self.runner.calls), 0)

    tasks = self.client.get("/api/tasks").get_json()
    self.assertEqual(tasks, [])

    connection = sqlite3.connect(historyStore.dbPath)
    try:
      rows = connection.execute(
        "SELECT album_id, status, user_state, detected_status, artwork_url FROM subscription_seen_albums ORDER BY album_id"
      ).fetchall()
    finally:
      connection.close()
    self.assertEqual(
      {albumId: (status, userState, detectedStatus, artworkUrl) for albumId, status, userState, detectedStatus, artworkUrl in rows},
      {
        "111": ("completed", "subscribed", "completed", "https://is1-ssl.mzstatic.com/image/thumb/album-111/512x512bb.jpg"),
        "222": ("failed", "pending", "failed_history", "https://is1-ssl.mzstatic.com/image/thumb/album-222/512x512bb.jpg"),
        "333": ("seen", "pending", "missing", "https://is1-ssl.mzstatic.com/image/thumb/album-333/512x512bb.jpg"),
      },
    )

    subscriptions = self.client.get("/api/subscriptions").get_json()
    self.assertEqual(subscriptions[0]["artistArtworkUrl"], "https://is1-ssl.mzstatic.com/image/thumb/artist-example/512x512bb.jpg")
    recentAlbums = {
      album["albumId"]: album
      for album in subscriptions[0]["recentAlbums"]
    }
    self.assertEqual(recentAlbums["111"]["albumName"], "Already Done")
    self.assertEqual(recentAlbums["111"]["status"], "completed")
    self.assertEqual(recentAlbums["222"]["userState"], "pending")
    self.assertEqual(recentAlbums["222"]["detectedStatus"], "failed_history")
    self.assertEqual(recentAlbums["333"]["albumName"], "New Album")
    self.assertEqual(recentAlbums["333"]["releaseDate"], "2024-03-01")
    self.assertEqual(recentAlbums["333"]["artworkUrl"], "https://is1-ssl.mzstatic.com/image/thumb/album-333/512x512bb.jpg")

  def testSubscriptionAutoPolicyQueuesAlbumsAfterDetection(self):
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = [
      AppleMusicAlbum(
        albumId="444",
        name="Auto Album",
        url="https://music.apple.com/cn/album/auto-album/444",
      )
    ]
    self.client.application.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic

    createResponse = self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )
    subscriptionId = createResponse.get_json()["subscription"]["id"]
    policyResponse = self.client.patch(
      f"/api/subscriptions/{subscriptionId}",
      data=json.dumps({"newAlbumPolicy": "auto"}),
      content_type="application/json",
    )
    scanResponse = self.client.post(f"/api/subscriptions/{subscriptionId}/scan")
    scanPayload = scanResponse.get_json()

    self.assertEqual(policyResponse.status_code, 200)
    self.assertEqual(scanResponse.status_code, 200)
    self.assertEqual(scanPayload["queuedCount"], 1)
    self.assertEqual(len(self.runner.calls), 1)

  def testSubscriptionIgnoredAndImportedAlbumsPersistAcrossScans(self):
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = [
      AppleMusicAlbum(
        albumId="777",
        name="Ignored Album",
        url="https://music.apple.com/cn/album/ignored-album/777",
      ),
      AppleMusicAlbum(
        albumId="888",
        name="Imported Album",
        url="https://music.apple.com/cn/album/imported-album/888",
      ),
    ]
    self.client.application.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic

    createResponse = self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )
    subscriptionId = createResponse.get_json()["subscription"]["id"]
    ignoreResponse = self.client.post(
      f"/api/subscriptions/{subscriptionId}/albums/actions",
      data=json.dumps({"albumIds": ["777"], "action": "ignore"}),
      content_type="application/json",
    )
    importedResponse = self.client.post(
      f"/api/subscriptions/{subscriptionId}/albums/actions",
      data=json.dumps({"albumIds": ["888"], "action": "mark_imported"}),
      content_type="application/json",
    )
    scanResponse = self.client.post(f"/api/subscriptions/{subscriptionId}/scan")
    scanPayload = scanResponse.get_json()

    self.assertEqual(ignoreResponse.status_code, 200)
    self.assertEqual(importedResponse.status_code, 200)
    self.assertEqual(scanPayload["queuedCount"], 0)
    self.assertEqual(scanPayload["skippedIgnoredCount"], 1)
    self.assertEqual(scanPayload["skippedImportedCount"], 1)
    subscriptions = self.client.get("/api/subscriptions").get_json()
    recentAlbums = {album["albumId"]: album for album in subscriptions[0]["recentAlbums"]}
    self.assertEqual(recentAlbums["777"]["userState"], "ignored")
    self.assertEqual(recentAlbums["888"]["userState"], "imported")

  def testSubscriptionAlbumCanBeMarkedCompletedManually(self):
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = [
      AppleMusicAlbum(
        albumId="999",
        name="Already Local Album",
        url="https://music.apple.com/cn/album/already-local/999",
      )
    ]
    app = self.client.application
    app.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic

    createResponse = self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )
    subscriptionId = createResponse.get_json()["subscription"]["id"]
    actionResponse = self.client.post(
      f"/api/subscriptions/{subscriptionId}/albums/actions",
      data=json.dumps({"albumIds": ["999"], "action": "mark_completed"}),
      content_type="application/json",
    )
    subscriptions = self.client.get("/api/subscriptions").get_json()
    recentAlbums = {album["albumId"]: album for album in subscriptions[0]["recentAlbums"]}

    self.assertEqual(createResponse.status_code, 201)
    self.assertEqual(actionResponse.status_code, 200)
    self.assertEqual(actionResponse.get_json()["updatedCount"], 1)
    self.assertEqual(recentAlbums["999"]["status"], "completed")
    self.assertEqual(recentAlbums["999"]["detectedStatus"], "completed")
    self.assertEqual(recentAlbums["999"]["userState"], "subscribed")
    self.assertIsNone(app.config["HISTORY_STORE"].getByUrl("https://music.apple.com/cn/album/already-local/999"))

  def testSubscriptionScanSkipsActiveAlbumByAlbumId(self):
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = [
      AppleMusicAlbum(
        albumId="555",
        name="Active Album",
        url="https://music.apple.com/cn/album/new-active-slug/555",
      )
    ]
    self.client.application.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic
    taskStore = self.client.application.config["TASK_STORE"]
    activeTask = taskStore.createTask(
      "https://music.apple.com/cn/album/old-active-slug/555?l=en",
      "alac",
      "telegram",
    )
    activeTask.setStage("downloading")
    activeTask.setStatus("running")

    response = self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )
    payload = response.get_json()

    self.assertEqual(response.status_code, 201)
    self.assertEqual(payload["scan"]["queuedCount"], 0)
    self.assertEqual(payload["scan"]["skippedActiveCount"], 1)
    self.assertEqual(len(self.runner.calls), 0)

  def testSubscriptionSeenAlbumStatusUpdatesWhenTaskFails(self):
    class FailingRunner:
      def __init__(self):
        self.calls = []

      def __call__(self, task, url, codec):
        self.calls.append((task.id, url, codec))
        task.setStage("failed")
        task.setStatus("failed")
        task.setError("subscription failure")

    failingRunner = FailingRunner()
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = [
      AppleMusicAlbum(
        albumId="666",
        name="Failing Album",
        url="https://music.apple.com/cn/album/failing-album/666",
      )
    ]
    app = self.client.application
    app.config["RUNNER_FACTORY"] = lambda: failingRunner
    app.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic

    response = self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )
    subscriptionId = response.get_json()["subscription"]["id"]
    actionResponse = self.client.post(
      f"/api/subscriptions/{subscriptionId}/albums/actions",
      data=json.dumps({"albumIds": ["666"], "action": "download"}),
      content_type="application/json",
    )

    self.assertEqual(response.status_code, 201)
    self.assertEqual(actionResponse.status_code, 200)
    self.assertEqual(actionResponse.get_json()["queuedCount"], 1)
    connection = sqlite3.connect(app.config["SUBSCRIPTION_STORE"].dbPath)
    try:
      row = connection.execute(
        "SELECT status, task_id, user_state, detected_status FROM subscription_seen_albums WHERE album_id = '666'"
      ).fetchone()
    finally:
      connection.close()
    self.assertIsNotNone(row)
    self.assertEqual(row[0], "failed")
    self.assertTrue(row[1])
    self.assertEqual(row[2], "subscribed")
    self.assertEqual(row[3], "failed_history")

  def testSubscriptionListReturnsAllAlbumsSortedByReleaseDate(self):
    subscriptionStore = self.client.application.config["SUBSCRIPTION_STORE"]
    artist = AppleMusicArtist(
      artistId="release-sort",
      storefront="cn",
      name="Release Sort Artist",
      url="https://music.apple.com/cn/artist/release-sort/123",
    )
    subscription, _created = subscriptionStore.createOrEnable(artist)
    albums = [
      AppleMusicAlbum(albumId="001", name="Oldest", url="https://music.apple.com/cn/album/oldest/001", releaseDate="2019-01-01"),
      AppleMusicAlbum(albumId="002", name="No Date", url="https://music.apple.com/cn/album/no-date/002", releaseDate=""),
      AppleMusicAlbum(albumId="003", name="Newest", url="https://music.apple.com/cn/album/newest/003", releaseDate="2026-06-05"),
      AppleMusicAlbum(albumId="004", name="Middle", url="https://music.apple.com/cn/album/middle/004", releaseDate="2024-08-10"),
      AppleMusicAlbum(albumId="005", name="Another Old", url="https://music.apple.com/cn/album/another-old/005", releaseDate="2020-05-20"),
      AppleMusicAlbum(albumId="006", name="Recent", url="https://music.apple.com/cn/album/recent/006", releaseDate="2025-12-31"),
      AppleMusicAlbum(albumId="007", name="Older", url="https://music.apple.com/cn/album/older/007", releaseDate="2021-03-15"),
    ]
    for album in albums:
      subscriptionStore.upsertSeenAlbum(int(subscription["id"]), album, status="seen")

    response = self.client.get("/api/subscriptions")
    payload = response.get_json()
    releaseSortSubscription = next(item for item in payload if item["artistId"] == "release-sort")
    albumIds = [album["albumId"] for album in releaseSortSubscription["recentAlbums"]]

    self.assertEqual(response.status_code, 200)
    self.assertEqual(len(albumIds), 7)
    self.assertEqual(albumIds, ["003", "006", "004", "007", "005", "001", "002"])

  def testSubscriptionListAndDeleteByArtistId(self):
    fakeAppleMusic = FakeAppleMusicClient()
    fakeAppleMusic.albums = []
    self.client.application.config["APPLE_MUSIC_CLIENT_FACTORY"] = lambda: fakeAppleMusic

    self.client.post(
      "/api/subscriptions",
      data=json.dumps({"artistUrl": "https://music.apple.com/cn/artist/example-artist/12345"}),
      content_type="application/json",
    )

    listResponse = self.client.get("/api/subscriptions")
    listPayload = listResponse.get_json()
    deleteResponse = self.client.delete("/api/subscriptions/by-artist/12345")
    afterDelete = self.client.get("/api/subscriptions").get_json()

    self.assertEqual(listResponse.status_code, 200)
    self.assertEqual(len(listPayload), 1)
    self.assertEqual(deleteResponse.status_code, 200)
    self.assertEqual(afterDelete, [])


class DownloaderRunnerTest(unittest.TestCase):
  def testIgnoresRetryPromptAfterCompletedSummary(self):
    task = DownloadTask(
      id="task-id",
      url="https://music.apple.com/cn/album/example/123",
      codec="alac"
    )

    updateTaskFromLine(task, "=======  [✔ ] Completed: 1/1  |  [⚠ ] Warnings: 0  |  [✖ ] Errors: 1  =======")
    updateTaskFromLine(task, "Error detected, press Enter to try again...")

    self.assertEqual(task.status, "completed")
    self.assertEqual(task.stage, "completed")
    self.assertEqual(task.progress, 100)

  def testMarksTaskFailedWhenProcessExitsNonZeroAfterNetworkError(self):
    task = DownloadTask(
      id="task-id",
      url="https://music.apple.com/cn/album/example/123",
      codec="alac"
    )

    class FakeProcess:
      def __init__(self):
        self.stdout = io.StringIO(
          "dial tcp 192.168.100.56:10020: connect: no route to host\n"
        )

      def wait(self):
        return 1

    with patch("webapp.app.subprocess.Popen", return_value=FakeProcess()):
      DownloaderRunner()(task, task.url, task.codec)

    self.assertEqual(task.status, "failed")
    self.assertEqual(task.stage, "failed")
    self.assertIn("no route to host", task.error)


if __name__ == "__main__":
  unittest.main()
