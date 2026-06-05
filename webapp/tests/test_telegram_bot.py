import os
import tempfile
import unittest
import io
from pathlib import Path
from urllib import error as urlerror

import webapp.config_loader as config_loader
import webapp.telegram_bot as telegram_bot


from webapp.telegram_bot import (
  TelegramTaskStore,
  buildTelegramCommands,
  createArtistSubscription,
  createDownloadTask,
  deleteArtistSubscription,
  extractAppleMusicUrl,
  extractAppleMusicUrls,
  filterTasksByStatus,
  formatArtistSearchResultsMessage,
  formatStartupWelcomeMessage,
  formatRetryFailedTasksMessage,
  formatSubscriptionScanSummaryMessage,
  formatTaskListMessage,
  getTelegramConfig,
  handleUpdate,
  initializeTelegramBot,
  isAllowedChat,
  listArtistSubscriptions,
  listTasks,
  pollTaskUpdates,
  retryFailedTasks,
  runPollingCycle,
  scanArtistSubscriptions,
  searchArtists,
  setTelegramCommands,
  sendTelegramMessage,
)


class TelegramBotTest(unittest.TestCase):
  def testExtractAppleMusicUrlReturnsFirstMatch(self):
    result = extractAppleMusicUrl(
      "one https://music.apple.com/cn/album/foo/123 and https://music.apple.com/us/album/bar/456"
    )

    self.assertEqual(result, "https://music.apple.com/cn/album/foo/123")

  def testExtractAppleMusicUrlsReturnsAllMatchesInOrder(self):
    result = extractAppleMusicUrls(
      "one https://music.apple.com/cn/album/foo/123 and https://music.apple.com/us/album/bar/456"
    )

    self.assertEqual(result, [
      "https://music.apple.com/cn/album/foo/123",
      "https://music.apple.com/us/album/bar/456",
    ])

  def testExtractAppleMusicUrlsStripsTrailingPunctuation(self):
    result = extractAppleMusicUrls(
      "链接：https://music.apple.com/cn/album/foo/123?ls）。另一个 <https://music.apple.com/us/album/bar/456>,"
    )

    self.assertEqual(result, [
      "https://music.apple.com/cn/album/foo/123?ls",
      "https://music.apple.com/us/album/bar/456",
    ])

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

  def testRunPollingCycleSkipsDuplicateProcessedUpdate(self):
    calls = []

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      store.markUpdateProcessed(8)

      nextOffset = runPollingCycle(
        offset=8,
        allowedChatId=12345,
        updatesTimeoutSeconds=30,
        store=store,
        getUpdatesFn=lambda offset, timeoutSeconds: [
          {
            "update_id": 8,
            "message": {
              "message_id": 10,
              "chat": {"id": 12345, "type": "private"},
              "text": "https://music.apple.com/cn/album/foo/123",
            },
          }
        ],
        createTask=lambda url: calls.append(url) or {"taskId": "task-1", "status": "running"},
        fetchTask=lambda taskId: {"status": "running"},
        sendMessage=lambda chatId, text, replyToMessageId=None: None,
      )

      self.assertEqual(store.getLastUpdateId(), 8)

    self.assertEqual(calls, [])
    self.assertEqual(nextOffset, 9)

  def testRunPollingCycleKeepsOffsetProgressWhenStaleTaskIsRemoved(self):
    calls = []

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      store.savePendingTask(
        taskId="stale-task",
        chatId=12345,
        messageId=88,
        url="https://music.apple.com/cn/album/stale/123",
      )

      def fakeFetchTask(taskId):
        if taskId == "stale-task":
          raise urlerror.HTTPError(
            url=f"http://127.0.0.1:5000/api/tasks/{taskId}",
            code=404,
            msg="NOT FOUND",
            hdrs=None,
            fp=None,
          )
        return {"status": "running"}

      nextOffset = runPollingCycle(
        offset=7,
        allowedChatId=12345,
        updatesTimeoutSeconds=30,
        store=store,
        getUpdatesFn=lambda offset, timeoutSeconds: [
          {
            "update_id": 8,
            "message": {
              "message_id": 10,
              "chat": {"id": 12345, "type": "private"},
              "text": "https://music.apple.com/cn/album/foo/123",
            },
          }
        ],
        createTask=lambda url: calls.append(url) or {"taskId": "task-1", "status": "running"},
        fetchTask=fakeFetchTask,
        sendMessage=lambda chatId, text, replyToMessageId=None: None,
      )

      self.assertEqual(store.getLastUpdateId(), 8)
      self.assertEqual(store.listPendingTasks(), [{
        "task_id": "task-1",
        "chat_id": 12345,
        "message_id": 10,
        "url": "https://music.apple.com/cn/album/foo/123",
        "notify_status": "pending",
      }])

    self.assertEqual(calls, ["https://music.apple.com/cn/album/foo/123"])
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

  def testHandleUpdateIgnoresBotAuthoredMessage(self):
    calls = []

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "from": {"id": 999, "is_bot": True},
            "text": "https://music.apple.com/cn/album/foo/123",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: calls.append(url) or {"taskId": "task-1", "status": "running"},
        sendMessage=lambda chatId, text, replyToMessageId=None: None,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, [])
    self.assertEqual(tasks, [])

  def testHandleUpdateTracksQueuedTaskAndMentionsWaiting(self):
    messages = []

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
        createTask=lambda url: {"taskId": "task-1", "status": "queued"},
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(len(messages), 1)
    self.assertIn("等待开始下载", messages[0][1])
    self.assertEqual(len(tasks), 1)

  def testHandleUpdateBatchCreatesTasksForMultipleUrlsAndSendsSummary(self):
    calls = []
    messages = []
    url1 = "https://music.apple.com/cn/album/foo/123"
    url2 = "https://music.apple.com/cn/album/bar/456"
    url3 = "https://music.apple.com/cn/album/baz/789"
    responses = {
      url1: {"taskId": "task-1", "status": "running"},
      url2: {"taskId": "task-2", "status": "queued"},
      url3: {"taskId": "task-3", "status": "completed"},
    }

    def fakeCreateTask(url):
      calls.append(url)
      return responses[url]

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
            "text": f"{url1}\n{url2}\n{url3}",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=fakeCreateTask,
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, [url1, url2, url3])
    self.assertEqual(len(messages), 1)
    self.assertIn("识别 3 个链接", messages[0][1])
    self.assertIn("开始下载 (1):", messages[0][1])
    self.assertIn(f"- {url1}", messages[0][1])
    self.assertIn("新加入队列 (1):", messages[0][1])
    self.assertIn(f"- {url2}", messages[0][1])
    self.assertIn("历史已完成 (1):", messages[0][1])
    self.assertIn(f"- {url3}", messages[0][1])
    self.assertEqual({task["task_id"] for task in tasks}, {"task-1", "task-2"})

  def testHandleUpdateBatchDeduplicatesUrlsWithinMessage(self):
    calls = []
    messages = []
    url1 = "https://music.apple.com/cn/album/foo/123"
    url2 = "https://music.apple.com/cn/album/bar/456"

    def fakeCreateTask(url):
      calls.append(url)
      return {"taskId": f"task-{len(calls)}", "status": "running"}

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
            "text": f"{url1}\n{url2}\n{url1}",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=fakeCreateTask,
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, [url1, url2])
    self.assertEqual(len(messages), 1)
    self.assertIn("识别 3 个链接", messages[0][1])
    self.assertIn("去重后 2 个", messages[0][1])
    self.assertIn("消息内重复跳过 (1):", messages[0][1])
    self.assertIn(f"- {url1}", messages[0][1])
    self.assertEqual(len(tasks), 2)

  def testHandleUpdateBatchSummaryReportsReusedTaskCounts(self):
    messages = []
    url1 = "https://music.apple.com/cn/album/foo/123"
    url2 = "https://music.apple.com/cn/album/bar/456"
    url3 = "https://music.apple.com/cn/album/baz/789"
    responses = {
      url1: {
        "taskId": "task-1",
        "status": "running",
        "message": "download already in progress",
      },
      url2: {
        "taskId": "task-2",
        "status": "queued",
        "message": "download already queued",
      },
      url3: {
        "taskId": "task-3",
        "status": "completed",
      },
    }

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
            "text": f"{url1}\n{url2}\n{url3}",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: responses[url],
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(len(messages), 1)
    self.assertIn("复用进行中任务 (1):", messages[0][1])
    self.assertIn(f"- {url1}", messages[0][1])
    self.assertIn("复用排队任务 (1):", messages[0][1])
    self.assertIn(f"- {url2}", messages[0][1])
    self.assertIn("历史已完成 (1):", messages[0][1])
    self.assertIn(f"- {url3}", messages[0][1])
    self.assertEqual({task["task_id"] for task in tasks}, {"task-1", "task-2"})

  def testHandleUpdateDoesNotClaimDownloadStartedForCompletedUrl(self):
    messages = []

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
        createTask=lambda url: {"taskId": "task-1", "status": "completed"},
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(len(messages), 1)
    self.assertEqual(messages[0][1], "该链接已下载，直接返回历史记录")
    self.assertEqual(tasks, [])

  def testHandleUpdateShowsHelpKeyboard(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "帮助",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("直接发送 Apple Music 链接即可下载", messages[0][1])
    self.assertIsInstance(messages[0][3], dict)

  def testHandleUpdateSupportsSlashHelp(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/help",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("直接发送 Apple Music 链接即可下载", messages[0][1])

  def testHandleUpdateShowsForceDownloadHint(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "强制下载说明",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url, force=False: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("/force https://music.apple.com/", messages[0][1])

  def testHandleUpdateSupportsForceCommand(self):
    calls = []
    messages = []

    def fakeCreateTask(url, force=False):
      calls.append((url, force))
      return {"taskId": "task-1", "status": "running"}

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/force https://music.apple.com/cn/album/foo/123",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=fakeCreateTask,
        retryTasks=lambda: {},
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, [("https://music.apple.com/cn/album/foo/123", True)])
    self.assertEqual(len(messages), 1)
    self.assertIn("已接收", messages[0][1])
    self.assertEqual(len(tasks), 1)

  def testHandleUpdateSupportsForceCommandForMultipleUrls(self):
    calls = []
    messages = []
    url1 = "https://music.apple.com/cn/album/foo/123"
    url2 = "https://music.apple.com/cn/album/bar/456"

    def fakeCreateTask(url, force=False):
      calls.append((url, force))
      return {"taskId": f"task-{len(calls)}", "status": "running"}

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": f"/force {url1}\n{url2}",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=fakeCreateTask,
        retryTasks=lambda: {},
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )
      tasks = store.listPendingTasks()

    self.assertEqual(calls, [(url1, True), (url2, True)])
    self.assertEqual(len(messages), 1)
    self.assertIn("识别 2 个链接", messages[0][1])
    self.assertIn("开始下载 (2):", messages[0][1])
    self.assertIn(f"- {url1}", messages[0][1])
    self.assertIn(f"- {url2}", messages[0][1])
    self.assertEqual(len(tasks), 2)

  def testHandleUpdateShowsForceUsageWhenCommandHasNoUrl(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/force",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url, force=False: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("/force https://music.apple.com/", messages[0][1])

  def testHandleUpdateSupportsArtistSearchCommand(self):
    messages = []
    calls = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/artist_search Example",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        searchArtists=lambda term: calls.append(term) or [{
          "artistId": "12345",
          "artistName": "Example Artist",
          "storefront": "cn",
          "artistUrl": "https://music.apple.com/cn/artist/example-artist/12345",
        }],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(calls, ["Example"])
    self.assertEqual(len(messages), 1)
    self.assertIn("Example Artist", messages[0][1])
    self.assertIn("https://music.apple.com/cn/artist/example-artist/12345", messages[0][1])

  def testHandleUpdateSupportsSubscriptionCommands(self):
    messages = []
    calls = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/subscribe https://music.apple.com/cn/artist/example-artist/12345",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        createSubscription=lambda artistUrl: calls.append(("subscribe", artistUrl)) or {
          "subscription": {"artistName": "Example Artist", "artistId": "12345"},
          "scan": {
            "foundCount": 2,
            "queuedCount": 1,
            "skippedCompletedCount": 1,
            "skippedActiveCount": 0,
            "errorCount": 0,
          },
        },
        sendMessage=fakeSendMessage,
      )

      handleUpdate(
        update={
          "update_id": 2,
          "message": {
            "message_id": 11,
            "chat": {"id": 12345, "type": "private"},
            "text": "/subscriptions",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=lambda: [{
          "artistName": "Example Artist",
          "artistId": "12345",
          "storefront": "cn",
          "albumCount": 2,
          "lastCheckedAt": "2026-06-05 00:00:00",
        }],
        sendMessage=fakeSendMessage,
      )

      handleUpdate(
        update={
          "update_id": 3,
          "message": {
            "message_id": 12,
            "chat": {"id": 12345, "type": "private"},
            "text": "/unsubscribe 12345",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        deleteSubscription=lambda artistId: calls.append(("unsubscribe", artistId)) or {"deleted": True},
        sendMessage=fakeSendMessage,
      )

      handleUpdate(
        update={
          "update_id": 4,
          "message": {
            "message_id": 13,
            "chat": {"id": 12345, "type": "private"},
            "text": "/scan_subscriptions",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        scanSubscriptions=lambda: {
          "scannedCount": 1,
          "foundCount": 2,
          "queuedCount": 1,
          "skippedCompletedCount": 1,
          "skippedActiveCount": 0,
          "errorCount": 0,
        },
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(calls, [
      ("subscribe", "https://music.apple.com/cn/artist/example-artist/12345"),
      ("unsubscribe", "12345"),
    ])
    self.assertIn("已订阅 Example Artist", messages[0][1])
    self.assertIn("歌手订阅", messages[1][1])
    self.assertIn("已取消订阅 artist_id: 12345", messages[2][1])
    self.assertIn("歌手订阅扫描完成", messages[3][1])

  def testRunPollingCycleMarksSubscriptionCommandProcessedAfterApiError(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    def fakeDeleteSubscription(artistId):
      raise urlerror.HTTPError(
        url="http://127.0.0.1:5000/api/subscriptions/by-artist/missing",
        code=404,
        msg="NOT FOUND",
        hdrs=None,
        fp=io.BytesIO(b'{"error": "subscription not found"}'),
      )

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      nextOffset = runPollingCycle(
        offset=7,
        allowedChatId=12345,
        updatesTimeoutSeconds=30,
        store=store,
        getUpdatesFn=lambda offset, timeoutSeconds: [
          {
            "update_id": 8,
            "message": {
              "message_id": 10,
              "chat": {"id": 12345, "type": "private"},
              "text": "/unsubscribe missing",
            },
          }
        ],
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchTask=lambda taskId: {"status": "running"},
        deleteSubscription=fakeDeleteSubscription,
        sendMessage=fakeSendMessage,
      )

      self.assertTrue(store.hasProcessedUpdate(8))
      self.assertEqual(store.getLastUpdateId(), 8)

    self.assertEqual(nextOffset, 9)
    self.assertEqual(len(messages), 1)
    self.assertIn("取消订阅失败", messages[0][1])
    self.assertIn("subscription not found", messages[0][1])

  def testHandleUpdateRetriesFailedTasksFromKeyboard(self):
    messages = []
    retryCalls = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "重试失败任务",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: retryCalls.append(True) or {
          "retriedCount": 2,
          "skippedCompletedCount": 1,
          "skippedRunningCount": 0,
        },
        fetchTasks=lambda: [],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(retryCalls, [True])
    self.assertEqual(len(messages), 1)
    self.assertIn("已重试 2 个失败任务", messages[0][1])

  def testHandleUpdateShowsTaskListFromKeyboard(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "查看队列",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [
          {
            "taskId": "task-1",
            "source": "telegram",
            "status": "running",
            "stage": "queued",
            "url": "https://music.apple.com/cn/album/foo/123",
          }
        ],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("当前任务队列", messages[0][1])
    self.assertIn("telegram", messages[0][1])

  def testHandleUpdateShowsFailedTasksFromSlashCommand(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/failed",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [
          {"source": "telegram", "status": "failed", "stage": "failed", "url": "https://music.apple.com/cn/album/foo/123"},
          {"source": "web", "status": "running", "stage": "queued", "url": "https://music.apple.com/cn/album/bar/456"},
        ],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("失败任务", messages[0][1])
    self.assertIn("foo/123", messages[0][1])
    self.assertNotIn("bar/456", messages[0][1])

  def testHandleUpdateShowsRunningTasksFromSlashCommand(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/running",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [
          {"source": "telegram", "status": "failed", "stage": "failed", "url": "https://music.apple.com/cn/album/foo/123"},
          {"source": "web", "status": "running", "stage": "queued", "url": "https://music.apple.com/cn/album/bar/456"},
        ],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("运行中任务", messages[0][1])
    self.assertIn("bar/456", messages[0][1])
    self.assertNotIn("foo/123", messages[0][1])

  def testHandleUpdateShowsRecentResultsFromSlashCommand(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "message": {
            "message_id": 10,
            "chat": {"id": 12345, "type": "private"},
            "text": "/recent",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        retryTasks=lambda: {},
        fetchTasks=lambda: [
          {"source": "telegram", "status": "completed", "stage": "completed", "url": "https://music.apple.com/cn/album/foo/123"},
          {"source": "web", "status": "running", "stage": "queued", "url": "https://music.apple.com/cn/album/bar/456"},
        ],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 1)
    self.assertIn("最近结果", messages[0][1])
    self.assertIn("foo/123", messages[0][1])
    self.assertNotIn("bar/456", messages[0][1])

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

  def testPollTaskUpdatesRemovesStaleTaskAndContinues(self):
    messages = []

    def fakeFetchTask(taskId):
      if taskId == "task-stale":
        raise urlerror.HTTPError(
          url=f"http://127.0.0.1:5000/api/tasks/{taskId}",
          code=404,
          msg="NOT FOUND",
          hdrs=None,
          fp=None,
        )
      return {
        "status": "completed",
        "result": [{"song": "Example Song", "path": "/downloads/ALAC/example.flac"}],
      }

    def fakeSendMessage(chatId, text, replyToMessageId=None):
      messages.append((chatId, text, replyToMessageId))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      store.savePendingTask(
        taskId="task-stale",
        chatId=12345,
        messageId=88,
        url="https://music.apple.com/cn/album/stale/123",
      )
      store.savePendingTask(
        taskId="task-good",
        chatId=12345,
        messageId=89,
        url="https://music.apple.com/cn/album/good/456",
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

  def testResolveConfigPathPrefersDataConfigWhenPresent(self):
    originalFile = config_loader.__file__
    try:
      with tempfile.TemporaryDirectory() as tempDir:
        repoPath = Path(tempDir)
        dataPath = repoPath / "data"
        webappPath = repoPath / "webapp"
        dataPath.mkdir()
        webappPath.mkdir()
        (dataPath / "config.yaml").write_text('telegram-bot-token: "data-token"\n', encoding="utf-8")
        (repoPath / "config.yaml").write_text('telegram-bot-token: "root-token"\n', encoding="utf-8")
        config_loader.__file__ = str(webappPath / "config_loader.py")

        configPath = config_loader.resolveConfigPath()

      self.assertEqual(configPath, dataPath / "config.yaml")
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

  def testCreateDownloadTaskSendsForceFlag(self):
    calls = []

    def fakeCallJsonApi(url, method="GET", payload=None):
      calls.append((url, method, payload))
      return {"taskId": "task-1", "status": "running"}

    originalCallJsonApi = telegram_bot.callJsonApi
    telegram_bot.callJsonApi = fakeCallJsonApi
    try:
      createDownloadTask("http://127.0.0.1:5000", "https://music.apple.com/cn/album/foo/123", force=True)
    finally:
      telegram_bot.callJsonApi = originalCallJsonApi

    self.assertEqual(calls[0][2]["force"], True)

  def testRetryFailedTasksPostsToRetryEndpoint(self):
    calls = []

    def fakeCallJsonApi(url, method="GET", payload=None):
      calls.append((url, method, payload))
      return {"retriedCount": 1}

    originalCallJsonApi = telegram_bot.callJsonApi
    telegram_bot.callJsonApi = fakeCallJsonApi
    try:
      retryFailedTasks("http://127.0.0.1:5000")
    finally:
      telegram_bot.callJsonApi = originalCallJsonApi

    self.assertEqual(calls[0][0], "http://127.0.0.1:5000/api/tasks/retry-failed")
    self.assertEqual(calls[0][1], "POST")

  def testListTasksGetsTaskListEndpoint(self):
    calls = []

    def fakeCallListApi(url):
      calls.append(url)
      return []

    originalCallListApi = telegram_bot.callListApi
    telegram_bot.callListApi = fakeCallListApi
    try:
      listTasks("http://127.0.0.1:5000")
    finally:
      telegram_bot.callListApi = originalCallListApi

    self.assertEqual(calls[0], "http://127.0.0.1:5000/api/tasks")

  def testArtistSubscriptionApiWrappersUseExpectedEndpoints(self):
    jsonCalls = []
    listCalls = []

    def fakeCallJsonApi(url, method="GET", payload=None):
      jsonCalls.append((url, method, payload))
      if url.endswith("/api/subscriptions/search"):
        return {"results": [{"artistId": "12345"}]}
      return {"ok": True}

    def fakeCallListApi(url):
      listCalls.append(url)
      return [{"artistId": "12345"}]

    originalCallJsonApi = telegram_bot.callJsonApi
    originalCallListApi = telegram_bot.callListApi
    telegram_bot.callJsonApi = fakeCallJsonApi
    telegram_bot.callListApi = fakeCallListApi
    try:
      searchResult = searchArtists("http://127.0.0.1:5000", "Example")
      createArtistSubscription("http://127.0.0.1:5000", "https://music.apple.com/cn/artist/example/12345")
      listResult = listArtistSubscriptions("http://127.0.0.1:5000")
      deleteArtistSubscription("http://127.0.0.1:5000", "12345")
      scanArtistSubscriptions("http://127.0.0.1:5000")
    finally:
      telegram_bot.callJsonApi = originalCallJsonApi
      telegram_bot.callListApi = originalCallListApi

    self.assertEqual(searchResult, [{"artistId": "12345"}])
    self.assertEqual(listResult, [{"artistId": "12345"}])
    self.assertEqual(jsonCalls[0], (
      "http://127.0.0.1:5000/api/subscriptions/search",
      "POST",
      {"term": "Example"},
    ))
    self.assertEqual(jsonCalls[1][0], "http://127.0.0.1:5000/api/subscriptions")
    self.assertEqual(jsonCalls[1][1], "POST")
    self.assertEqual(listCalls[0], "http://127.0.0.1:5000/api/subscriptions")
    self.assertEqual(jsonCalls[2][0], "http://127.0.0.1:5000/api/subscriptions/by-artist/12345")
    self.assertEqual(jsonCalls[2][1], "DELETE")
    self.assertEqual(jsonCalls[3][0], "http://127.0.0.1:5000/api/subscriptions/scan")
    self.assertEqual(jsonCalls[3][1], "POST")

  def testFormatSubscriptionMessages(self):
    self.assertIn(
      "Example Artist",
      formatArtistSearchResultsMessage([{
        "artistName": "Example Artist",
        "storefront": "cn",
        "artistUrl": "https://music.apple.com/cn/artist/example/12345",
      }]),
    )
    self.assertIn(
      "入队 2 个",
      formatSubscriptionScanSummaryMessage({
        "scannedCount": 1,
        "foundCount": 4,
        "queuedCount": 2,
        "skippedCompletedCount": 1,
        "skippedActiveCount": 1,
        "errorCount": 0,
      }),
    )
    self.assertIn(
      "0 个订阅",
      formatSubscriptionScanSummaryMessage({
        "scannedCount": 0,
        "foundCount": 0,
        "queuedCount": 0,
        "skippedCompletedCount": 0,
        "skippedActiveCount": 0,
        "errorCount": 0,
      }),
    )

  def testSendTelegramMessageSupportsReplyMarkup(self):
    calls = []

    def fakeCallTelegramApi(botToken, method, payload):
      calls.append((botToken, method, payload))
      return {}

    originalCallTelegramApi = telegram_bot.callTelegramApi
    telegram_bot.callTelegramApi = fakeCallTelegramApi
    try:
      sendTelegramMessage(
        "bot-token",
        12345,
        "hello",
        10,
        {"keyboard": [[{"text": "帮助"}]], "resize_keyboard": True},
      )
    finally:
      telegram_bot.callTelegramApi = originalCallTelegramApi

    self.assertEqual(calls[0][1], "sendMessage")
    self.assertIn("reply_markup", calls[0][2])

  def testFormatRetryFailedTasksMessageReportsNoFailures(self):
    self.assertEqual(
      formatRetryFailedTasksMessage({
        "retriedCount": 0,
        "skippedCompletedCount": 0,
        "skippedRunningCount": 0,
      }),
      "当前没有可重试的失败任务"
    )

  def testFormatTaskListMessageFormatsTasks(self):
    message = formatTaskListMessage([
      {
        "source": "telegram",
        "status": "running",
        "stage": "queued",
        "url": "https://music.apple.com/cn/album/foo/123",
      }
    ])

    self.assertIn("当前任务队列", message)
    self.assertIn("telegram", message)

  def testFilterTasksByStatusReturnsOnlyMatchingTasks(self):
    result = filterTasksByStatus([
      {"status": "failed", "url": "a"},
      {"status": "running", "url": "b"},
      {"status": "failed", "url": "c"},
    ], "failed")

    self.assertEqual([item["url"] for item in result], ["a", "c"])

  def testBuildTelegramCommandsReturnsExpectedCommands(self):
    commands = buildTelegramCommands()

    self.assertEqual(commands[0]["command"], "start")
    self.assertTrue(any(item["command"] == "retry_failed" for item in commands))
    self.assertTrue(any(item["command"] == "force" for item in commands))

  def testSetTelegramCommandsCallsSetMyCommands(self):
    calls = []

    def fakeCallTelegramApi(botToken, method, payload):
      calls.append((botToken, method, payload))
      return {}

    originalCallTelegramApi = telegram_bot.callTelegramApi
    telegram_bot.callTelegramApi = fakeCallTelegramApi
    try:
      setTelegramCommands("bot-token")
    finally:
      telegram_bot.callTelegramApi = originalCallTelegramApi

    self.assertEqual(calls[0][1], "setMyCommands")
    self.assertTrue(any(item["command"] == "recent" for item in calls[0][2]["commands"]))

  def testFormatStartupWelcomeMessageIncludesHelpHint(self):
    message = formatStartupWelcomeMessage()

    self.assertIn("机器人已启动", message)
    self.assertIn("直接发送 Apple Music 链接即可下载", message)

  def testInitializeTelegramBotRegistersCommandsAndSendsWelcome(self):
    commandCalls = []
    messageCalls = []

    def fakeSetTelegramCommands(botToken):
      commandCalls.append(botToken)

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messageCalls.append((chatId, text, replyToMessageId, replyMarkup))

    initializeTelegramBot(
      botToken="bot-token",
      allowedChatId=12345,
      setCommands=fakeSetTelegramCommands,
      sendMessage=fakeSendMessage,
    )

    self.assertEqual(commandCalls, ["bot-token"])
    self.assertEqual(len(messageCalls), 1)
    self.assertEqual(messageCalls[0][0], 12345)
    self.assertIn("机器人已启动", messageCalls[0][1])
    self.assertIsInstance(messageCalls[0][3], dict)


if __name__ == "__main__":
  unittest.main()
