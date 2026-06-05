const test = require("node:test")
const assert = require("node:assert/strict")

const {
  formatSubscriptionScanSummary,
  formatHistoryRetrySummary,
  formatRetryFailedSummary,
  getSingleSubscriptionScanPayload,
  getTaskSummaryCounts,
  isTerminalTaskStatus,
  mergeLogLines,
  retryFailedTasks,
  retryHistoryFailedTasks,
  retrySingleHistory,
  shouldOpenNewStream,
} = require("../static/app.js")

test("isTerminalTaskStatus returns true for completed and failed", () => {
  assert.equal(isTerminalTaskStatus("completed"), true)
  assert.equal(isTerminalTaskStatus("failed"), true)
  assert.equal(isTerminalTaskStatus("running"), false)
})

test("shouldOpenNewStream only opens when task changes", () => {
  assert.equal(shouldOpenNewStream("task-1", "task-1"), false)
  assert.equal(shouldOpenNewStream("task-1", "task-2"), true)
  assert.equal(shouldOpenNewStream("", "task-2"), true)
})

test("mergeLogLines keeps only latest max lines", () => {
  const result = mergeLogLines(["1", "2"], ["3", "4", "5"], 4)

  assert.deepEqual(result, ["2", "3", "4", "5"])
})

test("mergeLogLines appends pending logs in order", () => {
  const result = mergeLogLines(["old"], ["new-1", "new-2"], 10)

  assert.deepEqual(result, ["old", "new-1", "new-2"])
})

test("getTaskSummaryCounts separates queued and running", () => {
  const result = getTaskSummaryCounts([
    { status: "queued" },
    { status: "running" },
    { status: "completed" },
    { status: "failed" },
  ])

  assert.deepEqual(result, {
    activeCount: 1,
    queueCount: 1,
    successCount: 1,
    failureCount: 1,
  })
})

test("formatRetryFailedSummary reports retried and skipped counts", () => {
  const message = formatRetryFailedSummary({
    retriedCount: 2,
    skippedCompletedCount: 1,
    skippedRunningCount: 3,
  })

  assert.equal(message, "已重试 2 个失败任务，跳过 1 个已成功任务，跳过 3 个进行中任务")
})

test("formatRetryFailedSummary reports no failed tasks", () => {
  const message = formatRetryFailedSummary({
    retriedCount: 0,
    skippedCompletedCount: 0,
    skippedRunningCount: 0,
  })

  assert.equal(message, "当前没有可重试的失败任务")
})

test("formatSubscriptionScanSummary reports discovered queued skipped and errors", () => {
  const message = formatSubscriptionScanSummary({
    scannedCount: 2,
    foundCount: 8,
    queuedCount: 3,
    skippedCompletedCount: 4,
    skippedActiveCount: 1,
    errorCount: 0,
  })

  assert.equal(message, "扫描 2 个订阅，发现 8 个专辑，入队 3 个，历史跳过 4 个，队列跳过 1 个，错误 0 个")
})

test("formatSubscriptionScanSummary preserves zero scanned count", () => {
  const message = formatSubscriptionScanSummary({
    scannedCount: 0,
    foundCount: 0,
    queuedCount: 0,
    skippedCompletedCount: 0,
    skippedActiveCount: 0,
    errorCount: 0,
  })

  assert.equal(message, "扫描 0 个订阅，发现 0 个专辑，入队 0 个，历史跳过 0 个，队列跳过 0 个，错误 0 个")
})

test("getSingleSubscriptionScanPayload normalizes one subscription scan", () => {
  const payload = getSingleSubscriptionScanPayload({
    foundCount: 2,
    queuedCount: 1,
    skippedCompletedCount: 1,
    skippedActiveCount: 0,
    errorCount: 0,
  })

  assert.deepEqual(payload, {
    scannedCount: 1,
    foundCount: 2,
    queuedCount: 1,
    skippedCompletedCount: 1,
    skippedActiveCount: 0,
    errorCount: 0,
  })
})

test("retryFailedTasks posts to retry-failed endpoint", async () => {
  const originalFetch = global.fetch
  global.fetch = async (url, options) => {
    return {
      ok: true,
      async json() {
        return {
          retriedCount: 1,
          skippedCompletedCount: 0,
          skippedRunningCount: 0,
          retriedTaskIds: ["task-1"],
          skippedCompletedUrls: [],
          skippedRunningUrls: [],
        }
      },
      url,
      options,
    }
  }

  const payload = await retryFailedTasks()

  assert.deepEqual(payload, {
    retriedCount: 1,
    skippedCompletedCount: 0,
    skippedRunningCount: 0,
    retriedTaskIds: ["task-1"],
    skippedCompletedUrls: [],
    skippedRunningUrls: [],
  })

  global.fetch = originalFetch
})

test("formatHistoryRetrySummary reports retried and skipped counts", () => {
  const message = formatHistoryRetrySummary({
    retriedCount: 3,
    skippedCompletedCount: 1,
    skippedRunningCount: 2,
  })

  assert.equal(message, "已重试 3 条历史失败记录，跳过 1 条已成功记录，跳过 2 条进行中记录")
})

test("formatHistoryRetrySummary reports no failed records", () => {
  const message = formatHistoryRetrySummary({
    retriedCount: 0,
    skippedCompletedCount: 0,
    skippedRunningCount: 0,
  })

  assert.equal(message, "当前没有可重试的历史失败记录")
})

test("formatHistoryRetrySummary omits skipped when zero", () => {
  const message = formatHistoryRetrySummary({
    retriedCount: 2,
    skippedCompletedCount: 0,
    skippedRunningCount: 0,
  })

  assert.equal(message, "已重试 2 条历史失败记录")
})

test("retryHistoryFailedTasks posts to history retry-failed endpoint", async () => {
  const originalFetch = global.fetch
  global.fetch = async (url, options) => {
    return {
      ok: true,
      async json() {
        return {
          retriedCount: 2,
          retriedTaskIds: ["t1", "t2"],
          skippedCompletedCount: 1,
          skippedCompletedUrls: ["https://music.apple.com/cn/album/done/1"],
          skippedRunningCount: 0,
          skippedRunningUrls: [],
        }
      },
      url,
      options,
    }
  }

  const payload = await retryHistoryFailedTasks()

  assert.equal(payload.retriedCount, 2)
  assert.equal(payload.skippedCompletedCount, 1)
  assert.equal(payload.skippedRunningCount, 0)

  global.fetch = originalFetch
})

test("retrySingleHistory posts url to history retry endpoint", async () => {
  const originalFetch = global.fetch
  let capturedUrl = null
  let capturedOptions = null
  global.fetch = async (url, options) => {
    capturedUrl = url
    capturedOptions = options
    return {
      ok: true,
      async json() {
        return { taskId: "new-task", status: "running" }
      },
    }
  }

  const payload = await retrySingleHistory("https://music.apple.com/cn/album/test/123")

  assert.equal(capturedUrl, "/api/history/retry")
  assert.equal(capturedOptions.method, "POST")
  assert.deepEqual(JSON.parse(capturedOptions.body), { url: "https://music.apple.com/cn/album/test/123" })
  assert.deepEqual(payload, { taskId: "new-task", status: "running" })

  global.fetch = originalFetch
})
