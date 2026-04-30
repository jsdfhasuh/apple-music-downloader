import tempfile
import unittest
import os
from pathlib import Path

import webapp.config_loader as config_loader
import webapp.telegram_bot as telegram_bot


from webapp.telegram_bot import (
  TelegramTaskStore,
  createDownloadTask,
  extractAppleMusicUrl,
  getTelegramConfig,
  handleUpdate,
  isAllowedChat,
  pollTaskUpdates,
  runPollingCycle,
)


class TelegramBotTest(unittest.TestCase):
  def testExtractAppleMusicUrlReturnsFirstMatch(self):
    result = extractAppleMusicUrl(
      "one https://music.apple.com/cn/album/foo/123 and https://music.apple.com/us/album/bar/456"
    )

    self.assertEqual(result, "https://music.apple.com/cn/album/foo/123")

  def testExtractAppleMusicUrlReturnsNoneWithoutMatch(self):
    result = extractAppleMusicUrl("hello world")

    self.assertIsNone(result)

  def testIsAllowedChatMatchesConfiguredChatId(self):
    self.assertTrue(isAllowedChat(12345, 12345))
    self.assertFalse(isAllowedChat(12345, 67890))

  def testTelegramTaskStoreSavesAndListsPendingTasks(self):
    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      store.savePendingTask(
        taskId="task-1",
        chatId=12345,
        messageId=88,
        url="https://music.apple.com/cn/album/foo/123",
      )

      tasks = store.listPendingTasks()

    self.assertEqual(len(tasks), 1)
    self.assertEqual(tasks[0]["task_id"], "task-1")
    self.assertEqual(tasks[0]["notify_status"], "pending")

  def testTelegramTaskStorePersistsLastUpdateId(self):
    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")

      self.assertIsNone(store.getLastUpdateId())

      store.setLastUpdateId(42)

      self.assertEqual(store.getLastUpdateId(), 42)

  def testRunPollingCycleStartsFromPersistedOffset(self):
    calls = []

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      store.setLastUpdateId(7)

      def fakeGetUpdates(offset, timeoutSeconds):
        calls.append((offset, timeoutSeconds))
        return [
          {
            "update_id": 8,
            "message": {
              "message_id": 10,
              "chat": {"id": 12345, "type": "private"},
              "text": "https://music.apple.com/cn/album/foo/123",
            },
          }
        ]

      nextOffset = runPollingCycle(
        offset=(store.getLastUpdateId() + 1) if store.getLastUpdateId() is not None else None,
        allowedChatId=12345,
        updatesTimeoutSeconds=30,
        store=store,
        getUpdatesFn=fakeGetUpdates,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchTask=lambda taskId: {"status": "running"},
        sendMessage=lambda chatId, text, replyToMessageId=None: None,
      )

      self.assertEqual(store.getLastUpdateId(), 8)

    self.assertEqual(calls, [(8, 30)])
    self.assertEqual(nextOffset, 9)

  def testHandleUpdateCreatesTaskForAllowedPrivateMessage(self):
    calls = []
    messages = []

    def fakeCreateTask(url):
      calls.append(url)
      return {"taskId": "task-1", "status": "running"}

    def fakeSendMessage(chatId, text, replyToMessageId=None):
      messages.append((chatId, text, replyToMessageId))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "https://music.apple.com/cn/album/foo/123",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=fakeCreateTask,
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, ["https://music.apple.com/cn/album/foo/123"])
    self.assertEqual(len(messages), 1)
    self.assertIn("已接收", messages[0][1])
    self.assertEqual(len(tasks), 1)

  def testHandleUpdateIgnoresWrongChat(self):
    calls = []

    def fakeCreateTask(url):
      calls.append(url)
      return {"taskId": "task-1", "status": "running"}

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 54321, "type": "private"},
            "text": "https://music.apple.com/cn/album/foo/123",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=fakeCreateTask,
        sendMessage=lambda chatId, text, replyToMessageId=None: None,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, [])
    self.assertEqual(tasks, [])

  def testPollTaskUpdatesSendsCompletionMessage(self):
    messages = []

    def fakeFetchTask(taskId):
      self.assertEqual(taskId, "task-1")
      return {
        "status": "completed",
        "result": [{"song": "Example Song", "path": "/downloads/ALAC/example.flac"}],
      }

    def fakeSendMessage(chatId, text, replyToMessageId=None):
      messages.append((chatId, text, replyToMessageId))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      store.savePendingTask(
        taskId="task-1",
        chatId=12345,
        messageId=88,
        url="https://music.apple.com/cn/album/foo/123",
      )

      pollTaskUpdates(store=store, fetchTask=fakeFetchTask, sendMessage=fakeSendMessage)
      tasks = store.listPendingTasks()

    self.assertEqual(len(messages), 1)
    self.assertIn("下载完成", messages[0][1])
    self.assertEqual(tasks, [])

  def testGetTelegramConfigReadsConfigYaml(self):
    with tempfile.TemporaryDirectory() as tempDir:
      configPath = Path(tempDir) / "config.yaml"
      configPath.write_text(
        "\n".join([
          'telegram-bot-token: "bot-token"',
          'telegram-allowed-chat-id: "12345"',
          'telegram-webapp-base-url: "http://127.0.0.1:5000"',
          'telegram-store-path: "webapp/data/custom_telegram_tasks.db"',
        ]),
        encoding="utf-8",
      )

      config = getTelegramConfig(configPath)

    self.assertEqual(config.botToken, "bot-token")
    self.assertEqual(config.allowedChatId, 12345)
    self.assertEqual(config.webappBaseUrl, "http://127.0.0.1:5000")
    self.assertEqual(config.storePath, "webapp/data/custom_telegram_tasks.db")

  def testGetTelegramConfigReadsWebappConfigPathFromEnv(self):
    originalConfigPath = os.environ.get("WEBAPP_CONFIG_PATH")
    try:
      with tempfile.TemporaryDirectory() as tempDir:
        configPath = Path(tempDir) / "webapp-config.yaml"
        configPath.write_text(
          "\n".join([
            'telegram-bot-token: "env-bot-token"',
            'telegram-allowed-chat-id: "67890"',
            'telegram-webapp-base-url: "http://127.0.0.1:6000"',
            'telegram-store-path: "webapp/data/env_telegram_tasks.db"',
          ]),
          encoding="utf-8",
        )
        os.environ["WEBAPP_CONFIG_PATH"] = str(configPath)

        config = getTelegramConfig()

      self.assertEqual(config.botToken, "env-bot-token")
      self.assertEqual(config.allowedChatId, 67890)
      self.assertEqual(config.webappBaseUrl, "http://127.0.0.1:6000")
      self.assertEqual(config.storePath, "webapp/data/env_telegram_tasks.db")
    finally:
      if originalConfigPath is None:
        os.environ.pop("WEBAPP_CONFIG_PATH", None)
      else:
        os.environ["WEBAPP_CONFIG_PATH"] = originalConfigPath

  def testResolveConfigPathPrefersRootConfigByDefault(self):
    originalFile = config_loader.__file__
    try:
      with tempfile.TemporaryDirectory() as tempDir:
        repoPath = Path(tempDir)
        webappPath = repoPath / "webapp"
        webappPath.mkdir()
        (repoPath / "config.yaml").write_text('telegram-bot-token: "root-token"\n', encoding="utf-8")
        (webappPath / "config.yaml").write_text('telegram-bot-token: "webapp-token"\n', encoding="utf-8")
        config_loader.__file__ = str(webappPath / "config_loader.py")

        configPath = config_loader.resolveConfigPath()

      self.assertEqual(configPath, repoPath / "config.yaml")
    finally:
      config_loader.__file__ = originalFile

  def testCreateDownloadTaskSendsTelegramSource(self):
    calls = []

    def fakeCallJsonApi(url, method="GET", payload=None):
      calls.append((url, method, payload))
      return {"taskId": "task-1", "status": "running"}

    originalCallJsonApi = telegram_bot.callJsonApi
    telegram_bot.callJsonApi = fakeCallJsonApi
    try:
      createDownloadTask("http://127.0.0.1:5000", "https://music.apple.com/cn/album/foo/123")
    finally:
      telegram_bot.callJsonApi = originalCallJsonApi

    self.assertEqual(calls[0][1], "POST")
    self.assertEqual(calls[0][2]["source"], "telegram")


if __name__ == "__main__":
  unittest.main()
