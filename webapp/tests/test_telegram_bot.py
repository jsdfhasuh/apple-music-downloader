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
  answerTelegramCallbackQuery,
  buildReplyKeyboard,
  buildSubscriptionSelectionKeyboard,
  buildSubscriptionReviewKeyboard,
  buildTelegramCommands,
  createArtistSubscription,
  createDownloadTask,
  deleteArtistSubscription,
  editTelegramMessageText,
  editTelegramMessageReplyMarkup,
  extractAppleMusicUrl,
  extractAppleMusicUrls,
  filterTasksByStatus,
  formatArtistSearchResultsMessage,
  formatStartupWelcomeMessage,
  formatRetryFailedTasksMessage,
  formatSubscriptionSelectionMessage,
  formatSubscriptionReviewMessage,
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
  updateArtistSubscriptionAlbumBySubscriptionId,
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

  def testHandleUpdateShowsHelpAndRemovesKeyboardAfterButtonUse(self):
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
    self.assertEqual(messages[0][3], {"remove_keyboard": True})

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
    self.assertIsNone(messages[0][3])

  def testHandleUpdateShowsAndHidesMenu(self):
    messages = []

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      for updateId, text in [(1, "/menu"), (2, "/hide_menu")]:
        handleUpdate(
          update={
            "update_id": updateId,
            "message": {
              "message_id": updateId + 9,
              "chat": {"id": 12345, "type": "private"},
              "text": text,
            },
          },
          allowedChatId=12345,
          store=store,
          createTask=lambda url: {"taskId": "task-1", "status": "running"},
          sendMessage=fakeSendMessage,
        )

    self.assertEqual(len(messages), 2)
    self.assertTrue(messages[0][3]["one_time_keyboard"])
    self.assertNotIn("is_persistent", messages[0][3])
    self.assertEqual(messages[1][3], {"remove_keyboard": True})

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
    self.assertEqual(messages[0][3], {"remove_keyboard": True})

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
    self.assertIn("请选择歌手", messages[1][1])
    self.assertEqual(messages[1][3], {"inline_keyboard": [[{
      "text": "Example Artist (0)",
      "callback_data": "ss:a:12345:0",
    }]]})
    self.assertIn("已取消订阅 artist_id: 12345", messages[2][1])
    self.assertIn("歌手订阅扫描完成", messages[3][1])

  def testHandleUpdateSendsSubscriptionAlbumReviewAfterSubscribe(self):
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
          "subscription": {"id": 7, "artistName": "Example Artist", "artistId": "12345"},
          "scan": {
            "foundCount": 4,
            "pendingCount": 1,
            "queuedCount": 0,
            "skippedCompletedCount": 1,
            "skippedActiveCount": 1,
            "errorCount": 0,
          },
        },
        fetchSubscriptions=lambda: calls.append(("fetch",)) or [{
          "id": 7,
          "artistName": "Example Artist",
          "artistId": "12345",
          "recentAlbums": [
            {
              "albumId": "111",
              "albumName": "New Album",
              "releaseDate": "2026-06-01",
              "userState": "pending",
              "detectedStatus": "missing",
              "canDownload": True,
            },
            {
              "albumId": "222",
              "albumName": "Queued Album",
              "userState": "pending",
              "detectedStatus": "queued",
              "canDownload": False,
            },
            {
              "albumId": "333",
              "albumName": "Completed Album",
              "userState": "subscribed",
              "detectedStatus": "completed",
              "canDownload": False,
            },
          ],
        }],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(calls[0], ("subscribe", "https://music.apple.com/cn/artist/example-artist/12345"))
    self.assertEqual(calls[1], ("fetch",))
    self.assertEqual(len(messages), 2)
    self.assertIn("已订阅 Example Artist", messages[0][1])
    self.assertIn("Example Artist 专辑确认", messages[1][1])
    self.assertIn("New Album", messages[1][1])
    self.assertIn("进行中 1 个", messages[1][1])
    replyMarkup = messages[1][3]
    self.assertIsInstance(replyMarkup, dict)
    inlineKeyboard = replyMarkup["inline_keyboard"]
    callbackData = [button["callback_data"] for row in inlineKeyboard for button in row]
    self.assertIn("sa:7:111:d", callbackData)
    self.assertIn("sa:7:111:i", callbackData)
    self.assertIn("sa:7:111:m", callbackData)
    self.assertFalse(any(":222:" in item for item in callbackData))

  def testSubscriptionReviewPaginatesPendingAlbums(self):
    subscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [
        {
          "albumId": str(index),
          "albumName": f"Album {index}",
          "releaseDate": f"2026-06-{index:02d}",
          "userState": "pending",
          "detectedStatus": "missing",
          "canDownload": True,
        }
        for index in range(1, 7)
      ],
    }

    firstPageMessage = formatSubscriptionReviewMessage(subscription)
    secondPageMessage = formatSubscriptionReviewMessage(subscription, page=1)
    firstPageKeyboard = buildSubscriptionReviewKeyboard(subscription)
    secondPageKeyboard = buildSubscriptionReviewKeyboard(subscription, page=1)

    self.assertIn("第 1/2 页", firstPageMessage)
    self.assertIn("1. Album 6", firstPageMessage)
    self.assertIn("5. Album 2", firstPageMessage)
    self.assertNotIn("Album 1", firstPageMessage)
    self.assertIn("第 2/2 页", secondPageMessage)
    self.assertIn("6. Album 1", secondPageMessage)
    self.assertNotIn("Album 6", secondPageMessage)
    self.assertIn({"text": "下一页", "callback_data": "srp:7:1"}, firstPageKeyboard["inline_keyboard"][-1])
    self.assertIn({"text": "上一页", "callback_data": "srp:7:0"}, secondPageKeyboard["inline_keyboard"][-1])
    secondPageCallbackData = [
      button["callback_data"]
      for row in secondPageKeyboard["inline_keyboard"]
      for button in row
    ]
    self.assertIn("sa:7:1:d:1", secondPageCallbackData)
    self.assertEqual(len(firstPageKeyboard["inline_keyboard"]), 6)
    self.assertEqual(len(secondPageKeyboard["inline_keyboard"]), 2)

  def testSubscriptionReviewHandlesInvalidPageSize(self):
    subscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [{
        "albumId": "111",
        "albumName": "New Album",
        "userState": "pending",
        "detectedStatus": "missing",
        "canDownload": True,
      }],
    }

    message = formatSubscriptionReviewMessage(subscription, pageSize=0)
    keyboard = buildSubscriptionReviewKeyboard(subscription, pageSize=0)

    self.assertIn("第 1/1 页", message)
    self.assertIn("New Album", message)
    self.assertEqual(keyboard["inline_keyboard"][0][0]["callback_data"], "sa:7:111:d")

  def testSubscriptionSelectionPaginatesArtists(self):
    subscriptions = [
      {
        "id": index,
        "artistName": f"Artist {index}",
        "storefront": "cn",
        "pendingAlbumCount": index,
      }
      for index in range(1, 10)
    ]

    firstPageMessage = formatSubscriptionSelectionMessage(subscriptions)
    secondPageMessage = formatSubscriptionSelectionMessage(subscriptions, page=1)
    firstPageKeyboard = buildSubscriptionSelectionKeyboard(subscriptions)
    secondPageKeyboard = buildSubscriptionSelectionKeyboard(subscriptions, page=1)

    self.assertIn("第 1/2 页", firstPageMessage)
    self.assertIn("1. Artist 1 CN - 待确认 1 个", firstPageMessage)
    self.assertNotIn("Artist 9", firstPageMessage)
    self.assertIn("第 2/2 页", secondPageMessage)
    self.assertIn("9. Artist 9 CN - 待确认 9 个", secondPageMessage)
    self.assertIn({"text": "下一页", "callback_data": "ssp:1"}, firstPageKeyboard["inline_keyboard"][-1])
    self.assertIn({"text": "上一页", "callback_data": "ssp:0"}, secondPageKeyboard["inline_keyboard"][-1])
    self.assertEqual(firstPageKeyboard["inline_keyboard"][0][0]["callback_data"], "ss:s:1:0")

  def testHandleUpdateEditsSubscriptionReviewPageCallback(self):
    edits = []
    answers = []
    messages = []
    subscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [
        {
          "albumId": str(index),
          "albumName": f"Album {index}",
          "releaseDate": f"2026-06-{index:02d}",
          "userState": "pending",
          "detectedStatus": "missing",
          "canDownload": True,
        }
        for index in range(1, 7)
      ],
    }

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345, "is_bot": False},
            "message": {
              "message_id": 99,
              "chat": {"id": 12345, "type": "private"},
            },
            "data": "srp:7:1",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=lambda: [subscription],
        answerCallback=lambda callbackQueryId, text="": answers.append((callbackQueryId, text)),
        editMessageText=lambda chatId, messageId, text, replyMarkup=None: edits.append((chatId, messageId, text, replyMarkup)),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: messages.append((chatId, text, replyToMessageId, replyMarkup)),
      )

    self.assertEqual(answers, [("callback-1", "第 2 页")])
    self.assertEqual(len(edits), 1)
    self.assertEqual(messages, [])
    self.assertEqual(edits[0][0], 12345)
    self.assertEqual(edits[0][1], 99)
    self.assertIn("第 2/2 页", edits[0][2])
    self.assertIn("6. Album 1", edits[0][2])
    self.assertIn({"text": "上一页", "callback_data": "srp:7:0"}, edits[0][3]["inline_keyboard"][-1])

  def testHandleUpdateDoesNotDuplicateUnchangedSubscriptionReviewPage(self):
    answers = []
    messages = []
    subscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [
        {
          "albumId": str(index),
          "albumName": f"Album {index}",
          "releaseDate": f"2026-06-{index:02d}",
          "userState": "pending",
          "detectedStatus": "missing",
          "canDownload": True,
        }
        for index in range(1, 7)
      ],
    }

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345, "is_bot": False},
            "message": {
              "message_id": 99,
              "chat": {"id": 12345, "type": "private"},
            },
            "data": "srp:7:1",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=lambda: [subscription],
        answerCallback=lambda callbackQueryId, text="": answers.append((callbackQueryId, text)),
        editMessageText=lambda chatId, messageId, text, replyMarkup=None: (_ for _ in ()).throw(ValueError("Bad Request: message is not modified")),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: messages.append((chatId, text, replyToMessageId, replyMarkup)),
      )

    self.assertEqual(answers, [("callback-1", "第 2 页")])
    self.assertEqual(messages, [])

  def testHandleUpdateEditsSubscriptionSelectionPageCallback(self):
    edits = []
    answers = []
    messages = []
    subscriptions = [
      {
        "id": index,
        "artistName": f"Artist {index}",
        "storefront": "cn",
        "pendingAlbumCount": index,
      }
      for index in range(1, 10)
    ]

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345, "is_bot": False},
            "message": {
              "message_id": 99,
              "chat": {"id": 12345, "type": "private"},
            },
            "data": "ssp:1",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=lambda: subscriptions,
        answerCallback=lambda callbackQueryId, text="": answers.append((callbackQueryId, text)),
        editMessageText=lambda chatId, messageId, text, replyMarkup=None: edits.append((chatId, messageId, text, replyMarkup)),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: messages.append((chatId, text, replyToMessageId, replyMarkup)),
      )

    self.assertEqual(answers, [("callback-1", "第 2 页")])
    self.assertEqual(messages, [])
    self.assertEqual(len(edits), 1)
    self.assertIn("第 2/2 页", edits[0][2])
    self.assertIn("Artist 9", edits[0][2])
    self.assertIn({"text": "上一页", "callback_data": "ssp:0"}, edits[0][3]["inline_keyboard"][-1])

  def testHandleUpdateEditsSubscriptionReviewAfterArtistSelection(self):
    edits = []
    answers = []
    messages = []
    subscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [{
        "albumId": "111",
        "albumName": "New Album",
        "userState": "pending",
        "detectedStatus": "missing",
        "canDownload": True,
      }],
    }

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345, "is_bot": False},
            "message": {
              "message_id": 99,
              "chat": {"id": 12345, "type": "private"},
            },
            "data": "ss:s:7:0",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=lambda: [subscription],
        answerCallback=lambda callbackQueryId, text="": answers.append((callbackQueryId, text)),
        editMessageText=lambda chatId, messageId, text, replyMarkup=None: edits.append((chatId, messageId, text, replyMarkup)),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: messages.append((chatId, text, replyToMessageId, replyMarkup)),
      )

    self.assertEqual(answers, [("callback-1", "Example Artist")])
    self.assertEqual(messages, [])
    self.assertEqual(len(edits), 1)
    self.assertIn("Example Artist 专辑确认", edits[0][2])
    self.assertIn("New Album", edits[0][2])
    callbackData = [button["callback_data"] for row in edits[0][3]["inline_keyboard"] for button in row]
    self.assertIn("sa:7:111:d:0:0", callbackData)
    self.assertIn("ssp:0", callbackData)

  def testHandleUpdateKeepsSubscriptionReviewPageAfterAlbumAction(self):
    calls = []
    fetchCalls = []
    edits = []
    messages = []
    answers = []
    initialSubscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [
        {
          "albumId": str(index),
          "albumName": f"Album {index}",
          "releaseDate": f"2026-06-{index:02d}",
          "userState": "pending",
          "detectedStatus": "missing",
          "canDownload": True,
        }
        for index in range(1, 8)
      ],
    }
    refreshedSubscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [
        {
          "albumId": str(index),
          "albumName": f"Album {index}",
          "releaseDate": f"2026-06-{index:02d}",
          "userState": "ignored" if index == 1 else "pending",
          "detectedStatus": "missing",
          "canDownload": True,
        }
        for index in range(1, 8)
      ],
    }

    def fakeFetchSubscriptions():
      fetchCalls.append("fetch")
      if len(fetchCalls) == 1:
        return [initialSubscription]
      return [refreshedSubscription]

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345, "is_bot": False},
            "message": {
              "message_id": 99,
              "chat": {"id": 12345, "type": "private"},
            },
            "data": "sa:7:1:i:1",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=fakeFetchSubscriptions,
        updateSubscriptionAlbumBySubscriptionId=lambda subscriptionId, albumId, action: calls.append((subscriptionId, albumId, action)) or {
          "action": action,
          "updatedCount": 1,
        },
        answerCallback=lambda callbackQueryId, text="": answers.append((callbackQueryId, text)),
        editMessageReplyMarkup=lambda chatId, messageId, replyMarkup=None: edits.append((chatId, messageId, replyMarkup)),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: messages.append((chatId, text, replyToMessageId, replyMarkup)),
      )

    self.assertEqual(calls, [("7", "1", "ignore")])
    self.assertEqual(answers, [("callback-1", "已处理")])
    self.assertEqual(edits, [(12345, 99, None)])
    self.assertEqual(len(messages), 2)
    self.assertIn("已忽略：Album 1", messages[0][1])
    self.assertIn("第 2/2 页", messages[1][1])
    self.assertIn("6. Album 2", messages[1][1])
    self.assertNotIn("Album 1", messages[1][1])

  def testHandleUpdateSendsNoPendingSubscriptionReviewWithoutKeyboard(self):
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
            "text": "/subscribe https://music.apple.com/cn/artist/example-artist/12345",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        createSubscription=lambda artistUrl: {
          "subscription": {"id": 7, "artistName": "Example Artist", "artistId": "12345"},
          "scan": {"foundCount": 2, "pendingCount": 0, "errorCount": 0},
        },
        fetchSubscriptions=lambda: [{
          "id": 7,
          "artistName": "Example Artist",
          "artistId": "12345",
          "recentAlbums": [
            {"albumId": "111", "albumName": "Done Album", "userState": "subscribed", "detectedStatus": "completed", "canDownload": False},
            {"albumId": "222", "albumName": "Ignored Album", "userState": "ignored", "detectedStatus": "missing", "canDownload": False},
          ],
        }],
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(len(messages), 2)
    self.assertIn("没有需要处理的专辑", messages[1][1])
    self.assertIsNone(messages[1][3])

  def testHandleUpdateProcessesSubscriptionAlbumCallback(self):
    messages = []
    answers = []
    edits = []
    calls = []
    refreshed = [{
      "id": 7,
      "artistName": "Example Artist",
      "artistId": "12345",
      "recentAlbums": [
        {
          "albumId": "111",
          "albumName": "New Album",
          "userState": "pending",
          "detectedStatus": "missing",
          "canDownload": True,
        }
      ],
    }]

    def fakeFetchSubscriptions():
      calls.append(("fetch",))
      return refreshed

    def fakeSendMessage(chatId, text, replyToMessageId=None, replyMarkup=None):
      messages.append((chatId, text, replyToMessageId, replyMarkup))

    with tempfile.TemporaryDirectory() as tempDir:
      store = TelegramTaskStore(f"{tempDir}/telegram_tasks.db")
      handleUpdate(
        update={
          "update_id": 1,
          "callback_query": {
            "id": "callback-1",
            "from": {"id": 12345, "is_bot": False},
            "message": {
              "message_id": 99,
              "chat": {"id": 12345, "type": "private"},
            },
            "data": "sa:7:111:i",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=fakeFetchSubscriptions,
        updateSubscriptionAlbumBySubscriptionId=lambda subscriptionId, albumId, action: calls.append(("album", subscriptionId, albumId, action)) or {
          "action": action,
          "updatedCount": 1,
        },
        answerCallback=lambda callbackQueryId, text="": answers.append((callbackQueryId, text)),
        editMessageReplyMarkup=lambda chatId, messageId, replyMarkup=None: edits.append((chatId, messageId, replyMarkup)),
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(answers, [("callback-1", "已处理")])
    self.assertEqual(edits, [(12345, 99, None)])
    self.assertEqual(calls, [
      ("fetch",),
      ("album", "7", "111", "ignore"),
      ("fetch",),
    ])
    self.assertIn("已忽略：New Album", messages[0][1])
    self.assertIn("Example Artist 专辑确认", messages[1][1])

  def testRunPollingCycleMarksCallbackProcessedWhenTelegramFollowupFails(self):
    actionCalls = []

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
            "callback_query": {
              "id": "callback-1",
              "from": {"id": 12345, "is_bot": False},
              "message": {
                "message_id": 99,
                "chat": {"id": 12345, "type": "private"},
              },
              "data": "sa:7:111:i",
            },
          }
        ],
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchTask=lambda taskId: {"status": "running"},
        fetchSubscriptions=lambda: [{
          "id": 7,
          "artistName": "Example Artist",
          "artistId": "12345",
          "recentAlbums": [{
            "albumId": "111",
            "albumName": "New Album",
            "userState": "pending",
            "detectedStatus": "missing",
            "canDownload": True,
          }],
        }],
        updateSubscriptionAlbumBySubscriptionId=lambda subscriptionId, albumId, action: actionCalls.append((subscriptionId, albumId, action)) or {
          "action": action,
          "updatedCount": 1,
        },
        answerCallback=lambda callbackQueryId, text="": (_ for _ in ()).throw(RuntimeError("answer failed")),
        editMessageReplyMarkup=lambda chatId, messageId, replyMarkup=None: (_ for _ in ()).throw(RuntimeError("edit failed")),
        sendMessage=lambda chatId, text, replyToMessageId=None, replyMarkup=None: (_ for _ in ()).throw(RuntimeError("send failed")),
      )

      self.assertTrue(store.hasProcessedUpdate(8))
      self.assertEqual(store.getLastUpdateId(), 8)

    self.assertEqual(nextOffset, 9)
    self.assertEqual(actionCalls, [("7", "111", "ignore")])

  def testHandleUpdateSupportsSubscriptionPolicyAndAlbumCommands(self):
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
            "text": "/subscription_policy 12345 auto",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        updateSubscriptionPolicy=lambda artistId, policy: calls.append(("policy", artistId, policy)) or {
          "subscription": {"artistName": "Example Artist", "newAlbumPolicy": policy},
        },
        sendMessage=fakeSendMessage,
      )
      handleUpdate(
        update={
          "update_id": 2,
          "message": {
            "message_id": 11,
            "chat": {"id": 12345, "type": "private"},
            "text": "/subscription_album 12345 67890 completed",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        updateSubscriptionAlbum=lambda artistId, albumId, action: calls.append(("album", artistId, albumId, action)) or {
          "action": action,
          "updatedCount": 1,
        },
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(calls, [
      ("policy", "12345", "auto"),
      ("album", "12345", "67890", "mark_completed"),
    ])
    self.assertIn("策略已更新为 auto", messages[0][1])
    self.assertIn("已确认完成 1 个专辑", messages[1][1])

  def testHandleUpdateSupportsSubscriptionKeyboardShortcuts(self):
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
            "text": "订阅说明",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        sendMessage=fakeSendMessage,
      )

      handleUpdate(
        update={
          "update_id": 2,
          "message": {
            "message_id": 11,
            "chat": {"id": 12345, "type": "private"},
            "text": "查看订阅",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        fetchSubscriptions=lambda: calls.append("subscriptions") or [{
          "artistName": "Example Artist",
          "artistId": "12345",
          "storefront": "cn",
        }],
        sendMessage=fakeSendMessage,
      )

      handleUpdate(
        update={
          "update_id": 3,
          "message": {
            "message_id": 12,
            "chat": {"id": 12345, "type": "private"},
            "text": "扫描订阅",
          },
        },
        allowedChatId=12345,
        store=store,
        createTask=lambda url: {"taskId": "task-1", "status": "running"},
        scanSubscriptions=lambda: calls.append("scan") or {
          "scannedCount": 1,
          "foundCount": 2,
          "queuedCount": 1,
          "skippedCompletedCount": 1,
          "skippedActiveCount": 0,
          "errorCount": 0,
        },
        sendMessage=fakeSendMessage,
      )

    self.assertEqual(calls, ["subscriptions", "scan"])
    self.assertIn("/artist_search <关键词>", messages[0][1])
    self.assertIn("Example Artist", messages[1][1])
    self.assertIn("歌手订阅扫描完成", messages[2][1])
    self.assertEqual(messages[0][3], {"remove_keyboard": True})
    self.assertEqual(messages[1][3], {"inline_keyboard": [[{
      "text": "Example Artist (0)",
      "callback_data": "ss:a:12345:0",
    }]]})
    self.assertEqual(messages[2][3], {"remove_keyboard": True})

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

      self.assertEqual(configPath.resolve(), (repoPath / "config.yaml").resolve())
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

      self.assertEqual(configPath.resolve(), (dataPath / "config.yaml").resolve())
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
    reviewSubscription = {
      "id": 7,
      "artistName": "Example Artist",
      "recentAlbums": [{
        "albumId": "111",
        "albumName": "New Album",
        "userState": "pending",
        "detectedStatus": "missing",
        "canDownload": True,
      }],
    }
    self.assertIn("New Album", formatSubscriptionReviewMessage(reviewSubscription))
    self.assertEqual(
      buildSubscriptionReviewKeyboard(reviewSubscription),
      {"inline_keyboard": [[
        {"text": "1 下载", "callback_data": "sa:7:111:d"},
        {"text": "1 完成", "callback_data": "sa:7:111:c"},
        {"text": "1 忽略", "callback_data": "sa:7:111:i"},
        {"text": "1 已导入", "callback_data": "sa:7:111:m"},
      ]]},
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

  def testAnswerTelegramCallbackQueryCallsTelegramApi(self):
    calls = []

    def fakeCallTelegramApi(botToken, method, payload):
      calls.append((botToken, method, payload))
      return {}

    originalCallTelegramApi = telegram_bot.callTelegramApi
    telegram_bot.callTelegramApi = fakeCallTelegramApi
    try:
      answerTelegramCallbackQuery("bot-token", "callback-1", "已处理")
    finally:
      telegram_bot.callTelegramApi = originalCallTelegramApi

    self.assertEqual(calls, [(
      "bot-token",
      "answerCallbackQuery",
      {"callback_query_id": "callback-1", "text": "已处理"},
    )])

  def testEditTelegramMessageReplyMarkupCanRemoveInlineKeyboard(self):
    calls = []

    def fakeCallTelegramApi(botToken, method, payload):
      calls.append((botToken, method, payload))
      return {}

    originalCallTelegramApi = telegram_bot.callTelegramApi
    telegram_bot.callTelegramApi = fakeCallTelegramApi
    try:
      editTelegramMessageReplyMarkup("bot-token", 12345, 99)
    finally:
      telegram_bot.callTelegramApi = originalCallTelegramApi

    self.assertEqual(calls, [(
      "bot-token",
      "editMessageReplyMarkup",
      {"chat_id": 12345, "message_id": 99},
    )])

  def testEditTelegramMessageTextCanUpdatePaginatedReview(self):
    calls = []

    def fakeCallTelegramApi(botToken, method, payload):
      calls.append((botToken, method, payload))
      return {}

    originalCallTelegramApi = telegram_bot.callTelegramApi
    telegram_bot.callTelegramApi = fakeCallTelegramApi
    try:
      editTelegramMessageText(
        "bot-token",
        12345,
        99,
        "第 2 页",
        {"inline_keyboard": [[{"text": "上一页", "callback_data": "srp:7:0"}]]},
      )
    finally:
      telegram_bot.callTelegramApi = originalCallTelegramApi

    self.assertEqual(calls, [(
      "bot-token",
      "editMessageText",
      {
        "chat_id": 12345,
        "message_id": 99,
        "text": "第 2 页",
        "reply_markup": {"inline_keyboard": [[{"text": "上一页", "callback_data": "srp:7:0"}]]},
      },
    )])

  def testUpdateArtistSubscriptionAlbumBySubscriptionIdCallsActionsApi(self):
    calls = []

    def fakeCallJsonApi(url, method="GET", payload=None):
      calls.append((url, method, payload))
      return {"updatedCount": 1}

    originalCallJsonApi = telegram_bot.callJsonApi
    telegram_bot.callJsonApi = fakeCallJsonApi
    try:
      result = updateArtistSubscriptionAlbumBySubscriptionId(
        "http://127.0.0.1:5000",
        "7",
        "111",
        "ignore",
      )
    finally:
      telegram_bot.callJsonApi = originalCallJsonApi

    self.assertEqual(result, {"updatedCount": 1})
    self.assertEqual(calls, [(
      "http://127.0.0.1:5000/api/subscriptions/7/albums/actions",
      "POST",
      {"albumIds": ["111"], "action": "ignore"},
    )])

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
    self.assertTrue(any(item["command"] == "menu" for item in commands))
    self.assertTrue(any(item["command"] == "hide_menu" for item in commands))
    self.assertTrue(any(item["command"] == "retry_failed" for item in commands))
    self.assertTrue(any(item["command"] == "force" for item in commands))
    self.assertTrue(any(item["command"] == "subscriptions" for item in commands))
    self.assertTrue(any(item["command"] == "subscription_policy" for item in commands))
    self.assertTrue(any(item["command"] == "subscription_album" for item in commands))

  def testBuildReplyKeyboardIsOneTimeAndIncludesSubscriptionShortcuts(self):
    keyboard = buildReplyKeyboard()
    buttonTexts = [
      button["text"]
      for row in keyboard["keyboard"]
      for button in row
    ]

    self.assertTrue(keyboard["one_time_keyboard"])
    self.assertNotIn("is_persistent", keyboard)
    self.assertIn("订阅说明", buttonTexts)
    self.assertIn("查看订阅", buttonTexts)
    self.assertIn("扫描订阅", buttonTexts)
    self.assertIn("收起菜单", buttonTexts)

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
    self.assertIsNone(messageCalls[0][3])


if __name__ == "__main__":
  unittest.main()
