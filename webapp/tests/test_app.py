import json
import tempfile
import unittest


from webapp.app import PipelineRunner, createApp


class FakeRunner:
  def __init__(self):
    self.calls = []
    self.autoComplete = True

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
        "path": "/downloads/ALAC/example.flac",
        "artist": "Example Artist",
        "album": "Example Album",
        "song": "Example Song"
      }
    ])
    task.publishEvent("result", result=task.result)
    task.setStage("completed")
    task.setStatus("completed")
    task.setProgress(100)


class FlaskDashboardTest(unittest.TestCase):
  def setUp(self):
    self.runner = FakeRunner()
    self.tempDir = tempfile.TemporaryDirectory()
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
    self.assertEqual(taskPayload["result"][0]["path"], "/downloads/ALAC/example.flac")

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
    self.assertIn(b'"path": "/downloads/ALAC/example.flac"', body)

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

    restartedRunner = FakeRunner()
    restartedApp = createApp(
      runnerFactory=lambda: restartedRunner,
      dbPath=f"{self.tempDir.name}/downloads.db"
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
    self.assertTrue(scriptCalls[0][1].endswith("convert_to_flac.py"))
    self.assertTrue(scriptCalls[0][2].endswith("example.m4a"))
    self.assertTrue(scriptCalls[1][1].endswith("build_nfo.py"))
    self.assertEqual(scriptCalls[1][2], self.tempDir.name)
    self.assertEqual(len(self.runner.calls), 1)
    self.assertEqual(self.runner.calls[0][2], "alac")

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
        if command[1].endswith("convert_to_flac.py"):
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
    self.assertEqual(taskPayload["result"][0]["path"], derivedFlacPath)
    self.assertEqual(scriptCalls[1][2], self.tempDir.name)

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


if __name__ == "__main__":
  unittest.main()
