const state = {
  selectedTaskId: "",
  manualSelection: false,
  eventSource: null,
  streamTaskId: "",
  taskPoller: null,
  historyPoller: null,
  subscriptionPoller: null,
  logLines: [],
  pendingLogLines: [],
  logFlushScheduled: false,
};

const MAX_LOG_LINES = 300;


function isTerminalTaskStatus(status) {
  return status === "completed" || status === "failed";
}


function shouldOpenNewStream(currentTaskId, nextTaskId) {
  return Boolean(nextTaskId) && currentTaskId !== nextTaskId;
}


function mergeLogLines(currentLines, pendingLines, maxLines) {
  return [...currentLines, ...pendingLines].slice(-maxLines);
}


function shouldStickToBottom(logs) {
  return logs.scrollHeight - logs.scrollTop - logs.clientHeight < 40;
}


function flushLogs() {
  if (typeof document === "undefined") {
    state.pendingLogLines = [];
    state.logFlushScheduled = false;
    return;
  }
  if (state.pendingLogLines.length === 0) {
    state.logFlushScheduled = false;
    return;
  }

  const logs = document.getElementById("logs-output");
  const stickToBottom = shouldStickToBottom(logs);
  state.logLines = mergeLogLines(state.logLines, state.pendingLogLines, MAX_LOG_LINES);
  state.pendingLogLines = [];
  logs.textContent = state.logLines.join("\n");
  if (logs.textContent) {
    logs.textContent += "\n";
  }
  if (stickToBottom) {
    logs.scrollTop = logs.scrollHeight;
  }
  state.logFlushScheduled = false;
}


function scheduleLogFlush() {
  if (state.logFlushScheduled) {
    return;
  }
  state.logFlushScheduled = true;
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(flushLogs);
    return;
  }
  setTimeout(flushLogs, 16);
}


function resetLogState() {
  state.logLines = [];
  state.pendingLogLines = [];
  state.logFlushScheduled = false;
}


function formatRetryFailedSummary(payload) {
  const retriedCount = Number(payload.retriedCount || 0);
  const skippedCompletedCount = Number(payload.skippedCompletedCount || 0);
  const skippedRunningCount = Number(payload.skippedRunningCount || 0);

  if (retriedCount === 0 && skippedCompletedCount === 0 && skippedRunningCount === 0) {
    return "当前没有可重试的失败任务";
  }

  const parts = [`已重试 ${retriedCount} 个失败任务`];
  if (skippedCompletedCount > 0) {
    parts.push(`跳过 ${skippedCompletedCount} 个已成功任务`);
  }
  if (skippedRunningCount > 0) {
    parts.push(`跳过 ${skippedRunningCount} 个进行中任务`);
  }
  return parts.join("，");
}


async function retryFailedTasks() {
  const response = await fetch("/api/tasks/retry-failed", {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "重试失败")
  }
  return payload;
}


function formatHistoryRetrySummary(payload) {
  const retriedCount = Number(payload.retriedCount || 0);
  const skippedCompletedCount = Number(payload.skippedCompletedCount || 0);
  const skippedRunningCount = Number(payload.skippedRunningCount || 0);

  if (retriedCount === 0 && skippedCompletedCount === 0 && skippedRunningCount === 0) {
    return "当前没有可重试的历史失败记录";
  }

  const parts = [`已重试 ${retriedCount} 条历史失败记录`];
  if (skippedCompletedCount > 0) {
    parts.push(`跳过 ${skippedCompletedCount} 条已成功记录`);
  }
  if (skippedRunningCount > 0) {
    parts.push(`跳过 ${skippedRunningCount} 条进行中记录`);
  }
  return parts.join("，");
}


async function retryHistoryFailedTasks() {
  const response = await fetch("/api/history/retry-failed", {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "重试失败")
  }
  return payload;
}


async function retrySingleHistory(url) {
  const response = await fetch("/api/history/retry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url })
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "重试失败")
  }
  return payload;
}


function formatSubscriptionScanSummary(payload) {
  const scannedCount = Number(payload.scannedCount ?? 1);
  const foundCount = Number(payload.foundCount ?? 0);
  const queuedCount = Number(payload.queuedCount ?? 0);
  const skippedCompletedCount = Number(payload.skippedCompletedCount ?? 0);
  const skippedActiveCount = Number(payload.skippedActiveCount ?? 0);
  const errorCount = Number(payload.errorCount ?? 0);

  return `扫描 ${scannedCount} 个订阅，发现 ${foundCount} 个专辑，入队 ${queuedCount} 个，历史跳过 ${skippedCompletedCount} 个，队列跳过 ${skippedActiveCount} 个，错误 ${errorCount} 个`;
}


function getSingleSubscriptionScanPayload(payload) {
  return {
    scannedCount: 1,
    foundCount: payload.foundCount || 0,
    queuedCount: payload.queuedCount || 0,
    skippedCompletedCount: payload.skippedCompletedCount || 0,
    skippedActiveCount: payload.skippedActiveCount || 0,
    errorCount: payload.errorCount || 0,
  };
}


function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}


async function searchArtists(term) {
  const response = await fetch("/api/subscriptions/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ term })
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "搜索失败")
  }
  return payload.results || [];
}


async function createSubscription(payload) {
  const response = await fetch("/api/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const responsePayload = await response.json();
  if (!response.ok) {
    throw new Error(responsePayload.error || "订阅失败")
  }
  return responsePayload;
}


async function fetchSubscriptions() {
  const response = await fetch("/api/subscriptions");
  if (!response.ok) {
    return [];
  }
  return response.json();
}


async function scanSubscription(subscriptionId) {
  const response = await fetch(`/api/subscriptions/${subscriptionId}/scan`, {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "扫描失败")
  }
  return payload;
}


async function scanAllSubscriptions() {
  const response = await fetch("/api/subscriptions/scan", {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "扫描失败")
  }
  return payload;
}


async function deleteSubscription(subscriptionId) {
  const response = await fetch(`/api/subscriptions/${subscriptionId}`, {
    method: "DELETE"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "删除失败")
  }
  return payload;
}


async function handleRetryFailedTasks() {
  document.getElementById("form-error").textContent = "";
  const payload = await retryFailedTasks();
  setSubmissionNote(formatRetryFailedSummary(payload));
  await refreshTaskList();
}


function appendLog(message) {
  if (!message) {
    return;
  }
  state.pendingLogLines.push(message);
  scheduleLogFlush();
}


function clearTaskDetails() {
  document.getElementById("current-url").textContent = "-";
  document.getElementById("logs-output").textContent = "";
  resetLogState();
  document.getElementById("result-list").innerHTML = '<p class="empty-text">暂无结果</p>';
  setStage("idle");
  setProgress(0);
  setStatus("等待开始", "尚未开始");
}


function setProgress(progress) {
  const safeProgress = Math.max(0, Math.min(progress, 100));
  document.getElementById("progress-bar").style.width = `${safeProgress}%`;
  document.getElementById("progress-text").textContent = `${safeProgress}%`;
}


function setStage(stage) {
  document.getElementById("stage-badge").textContent = stage || "idle";
}


function setStatus(status, message) {
  document.getElementById("status-text").textContent = status || "等待开始";
  document.getElementById("current-message").textContent = message || "";
}


function setSubmissionNote(message) {
  document.getElementById("submission-note").textContent = message || "";
}


function getTaskSummaryCounts(tasks) {
  return {
    activeCount: tasks.filter((task) => task.status === "running").length,
    queueCount: tasks.filter((task) => task.status === "queued").length,
    successCount: tasks.filter((task) => task.status === "completed").length,
    failureCount: tasks.filter((task) => task.status === "failed").length,
  };
}


function renderResult(result) {
  const container = document.getElementById("result-list");
  if (!Array.isArray(result) || result.length === 0) {
    container.innerHTML = '<p class="empty-text">暂无结果</p>';
    return;
  }
  container.innerHTML = result.map((item) => `
    <article class="result-item">
      <strong>${item.song || "未知歌曲"}</strong>
      <p>${item.artist || "未知歌手"} · ${item.album || "未知专辑"}</p>
      <p class="result-path">${item.path || ""}</p>
    </article>
  `).join("");
}


function applySnapshot(data) {
  if (data.url) {
    document.getElementById("current-url").textContent = data.url;
  }
  setStage(data.stage);
  setProgress(Number(data.progress || 0));
  setStatus(data.status, data.error || data.stage || "");
  renderResult(data.result || []);
}


function updateSummaryFromTasks(tasks) {
  const counts = getTaskSummaryCounts(tasks);
  document.getElementById("active-count").textContent = String(counts.activeCount);
  document.getElementById("queue-count").textContent = String(counts.queueCount);
  document.getElementById("success-count").textContent = String(counts.successCount);
  document.getElementById("failure-count").textContent = String(counts.failureCount);
}


function closeStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.streamTaskId = "";
}


function openStream(taskId) {
  if (!shouldOpenNewStream(state.streamTaskId, taskId)) {
    return;
  }
  closeStream();
  const eventSource = new EventSource(`/api/tasks/${taskId}/stream`);
  state.eventSource = eventSource;
  state.streamTaskId = taskId;

  eventSource.addEventListener("snapshot", (event) => {
    const data = JSON.parse(event.data);
    applySnapshot(data);
    if (isTerminalTaskStatus(data.status)) {
      closeStream();
    }
  });

  eventSource.addEventListener("log", (event) => {
    const data = JSON.parse(event.data);
    appendLog(data.message);
    setStatus("running", data.message);
  });

  eventSource.addEventListener("stage", (event) => {
    const data = JSON.parse(event.data);
    setStage(data.stage);
  });

  eventSource.addEventListener("progress", (event) => {
    const data = JSON.parse(event.data);
    setProgress(Number(data.progress || 0));
  });

  eventSource.addEventListener("result", (event) => {
    const data = JSON.parse(event.data);
    renderResult(data.result || []);
  });

  eventSource.addEventListener("error", () => {
    closeStream();
  });
}


function selectTask(taskId, manualSelection = false) {
  if (!taskId) {
    return;
  }
  const isNewSelection = state.selectedTaskId !== taskId;
  state.selectedTaskId = taskId;
  if (manualSelection) {
    state.manualSelection = true;
  }
  renderSelectedTask();
  if (shouldOpenNewStream(state.streamTaskId, taskId) || isNewSelection) {
    document.getElementById("logs-output").textContent = "";
    resetLogState();
    document.getElementById("result-list").innerHTML = '<p class="empty-text">暂无结果</p>';
    openStream(taskId);
  }
}


function renderSelectedTask() {
  const items = document.querySelectorAll(".task-item");
  for (const item of items) {
    const selected = item.dataset.taskId === state.selectedTaskId;
    item.classList.toggle("selected", selected);
  }
}


function renderTaskList(tasks) {
  const container = document.getElementById("task-list");
  if (!Array.isArray(tasks) || tasks.length === 0) {
    container.innerHTML = '<p class="empty-text">暂无任务</p>';
    if (!state.manualSelection) {
      state.selectedTaskId = "";
      clearTaskDetails();
    }
    return;
  }

  container.innerHTML = tasks.map((task) => `
    <button type="button" class="task-item${task.taskId === state.selectedTaskId ? " selected" : ""}" data-task-id="${task.taskId}">
      <div class="task-item-top">
        <span class="badge task-source ${task.source === "telegram" ? "telegram" : task.source === "subscription" ? "subscription" : "web"}">${task.source}</span>
        <span class="task-progress">${Number(task.progress || 0)}%</span>
      </div>
      <p class="task-url">${task.url || "-"}</p>
      <div class="task-meta-row">
        <span>${task.status || "pending"}</span>
        <span>${task.stage || "idle"}</span>
      </div>
    </button>
  `).join("");

  for (const item of container.querySelectorAll(".task-item")) {
    item.addEventListener("click", () => {
      selectTask(item.dataset.taskId || "", true);
    });
  }
}


function getAutoSelectedTask(tasks) {
  return tasks.find((task) => task.status === "running") || tasks[0] || null;
}


async function refreshTaskList() {
  const response = await fetch("/api/tasks");
  if (!response.ok) {
    return;
  }
  const tasks = await response.json();
  updateSummaryFromTasks(tasks);
  renderTaskList(tasks);

  if (!state.manualSelection) {
    const preferredTask = getAutoSelectedTask(tasks);
    if (preferredTask && preferredTask.taskId) {
      selectTask(preferredTask.taskId, false);
      return;
    }
  }

  if (state.selectedTaskId && !tasks.some((task) => task.taskId === state.selectedTaskId)) {
    state.selectedTaskId = "";
    state.manualSelection = false;
    closeStream();
    clearTaskDetails();
  }
}


function renderHistoryList(records) {
  const container = document.getElementById("history-list");
  if (!Array.isArray(records) || records.length === 0) {
    container.innerHTML = '<p class="empty-text">暂无历史</p>';
    return;
  }
  container.innerHTML = records.map((record) => `
    <div class="history-item${record.status === "failed" ? " history-failed" : ""}">
      <div class="history-item-top">
        <span class="badge history-status ${record.status}">${record.status}</span>
        ${record.status === "failed" ? `<button type="button" class="secondary-button history-retry-btn" data-url="${record.url}">重试</button>` : ""}
      </div>
      <p class="history-url">${record.url || "-"}</p>
      <p class="history-updated">${record.updated_at || "-"}</p>
    </div>
  `).join("");

  for (const btn of container.querySelectorAll(".history-retry-btn")) {
    btn.addEventListener("click", () => {
      const url = btn.dataset.url || "";
      handleRetrySingleHistory(url).catch((error) => {
        document.getElementById("form-error").textContent = error.message || "重试失败";
      });
    });
  }
}


async function refreshHistoryList() {
  const response = await fetch("/api/history");
  if (!response.ok) {
    return;
  }
  const records = await response.json();
  renderHistoryList(records);
}


function setSubscriptionNote(message) {
  document.getElementById("subscription-note").textContent = message || "";
}


function setSubscriptionError(message) {
  document.getElementById("subscription-error").textContent = message || "";
}


function renderArtistSearchResults(results) {
  const container = document.getElementById("artist-search-results");
  if (!Array.isArray(results) || results.length === 0) {
    container.innerHTML = '<p class="empty-text">暂无搜索结果</p>';
    return;
  }
  container.innerHTML = results.map((artist) => `
    <div class="artist-result">
      <div>
        <strong>${escapeHtml(artist.artistName || "未知歌手")}</strong>
        <p>${escapeHtml((artist.storefront || "").toUpperCase())}</p>
      </div>
      <button type="button" class="secondary-button artist-subscribe-btn" data-artist-id="${escapeHtml(artist.artistId)}">订阅</button>
    </div>
  `).join("");

  for (const btn of container.querySelectorAll(".artist-subscribe-btn")) {
    btn.addEventListener("click", () => {
      const artist = results.find((item) => String(item.artistId) === String(btn.dataset.artistId || ""));
      if (!artist) {
        return;
      }
      handleCreateSubscription({
        artistId: artist.artistId,
        storefront: artist.storefront,
        artistName: artist.artistName,
        artistUrl: artist.artistUrl,
      }).catch((error) => {
        setSubscriptionError(error.message || "订阅失败");
      });
    });
  }
}


function renderSubscriptionList(subscriptions) {
  const container = document.getElementById("subscription-list");
  if (!Array.isArray(subscriptions) || subscriptions.length === 0) {
    container.innerHTML = '<p class="empty-text">暂无订阅</p>';
    return;
  }
  container.innerHTML = subscriptions.map((subscription) => `
    <div class="subscription-item">
      <div class="subscription-item-top">
        <div>
          <strong>${escapeHtml(subscription.artistName || "未知歌手")}</strong>
          <p>${escapeHtml((subscription.storefront || "").toUpperCase())} · ${escapeHtml(subscription.artistId || "")}</p>
        </div>
        <span class="badge subscription-status">${subscription.enabled ? "enabled" : "disabled"}</span>
      </div>
      <div class="subscription-stats">
        <span>专辑 ${Number(subscription.albumCount || 0)}</span>
        <span>进行中 ${Number(subscription.activeAlbumCount || 0)}</span>
        <span>已完成 ${Number(subscription.completedAlbumCount || 0)}</span>
      </div>
      <p class="history-updated">上次扫描：${escapeHtml(subscription.lastCheckedAt || "未扫描")}</p>
      ${subscription.lastError ? `<p class="subscription-error-line">${escapeHtml(subscription.lastError)}</p>` : ""}
      <div class="subscription-actions">
        <button type="button" class="secondary-button subscription-scan-btn" data-subscription-id="${subscription.id}">扫描</button>
        <button type="button" class="secondary-button subscription-delete-btn" data-subscription-id="${subscription.id}">删除</button>
      </div>
    </div>
  `).join("");

  for (const btn of container.querySelectorAll(".subscription-scan-btn")) {
    btn.addEventListener("click", () => {
      const subscriptionId = btn.dataset.subscriptionId || "";
      handleScanSubscription(subscriptionId).catch((error) => {
        setSubscriptionError(error.message || "扫描失败");
      });
    });
  }

  for (const btn of container.querySelectorAll(".subscription-delete-btn")) {
    btn.addEventListener("click", () => {
      const subscriptionId = btn.dataset.subscriptionId || "";
      handleDeleteSubscription(subscriptionId).catch((error) => {
        setSubscriptionError(error.message || "删除失败");
      });
    });
  }
}


async function refreshSubscriptionList() {
  const subscriptions = await fetchSubscriptions();
  renderSubscriptionList(subscriptions);
}


async function handleArtistSearch(event) {
  event.preventDefault();
  setSubscriptionError("");
  setSubscriptionNote("");
  const term = document.getElementById("artist-search-input").value.trim();
  if (!term) {
    setSubscriptionError("请输入歌手名");
    return;
  }
  renderArtistSearchResults(await searchArtists(term));
}


async function handleCreateSubscription(payload) {
  setSubscriptionError("");
  setSubscriptionNote("");
  const responsePayload = await createSubscription(payload);
  if (responsePayload.scan) {
    setSubscriptionNote(formatSubscriptionScanSummary(getSingleSubscriptionScanPayload(responsePayload.scan)));
  } else {
    setSubscriptionNote("已创建订阅");
  }
  await refreshSubscriptionList();
  await refreshTaskList();
  await refreshHistoryList();
}


async function handleCreateSubscriptionFromUrl() {
  const artistUrl = document.getElementById("artist-url-input").value.trim();
  if (!artistUrl) {
    setSubscriptionError("请输入歌手链接");
    return;
  }
  await handleCreateSubscription({ artistUrl });
}


async function handleScanSubscription(subscriptionId) {
  setSubscriptionError("");
  const payload = await scanSubscription(subscriptionId);
  setSubscriptionNote(formatSubscriptionScanSummary(getSingleSubscriptionScanPayload(payload)));
  await refreshSubscriptionList();
  await refreshTaskList();
  await refreshHistoryList();
}


async function handleScanAllSubscriptions() {
  setSubscriptionError("");
  const payload = await scanAllSubscriptions();
  setSubscriptionNote(formatSubscriptionScanSummary(payload));
  await refreshSubscriptionList();
  await refreshTaskList();
  await refreshHistoryList();
}


async function handleDeleteSubscription(subscriptionId) {
  setSubscriptionError("");
  await deleteSubscription(subscriptionId);
  setSubscriptionNote("已删除订阅");
  await refreshSubscriptionList();
}


async function handleRetryHistoryFailed() {
  document.getElementById("form-error").textContent = "";
  const payload = await retryHistoryFailedTasks();
  setSubmissionNote(formatHistoryRetrySummary(payload));
  await refreshHistoryList();
  await refreshTaskList();
}


async function handleRetrySingleHistory(url) {
  document.getElementById("form-error").textContent = "";
  await retrySingleHistory(url);
  setSubmissionNote("已重新提交下载任务");
  await refreshHistoryList();
  await refreshTaskList();
}


async function submitTask(event) {
  event.preventDefault();
  document.getElementById("form-error").textContent = "";
  setSubmissionNote("");

  const url = document.getElementById("url-input").value.trim();
  const force = document.getElementById("force-checkbox").checked;

  const response = await fetch("/api/downloads", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ url, force, source: "web" })
  });

  const payload = await response.json();
  if (!response.ok) {
    document.getElementById("form-error").textContent = payload.error || "提交失败";
    return;
  }

  if (payload.status === "completed") {
    state.manualSelection = false;
    state.selectedTaskId = "";
    closeStream();
    document.getElementById("current-url").textContent = url;
    resetLogState();
    document.getElementById("logs-output").textContent = payload.message || "already downloaded";
    setStage("completed");
    setProgress(100);
    setStatus("completed", payload.message || "already downloaded");
    setSubmissionNote(force ? "强制下载已请求，但服务返回历史完成记录" : "该链接已下载，直接返回历史记录");
    renderResult(payload.result || []);
    await refreshTaskList();
    return;
  }

  state.manualSelection = false;
  selectTask(payload.taskId, false);
  if (payload.message === "download already in progress") {
    setSubmissionNote("该链接正在下载，已复用现有任务");
  } else {
    setSubmissionNote(force ? "已强制创建新的下载任务" : "已创建新下载任务");
  }
  await refreshTaskList();
}


function startTaskPolling() {
  if (state.taskPoller) {
    window.clearInterval(state.taskPoller);
  }
  state.taskPoller = window.setInterval(() => {
    refreshTaskList().catch(() => {});
  }, 3000);
}


function startHistoryPolling() {
  if (state.historyPoller) {
    window.clearInterval(state.historyPoller);
  }
  state.historyPoller = window.setInterval(() => {
    refreshHistoryList().catch(() => {});
  }, 3000);
}


function startSubscriptionPolling() {
  if (state.subscriptionPoller) {
    window.clearInterval(state.subscriptionPoller);
  }
  state.subscriptionPoller = window.setInterval(() => {
    refreshSubscriptionList().catch(() => {});
  }, 30000);
}


if (typeof document !== "undefined") {
  document.getElementById("download-form").addEventListener("submit", (event) => {
    submitTask(event).catch(() => {
      document.getElementById("form-error").textContent = "提交失败";
    });
  });

  document.getElementById("retry-failed-button").addEventListener("click", () => {
    handleRetryFailedTasks().catch((error) => {
      document.getElementById("form-error").textContent = error.message || "重试失败";
    });
  });

  document.getElementById("retry-history-failed-button").addEventListener("click", () => {
    handleRetryHistoryFailed().catch((error) => {
      document.getElementById("form-error").textContent = error.message || "重试失败";
    });
  });

  document.getElementById("subscription-search-form").addEventListener("submit", (event) => {
    handleArtistSearch(event).catch((error) => {
      setSubscriptionError(error.message || "搜索失败");
    });
  });

  document.getElementById("subscribe-url-button").addEventListener("click", () => {
    handleCreateSubscriptionFromUrl().catch((error) => {
      setSubscriptionError(error.message || "订阅失败");
    });
  });

  document.getElementById("scan-subscriptions-button").addEventListener("click", () => {
    handleScanAllSubscriptions().catch((error) => {
      setSubscriptionError(error.message || "扫描失败");
    });
  });

  clearTaskDetails();
  refreshTaskList().catch(() => {});
  refreshHistoryList().catch(() => {});
  refreshSubscriptionList().catch(() => {});
  startTaskPolling();
  startHistoryPolling();
  startSubscriptionPolling();
}

if (typeof module !== "undefined") {
  module.exports = {
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
  };
}
