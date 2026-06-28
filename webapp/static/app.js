const state = {
  selectedTaskId: "",
  manualSelection: false,
  activeSubscriptionId: "",
  eventSource: null,
  streamTaskId: "",
  taskPoller: null,
  historyPoller: null,
  subscriptionPoller: null,
  demoPoller: null,
  demoMode: false,
  demoLogIndex: 0,
  logLines: [],
  pendingLogLines: [],
  logFlushScheduled: false,
  subscriptionAlbumSelections: {},
  subscriptionAlbumScrollPositions: {},
  subscriptionAlbumFilters: {},
};

const MAX_LOG_LINES = 300;
const SIDEBAR_COLLAPSED_KEY = "amd-sidebar-collapsed";

const demoStore = {
  tasks: [
    {
      taskId: "demo-running",
      url: "https://music.apple.com/cn/album/prema/1819419299",
      codec: "alac",
      source: "web",
      albumName: "Prema",
      createdAt: Date.now() / 1000,
      status: "running",
      stage: "downloading",
      progress: 42,
      result: [],
      error: "",
    },
    {
      taskId: "demo-queued",
      url: "https://music.apple.com/cn/album/folklore/1524803417",
      codec: "alac",
      source: "subscription",
      albumName: "Folklore",
      createdAt: Date.now() / 1000 - 80,
      status: "queued",
      stage: "queued",
      progress: 0,
      result: [],
      error: "",
    },
    {
      taskId: "demo-failed",
      url: "https://music.apple.com/cn/album/dynamite-remixes/1529621453",
      codec: "atmos",
      source: "telegram",
      albumName: "Dynamite (Remixes)",
      createdAt: Date.now() / 1000 - 180,
      status: "failed",
      stage: "failed",
      progress: 18,
      result: [],
      error: "widevine key exchange returned HTTP 503",
    },
  ],
  history: [
    {
      url: "https://music.apple.com/cn/album/lemonade-the-2nd-album/1893599771?ls",
      status: "completed",
      source: "web",
      codec: "alac",
      task_id: "demo-history-1",
      album_id: "1893599771",
      updated_at: "2026-06-07 08:42:10",
    },
    {
      url: "https://music.apple.com/cn/album/dynamite-remixes/1529621453",
      status: "failed",
      source: "telegram",
      codec: "atmos",
      task_id: "demo-history-2",
      album_id: "1529621453",
      error: "widevine key exchange returned HTTP 503",
      updated_at: "2026-06-07 08:38:03",
    },
  ],
  subscriptions: [
    {
      id: 1,
      artistId: "159260351",
      storefront: "us",
      artistName: "Taylor Swift",
      artistUrl: "https://music.apple.com/us/artist/taylor-swift/159260351",
      artistArtworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Features125/v4/demo/512x512bb.jpg",
      enabled: true,
      newAlbumPolicy: "confirm",
      albumCount: 3,
      pendingAlbumCount: 0,
      activeAlbumCount: 2,
      completedAlbumCount: 1,
      failedAlbumCount: 0,
      ignoredAlbumCount: 0,
      importedAlbumCount: 0,
      lastCheckedAt: "2026-06-07 08:40:00",
      lastError: "",
      recentAlbums: [
        {
          albumId: "1819419299",
          albumName: "Prema",
          albumUrl: "https://music.apple.com/cn/album/prema/1819419299",
          artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/demo1/512x512bb.jpg",
          releaseDate: "2026-06-05",
          status: "running",
          userState: "subscribed",
          detectedStatus: "running",
        },
        {
          albumId: "1524803417",
          albumName: "Folklore",
          albumUrl: "https://music.apple.com/cn/album/folklore/1524803417",
          artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Music115/v4/demo2/512x512bb.jpg",
          releaseDate: "2020-07-24",
          status: "queued",
          userState: "subscribed",
          detectedStatus: "queued",
        },
        {
          albumId: "1893599771",
          albumName: "Lemonade - The 2nd Album",
          albumUrl: "https://music.apple.com/cn/album/lemonade-the-2nd-album/1893599771",
          artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/demo3/512x512bb.jpg",
          releaseDate: "2026-05-18",
          status: "completed",
          userState: "subscribed",
          detectedStatus: "completed",
        },
      ],
    },
    {
      id: 2,
      artistId: "471744",
      storefront: "cn",
      artistName: "陈奕迅",
      artistUrl: "https://music.apple.com/cn/artist/eason-chan/471744",
      artistArtworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Features115/v4/demo/512x512bb.jpg",
      enabled: true,
      newAlbumPolicy: "confirm",
      albumCount: 2,
      pendingAlbumCount: 1,
      activeAlbumCount: 0,
      completedAlbumCount: 1,
      failedAlbumCount: 0,
      ignoredAlbumCount: 0,
      importedAlbumCount: 0,
      lastCheckedAt: "2026-06-07 07:12:00",
      lastError: "",
      recentAlbums: [
        {
          albumId: "47174401",
          albumName: "CHIN UP!",
          albumUrl: "https://music.apple.com/cn/album/chin-up/47174401",
          artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/demo4/512x512bb.jpg",
          releaseDate: "2025-10-10",
          status: "completed",
          userState: "subscribed",
          detectedStatus: "completed",
        },
        {
          albumId: "47174402",
          albumName: "准备中",
          albumUrl: "https://music.apple.com/cn/album/demo/47174402",
          artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/demo5/512x512bb.jpg",
          releaseDate: "",
          status: "seen",
          userState: "pending",
          detectedStatus: "missing",
          canDownload: true,
        },
      ],
    },
  ],
};

const DEMO_LOG_LINES = [
  "解析 Apple Music metadata...",
  "匹配到 ALAC 最高音质资源",
  "连接远程下载器容器 applemusic_download",
  "开始分块下载主音轨",
  "下载进度超过 60%，歌词与封面元数据已写入",
  "调用 FFmpeg 转换为 FLAC",
  "准备生成 album.nfo 并移动到 completed 目录",
];


function isDemoModeEnabled() {
  if (typeof window === "undefined") {
    return false;
  }
  const params = new URLSearchParams(window.location.search);
  return params.get("demo") === "1" || window.localStorage.getItem("amd-demo") === "1";
}


function cloneDemo(value) {
  return JSON.parse(JSON.stringify(value));
}


function getInitialViewName() {
  if (typeof window === "undefined") {
    return "console";
  }
  const candidate = window.location.hash.replace("#", "");
  return ["console", "queue", "subscriptions", "history"].includes(candidate) ? candidate : "console";
}


function showView(viewName, updateHash = true) {
  const safeView = ["console", "queue", "subscriptions", "history"].includes(viewName) ? viewName : "console";
  for (const panel of document.querySelectorAll(".view-panel")) {
    panel.hidden = panel.dataset.view !== safeView;
  }
  for (const link of document.querySelectorAll(".nav-link[data-view-target]")) {
    link.classList.toggle("active", link.dataset.viewTarget === safeView);
  }
  if (updateHash && window.location.hash !== `#${safeView}`) {
    window.history.replaceState(null, "", `#${safeView}`);
  }
}


function bindViewNavigation() {
  for (const link of document.querySelectorAll(".nav-link[data-view-target]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showView(link.dataset.viewTarget || "console");
    });
  }
  window.addEventListener("hashchange", () => {
    showView(getInitialViewName(), false);
  });
}


function isSidebarCollapsedStored() {
  if (typeof window === "undefined" || !window.localStorage) {
    return false;
  }
  return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
}


function setSidebarCollapsed(collapsed, persist = true) {
  if (typeof document === "undefined") {
    return;
  }
  document.body.classList.toggle("sidebar-collapsed", collapsed);

  const toggle = document.getElementById("sidebar-toggle");
  if (toggle) {
    const label = collapsed ? "展开侧栏" : "收起侧栏";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
  }

  if (persist && typeof window !== "undefined" && window.localStorage) {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
  }
}


function bindSidebarToggle() {
  const toggle = document.getElementById("sidebar-toggle");
  if (!toggle) {
    return;
  }

  setSidebarCollapsed(isSidebarCollapsedStored(), false);
  toggle.addEventListener("click", () => {
    setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
  });
}


function isTerminalTaskStatus(status) {
  return status === "completed" || status === "failed" || status === "cancelled";
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
  if (state.demoMode) {
    return { retriedCount: 1, skippedCompletedCount: 0, skippedRunningCount: 1 };
  }
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
  if (state.demoMode) {
    return { retriedCount: 1, skippedCompletedCount: 1, skippedRunningCount: 0 };
  }
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
  if (state.demoMode) {
    demoStore.tasks.unshift({
      taskId: `demo-retry-${Date.now()}`,
      url,
      codec: "alac",
      source: "web",
      createdAt: Date.now() / 1000,
      status: "queued",
      stage: "queued",
      progress: 0,
      result: [],
      error: "",
    });
    return { taskId: demoStore.tasks[0].taskId, status: "queued" };
  }
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


function taskUrlContainsAlbumId(task, albumId) {
  return Boolean(task?.url && albumId && String(task.url).includes(`/${albumId}`));
}


function recalculateDemoSubscriptionStats(subscription) {
  const albums = Array.isArray(subscription.recentAlbums) ? subscription.recentAlbums : [];
  subscription.albumCount = albums.length;
  subscription.pendingAlbumCount = albums.filter((album) => normalizeAlbumUserState(album.userState) === "pending").length;
  subscription.activeAlbumCount = albums.filter((album) => ["queued", "running"].includes(getAlbumDetectedStatus(album)) || ["queued", "running"].includes(normalizeAlbumStatus(album.status))).length;
  subscription.completedAlbumCount = albums.filter((album) => getAlbumDetectedStatus(album) === "completed" || normalizeAlbumStatus(album.status) === "completed").length;
  subscription.failedAlbumCount = albums.filter((album) => ["failed_history", "stale_history"].includes(getAlbumDetectedStatus(album)) || normalizeAlbumStatus(album.status) === "failed").length;
  subscription.ignoredAlbumCount = albums.filter((album) => normalizeAlbumUserState(album.userState) === "ignored").length;
  subscription.importedAlbumCount = albums.filter((album) => normalizeAlbumUserState(album.userState) === "imported").length;
}


function updateDemoSubscriptionAfterTaskCancellation(task) {
  for (const subscription of demoStore.subscriptions) {
    let changed = false;
    for (const album of subscription.recentAlbums || []) {
      if (album.albumUrl !== task.url && !taskUrlContainsAlbumId(task, album.albumId)) {
        continue;
      }
      album.status = "seen";
      album.detectedStatus = "missing";
      if (normalizeAlbumUserState(album.userState) === "subscribed") {
        album.userState = "pending";
      }
      changed = true;
    }
    if (changed) {
      recalculateDemoSubscriptionStats(subscription);
    }
  }
}


async function cancelTask(taskId) {
  if (state.demoMode) {
    const task = getDemoTask(taskId);
    if (!task) {
      throw new Error("任务不存在");
    }
    if (task.status !== "queued") {
      throw new Error("只能取消排队任务");
    }
    task.status = "cancelled";
    task.stage = "cancelled";
    task.error = "cancelled before start";
    task.progress = 0;
    updateDemoSubscriptionAfterTaskCancellation(task);
    return { cancelled: true, task };
  }
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "取消失败")
  }
  return payload;
}


function formatSubscriptionScanSummary(payload) {
  const scannedCount = Number(payload.scannedCount ?? 1);
  const foundCount = Number(payload.foundCount ?? 0);
  const queuedCount = Number(payload.queuedCount ?? 0);
  const pendingCount = Number(payload.pendingCount ?? 0);
  const skippedCompletedCount = Number(payload.skippedCompletedCount ?? 0);
  const skippedActiveCount = Number(payload.skippedActiveCount ?? 0);
  const skippedIgnoredCount = Number(payload.skippedIgnoredCount ?? 0);
  const skippedImportedCount = Number(payload.skippedImportedCount ?? 0);
  const errorCount = Number(payload.errorCount ?? 0);

  return `扫描 ${scannedCount} 个订阅，发现 ${foundCount} 个专辑，待确认 ${pendingCount} 个，入队 ${queuedCount} 个，历史跳过 ${skippedCompletedCount} 个，队列跳过 ${skippedActiveCount} 个，忽略 ${skippedIgnoredCount} 个，已导入 ${skippedImportedCount} 个，错误 ${errorCount} 个`;
}


function getSingleSubscriptionScanPayload(payload) {
  return {
    scannedCount: 1,
    foundCount: payload.foundCount || 0,
    queuedCount: payload.queuedCount || 0,
    pendingCount: payload.pendingCount || 0,
    skippedCompletedCount: payload.skippedCompletedCount || 0,
    skippedActiveCount: payload.skippedActiveCount || 0,
    skippedIgnoredCount: payload.skippedIgnoredCount || 0,
    skippedImportedCount: payload.skippedImportedCount || 0,
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


function getSafeImageUrl(value) {
  const rawUrl = String(value || "").trim();
  if (!/^https?:\/\//i.test(rawUrl)) {
    return "";
  }
  try {
    const parsed = new URL(rawUrl);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "";
  } catch {
    return "";
  }
}


function getCachedArtworkUrl(value) {
  const safeUrl = getSafeImageUrl(value);
  return safeUrl ? `/api/artwork?url=${encodeURIComponent(safeUrl)}` : "";
}


function renderArtworkImage(url, alt, className) {
  const safeUrl = getSafeImageUrl(url);
  const escapedAlt = escapeHtml(alt || "artwork");
  if (!safeUrl) {
    return `<div class="${className} artwork-placeholder" aria-hidden="true">封面</div>`;
  }
  return `<img class="${className}" src="${escapeHtml(getCachedArtworkUrl(safeUrl))}" alt="${escapedAlt}" loading="lazy" decoding="async">`;
}


function attachArtworkFallbackHandlers(root) {
  if (!root || typeof root.querySelectorAll !== "function" || typeof document === "undefined" || typeof document.createElement !== "function") {
    return;
  }
  for (const image of root.querySelectorAll("img.artist-result-artwork, img.subscription-card-artwork, img.subscription-detail-artwork, img.subscription-album-artwork")) {
    const replaceWithPlaceholder = () => {
      if (!image.parentNode) {
        return;
      }
      const placeholder = document.createElement("div");
      placeholder.className = `${image.className} artwork-placeholder`;
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.textContent = "封面";
      image.replaceWith(placeholder);
    };
    image.addEventListener("error", replaceWithPlaceholder, { once: true });
    if (image.complete && image.naturalWidth === 0) {
      replaceWithPlaceholder();
    }
  }
}


function normalizeHistoryStatus(status) {
  const normalized = String(status || "").trim().toLowerCase();
  return ["completed", "failed", "running", "queued", "cancelled"].includes(normalized) ? normalized : "unknown";
}


function formatAlbumTitleFromUrl(url) {
  const rawUrl = String(url || "").trim();
  if (!rawUrl) {
    return "";
  }
  let pathname = "";
  try {
    pathname = new URL(rawUrl).pathname;
  } catch {
    pathname = rawUrl.split("?")[0];
  }
  const parts = pathname.split("/").filter(Boolean);
  const albumIndex = parts.indexOf("album");
  const slug = albumIndex >= 0 ? parts[albumIndex + 1] : "";
  if (!slug) {
    return "";
  }
  return decodeURIComponent(slug)
    .split("-")
    .filter(Boolean)
    .map((word) => word ? `${word.charAt(0).toUpperCase()}${word.slice(1)}` : "")
    .join(" ");
}


function getTaskAlbumName(task) {
  if (!task) {
    return "";
  }
  if (task.albumName) {
    return String(task.albumName);
  }
  if (Array.isArray(task.result) && task.result.length > 0 && task.result[0]?.album) {
    return String(task.result[0].album);
  }
  return formatAlbumTitleFromUrl(task.url);
}


async function searchArtists(term) {
  if (state.demoMode) {
    return [
      {
        artistId: "159260351",
        storefront: "us",
        artistName: term || "Taylor Swift",
        artistUrl: "https://music.apple.com/us/artist/taylor-swift/159260351",
      },
      {
        artistId: "471744",
        storefront: "cn",
        artistName: "陈奕迅",
        artistUrl: "https://music.apple.com/cn/artist/eason-chan/471744",
      },
    ];
  }
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
  if (state.demoMode) {
    const id = Date.now();
    demoStore.subscriptions.unshift({
      id,
      artistId: payload.artistId || String(id),
      storefront: payload.storefront || "cn",
      artistName: payload.artistName || "Demo Artist",
      artistUrl: payload.artistUrl || "",
      enabled: true,
      newAlbumPolicy: "confirm",
      albumCount: 0,
      pendingAlbumCount: 0,
      activeAlbumCount: 0,
      completedAlbumCount: 0,
      failedAlbumCount: 0,
      ignoredAlbumCount: 0,
      importedAlbumCount: 0,
      lastCheckedAt: "",
      lastError: "",
      recentAlbums: [],
    });
    return {
      subscription: demoStore.subscriptions[0],
      scan: {
        foundCount: 3,
        queuedCount: 0,
        pendingCount: 3,
        skippedCompletedCount: 2,
        skippedActiveCount: 0,
        skippedIgnoredCount: 0,
        skippedImportedCount: 0,
        errorCount: 0,
      },
    };
  }
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
  if (state.demoMode) {
    return cloneDemo(demoStore.subscriptions);
  }
  const response = await fetch("/api/subscriptions");
  if (!response.ok) {
    return [];
  }
  return response.json();
}


async function scanSubscription(subscriptionId) {
  if (state.demoMode) {
    return {
      subscriptionId,
      foundCount: 4,
      queuedCount: 0,
      pendingCount: 1,
      skippedCompletedCount: 2,
      skippedActiveCount: 0,
      skippedIgnoredCount: 1,
      skippedImportedCount: 0,
      errorCount: 0,
    };
  }
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
  if (state.demoMode) {
    return {
      scannedCount: demoStore.subscriptions.length,
      foundCount: 8,
      queuedCount: 2,
      pendingCount: 2,
      skippedCompletedCount: 5,
      skippedActiveCount: 1,
      skippedIgnoredCount: 1,
      skippedImportedCount: 1,
      errorCount: 0,
    };
  }
  const response = await fetch("/api/subscriptions/scan", {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "扫描失败")
  }
  return payload;
}


async function updateSubscriptionPolicy(subscriptionId, newAlbumPolicy) {
  if (state.demoMode) {
    const subscription = demoStore.subscriptions.find((item) => String(item.id) === String(subscriptionId));
    if (subscription) {
      subscription.newAlbumPolicy = newAlbumPolicy === "auto" ? "auto" : "confirm";
    }
    return { subscription };
  }
  const response = await fetch(`/api/subscriptions/${subscriptionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ newAlbumPolicy })
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "策略更新失败")
  }
  return payload;
}


async function applySubscriptionAlbumAction(subscriptionId, albumIds, action) {
  const normalizedAlbumIds = Array.isArray(albumIds) ? albumIds : [albumIds];
  if (state.demoMode) {
    const subscription = demoStore.subscriptions.find((item) => String(item.id) === String(subscriptionId));
    const updatedAlbumIds = [];
    let queuedCount = 0;
    if (subscription && Array.isArray(subscription.recentAlbums)) {
      for (const album of subscription.recentAlbums) {
        if (!normalizedAlbumIds.includes(String(album.albumId))) {
          continue;
        }
        if (action === "download") {
          album.userState = "subscribed";
          album.detectedStatus = "queued";
          album.status = "queued";
          queuedCount += 1;
        } else if (action === "ignore") {
          album.userState = "ignored";
        } else if (action === "mark_imported") {
          album.userState = "imported";
        } else if (action === "mark_completed") {
          album.userState = "subscribed";
          album.detectedStatus = "completed";
          album.status = "completed";
          album.taskId = "";
        } else if (action === "pending") {
          album.userState = "pending";
        }
        updatedAlbumIds.push(String(album.albumId));
      }
    }
    if (subscription) {
      recalculateDemoSubscriptionStats(subscription);
    }
    return {
      action,
      updatedCount: updatedAlbumIds.length,
      updatedAlbumIds,
      queuedCount,
      pendingCount: 0,
      skippedCompletedCount: 0,
      skippedActiveCount: 0,
      skippedIgnoredCount: 0,
      skippedImportedCount: 0,
      errorCount: 0,
    };
  }
  const response = await fetch(`/api/subscriptions/${subscriptionId}/albums/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ albumIds: normalizedAlbumIds, action })
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "专辑操作失败")
  }
  return payload;
}


async function deleteSubscription(subscriptionId) {
  if (state.demoMode) {
    const index = demoStore.subscriptions.findIndex((item) => String(item.id) === String(subscriptionId));
    if (index >= 0) {
      demoStore.subscriptions.splice(index, 1);
    }
    return { deleted: true };
  }
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


function applySnapshotLogs(logs) {
  if (!Array.isArray(logs) || logs.length === 0) {
    return;
  }
  if (state.logLines.length > 0 || state.pendingLogLines.length > 0) {
    return;
  }
  state.logLines = logs.slice(-MAX_LOG_LINES);
  const logsOutput = document.getElementById("logs-output");
  logsOutput.textContent = state.logLines.join("\n");
  if (logsOutput.textContent) {
    logsOutput.textContent += "\n";
    logsOutput.scrollTop = logsOutput.scrollHeight;
  }
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


function getDemoTasks() {
  return cloneDemo(demoStore.tasks);
}


function getDemoTask(taskId) {
  return demoStore.tasks.find((task) => task.taskId === taskId) || null;
}


function getDemoResult(task) {
  if (!task || task.status === "queued") {
    return [];
  }
  if (task.result && task.result.length > 0) {
    return task.result;
  }
  if (task.status === "failed") {
    return [];
  }
  return [
    {
      song: "Demo Track",
      artist: "Demo Artist",
      album: "Demo Album",
      path: "C:\\downloads\\completed\\Demo Artist\\Demo Album\\01 Demo Track.flac",
    },
  ];
}


function setDemoLogs(task) {
  const logs = document.getElementById("logs-output");
  const progress = Number(task?.progress || 0);
  const visibleLogs = [
    "[demo] 本地预览模式已启用，不会提交真实下载任务",
    `[demo] 当前任务: ${task?.url || "-"}`,
    ...DEMO_LOG_LINES.slice(0, Math.max(2, Math.ceil(progress / 18))),
  ];
  state.logLines = visibleLogs;
  state.pendingLogLines = [];
  logs.textContent = `${visibleLogs.join("\n")}\n`;
  logs.scrollTop = logs.scrollHeight;
}


function applyDemoTaskSnapshot(taskId) {
  const task = getDemoTask(taskId);
  if (!task) {
    clearTaskDetails();
    return;
  }
  document.getElementById("current-url").textContent = task.url;
  setStage(task.stage);
  setProgress(Number(task.progress || 0));
  setStatus(task.status, task.error || task.stage || "");
  renderResult(getDemoResult(task));
  setDemoLogs(task);
}


function advanceDemoTask() {
  const task = demoStore.tasks.find((item) => item.status === "running");
  if (!task) {
    return;
  }
  task.progress = Math.min(96, Number(task.progress || 0) + 3);
  if (task.progress >= 90) {
    task.stage = "building_nfo";
  } else if (task.progress >= 72) {
    task.stage = "post_processing";
  } else {
    task.stage = "downloading";
  }
  if (task.taskId === state.selectedTaskId) {
    applyDemoTaskSnapshot(task.taskId);
  }
}


function submitDemoTask(url, force) {
  const task = {
    taskId: `demo-${Date.now()}`,
    url,
    codec: "alac",
    source: "web",
    createdAt: Date.now() / 1000,
    status: demoStore.tasks.some((item) => item.status === "running") ? "queued" : "running",
    stage: demoStore.tasks.some((item) => item.status === "running") ? "queued" : "downloading",
    progress: 0,
    result: [],
    error: "",
  };
  demoStore.tasks.unshift(task);
  state.manualSelection = false;
  selectTask(task.taskId, false);
  setSubmissionNote(force ? "Demo：已模拟创建强制下载任务" : "Demo：已模拟创建下载任务");
  document.getElementById("url-input").value = "";
  refreshTaskList().catch(() => {});
}


function startDemoSimulation() {
  if (state.demoPoller) {
    window.clearInterval(state.demoPoller);
  }
  state.demoPoller = window.setInterval(() => {
    advanceDemoTask();
    refreshTaskList().catch(() => {});
  }, 1800);
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
      <strong>${escapeHtml(item.song || "未知歌曲")}</strong>
      <p>${escapeHtml(item.artist || "未知歌手")} · ${escapeHtml(item.album || "未知专辑")}</p>
      <p class="result-path">${escapeHtml(item.path || "")}</p>
    </article>
  `).join("");
}


function applySnapshot(data) {
  if (data.url) {
    document.getElementById("current-url").textContent = data.url;
  }
  applySnapshotLogs(data.logs);
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
  if (state.demoMode) {
    closeStream();
    applyDemoTaskSnapshot(taskId);
    return;
  }
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

  container.innerHTML = tasks.map((task) => {
    const source = task.source === "telegram" ? "telegram" : task.source === "subscription" ? "subscription" : "web";
    const albumName = getTaskAlbumName(task) || "未知专辑";
    const canCancel = task.status === "queued";
    const errorReason = task.status === "failed" && task.error
      ? `<p class="task-error-reason">失败原因：${escapeHtml(task.error)}</p>`
      : "";
    return `
      <article class="task-item${task.taskId === state.selectedTaskId ? " selected" : ""}" data-task-id="${escapeHtml(task.taskId || "")}" role="button" tabindex="0">
        <div class="task-item-top">
          <span class="badge task-source ${source}">${escapeHtml(task.source || source)}</span>
          <span class="task-progress">${Number(task.progress || 0)}%</span>
        </div>
        <strong class="task-album-title">${escapeHtml(albumName)}</strong>
        <p class="task-url">${escapeHtml(task.url || "-")}</p>
        <div class="task-meta-row">
          <span>${escapeHtml(task.status || "pending")}</span>
          <span>${escapeHtml(task.stage || "idle")}</span>
        </div>
        ${errorReason}
        ${canCancel ? `<div class="task-actions"><button type="button" class="secondary-button compact-button task-cancel-btn" data-task-id="${escapeHtml(task.taskId || "")}">取消</button></div>` : ""}
      </article>
    `;
  }).join("");

  for (const item of container.querySelectorAll(".task-item")) {
    item.addEventListener("click", (event) => {
      if (event.target.closest("button")) {
        return;
      }
      selectTask(item.dataset.taskId || "", true);
    });
    item.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      event.preventDefault();
      selectTask(item.dataset.taskId || "", true);
    });
  }

  for (const btn of container.querySelectorAll(".task-cancel-btn")) {
    btn.addEventListener("click", () => {
      handleCancelTask(btn.dataset.taskId || "").catch((error) => {
        document.getElementById("form-error").textContent = error.message || "取消失败";
      });
    });
  }
}


function getAutoSelectedTask(tasks) {
  return tasks.find((task) => task.status === "running") || tasks[0] || null;
}


async function refreshTaskList() {
  if (state.demoMode) {
    const tasks = getDemoTasks();
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
      clearTaskDetails();
    }
    return;
  }

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
  container.innerHTML = records.map((record) => {
    const status = normalizeHistoryStatus(record.status);
    const rawStatus = record.status || "-";
    const url = record.url || "-";
    const errorReason = status === "failed" && record.error
      ? `<p class="history-error-reason">失败原因：${escapeHtml(record.error)}</p>`
      : "";
    return `
      <div class="history-item${status === "failed" ? " history-failed" : ""}">
        <div class="history-item-top">
          <span class="badge history-status ${status}">${escapeHtml(rawStatus)}</span>
          ${status === "failed" ? `<button type="button" class="secondary-button history-retry-btn" data-url="${escapeHtml(url)}">重试</button>` : ""}
        </div>
        <p class="history-url">${escapeHtml(url)}</p>
        ${errorReason}
        <p class="history-updated">${escapeHtml(record.updated_at || "-")}</p>
      </div>
    `;
  }).join("");

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
  if (state.demoMode) {
    renderHistoryList(cloneDemo(demoStore.history));
    return;
  }
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


function setSubscriptionFormExpanded(expanded) {
  const panel = document.getElementById("subscription-form-panel");
  const button = document.getElementById("toggle-subscription-form-button");
  if (!panel || !button) {
    return;
  }
  panel.hidden = !expanded;
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  button.textContent = "新增订阅";
  document.body.classList.toggle("subscription-modal-open", expanded);
  if (expanded) {
    window.setTimeout(() => {
      document.getElementById("artist-search-input")?.focus();
    }, 0);
  } else if (document.activeElement && panel.contains(document.activeElement)) {
    button.focus();
  }
}


function toggleSubscriptionFormPanel() {
  const panel = document.getElementById("subscription-form-panel");
  if (!panel) {
    return;
  }
  setSubscriptionFormExpanded(panel.hidden);
}


function bindSubscriptionFormModal() {
  const panel = document.getElementById("subscription-form-panel");
  const closeButton = document.getElementById("close-subscription-form-button");
  if (!panel || !closeButton) {
    return;
  }
  closeButton.addEventListener("click", () => {
    setSubscriptionFormExpanded(false);
  });
  panel.addEventListener("click", (event) => {
    if (event.target === panel) {
      setSubscriptionFormExpanded(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      setSubscriptionFormExpanded(false);
    }
  });
}


function renderArtistSearchResults(results) {
  const container = document.getElementById("artist-search-results");
  if (!Array.isArray(results) || results.length === 0) {
    container.innerHTML = '<p class="empty-text">暂无搜索结果</p>';
    return;
  }
  container.innerHTML = `
    <div class="artist-search-results-title">搜索结果 · 点击添加订阅</div>
    ${results.map((artist) => `
      <div class="artist-result">
        <div class="artist-result-main">
          ${renderArtworkImage(artist.artistArtworkUrl, artist.artistName || "未知歌手", "artist-result-artwork")}
          <div>
            <strong>${escapeHtml(artist.artistName || "未知歌手")}</strong>
            <p>${escapeHtml((artist.storefront || "").toUpperCase())} ${artist.artistId ? `· ${escapeHtml(artist.artistId)}` : ""}</p>
          </div>
        </div>
        <button type="button" class="primary-button compact-button artist-subscribe-btn" data-artist-id="${escapeHtml(artist.artistId)}">添加订阅</button>
      </div>
    `).join("")}
  `;
  attachArtworkFallbackHandlers(container);

  for (const btn of container.querySelectorAll(".artist-subscribe-btn")) {
    btn.addEventListener("click", () => {
      const artist = results.find((item) => String(item.artistId) === String(btn.dataset.artistId || ""));
      if (!artist) {
        return;
      }
      btn.disabled = true;
      btn.textContent = "添加中";
      handleCreateSubscription({
        artistId: artist.artistId,
        storefront: artist.storefront,
        artistName: artist.artistName,
        artistUrl: artist.artistUrl,
        artistArtworkUrl: artist.artistArtworkUrl,
      }).then(() => {
        setSubscriptionFormExpanded(false);
      }).catch((error) => {
        btn.disabled = false;
        btn.textContent = "添加订阅";
        setSubscriptionError(error.message || "订阅失败");
      });
    });
  }
}


function normalizeAlbumStatus(status) {
  const normalized = String(status || "seen").toLowerCase();
  return ["completed", "running", "queued", "failed", "seen", "missing", "failed_history", "stale_history"].includes(normalized) ? normalized : "seen";
}


function normalizeAlbumUserState(userState) {
  const normalized = String(userState || "subscribed").toLowerCase();
  return ["pending", "subscribed", "ignored", "imported"].includes(normalized) ? normalized : "subscribed";
}


function getAlbumDetectedStatus(album) {
  return normalizeAlbumStatus(album?.detectedStatus || album?.status || "seen");
}


function getAlbumStatusLabel(status) {
  const labels = {
    completed: "已完成",
    running: "下载中",
    queued: "队列中",
    failed: "失败",
    failed_history: "失败历史",
    stale_history: "历史不可用",
    missing: "待处理",
    seen: "已记录",
  };
  return labels[normalizeAlbumStatus(status)] || "已记录";
}


function getAlbumUserStateLabel(userState) {
  const labels = {
    pending: "待确认",
    subscribed: "已确认",
    ignored: "已忽略",
    imported: "已导入",
  };
  return labels[normalizeAlbumUserState(userState)] || "已确认";
}


function canDownloadAlbum(album) {
  if (album?.canDownload === true) {
    return true;
  }
  const detectedStatus = getAlbumDetectedStatus(album);
  const userState = normalizeAlbumUserState(album?.userState);
  return ["missing", "failed_history", "stale_history"].includes(detectedStatus) && !["ignored", "imported"].includes(userState);
}


function canRestoreAlbum(album) {
  return ["ignored", "imported"].includes(normalizeAlbumUserState(album?.userState));
}


function isSelectableSubscriptionAlbum(album) {
  return normalizeAlbumUserState(album?.userState) === "pending" && canDownloadAlbum(album);
}


function normalizeSelectionKey(value) {
  return String(value ?? "").trim();
}


function getSubscriptionAlbumSelection(subscriptionId) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (!subscriptionKey) {
    return new Set();
  }
  const existing = state.subscriptionAlbumSelections[subscriptionKey];
  if (existing instanceof Set) {
    return existing;
  }
  const normalized = new Set(Array.isArray(existing) ? existing.map(normalizeSelectionKey).filter(Boolean) : []);
  state.subscriptionAlbumSelections[subscriptionKey] = normalized;
  return normalized;
}


function setSubscriptionAlbumSelection(subscriptionId, albumIds) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (!subscriptionKey) {
    return [];
  }
  const normalizedAlbumIds = [...new Set((Array.isArray(albumIds) ? albumIds : [albumIds]).map(normalizeSelectionKey).filter(Boolean))];
  if (normalizedAlbumIds.length === 0) {
    delete state.subscriptionAlbumSelections[subscriptionKey];
    return [];
  }
  state.subscriptionAlbumSelections[subscriptionKey] = new Set(normalizedAlbumIds);
  return normalizedAlbumIds;
}


function setSubscriptionAlbumSelected(subscriptionId, albumId, selected) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  const albumKey = normalizeSelectionKey(albumId);
  if (!subscriptionKey || !albumKey) {
    return [];
  }
  const selection = getSubscriptionAlbumSelection(subscriptionKey);
  if (selected) {
    selection.add(albumKey);
  } else {
    selection.delete(albumKey);
  }
  if (selection.size === 0) {
    delete state.subscriptionAlbumSelections[subscriptionKey];
    return [];
  }
  return [...selection];
}


function clearSubscriptionAlbumSelection(subscriptionId) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (subscriptionKey) {
    delete state.subscriptionAlbumSelections[subscriptionKey];
  }
}


function clearSubscriptionAlbumSelectionIds(subscriptionId, albumIds) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (!subscriptionKey) {
    return [];
  }
  const selection = getSubscriptionAlbumSelection(subscriptionKey);
  for (const albumId of Array.isArray(albumIds) ? albumIds : [albumIds]) {
    selection.delete(normalizeSelectionKey(albumId));
  }
  if (selection.size === 0) {
    delete state.subscriptionAlbumSelections[subscriptionKey];
    return [];
  }
  return [...selection];
}


function clearAllSubscriptionAlbumSelections() {
  state.subscriptionAlbumSelections = {};
}


function findSubscriptionDetailPanel(root, subscriptionId = "") {
  if (!root || typeof root.querySelector !== "function") {
    return null;
  }
  const targetId = normalizeSelectionKey(subscriptionId);
  if (targetId && typeof root.querySelectorAll === "function") {
    for (const panel of root.querySelectorAll(".subscription-detail-panel")) {
      if (normalizeSelectionKey(panel?.dataset?.subscriptionId) === targetId) {
        return panel;
      }
    }
  }
  return root.querySelector(".subscription-detail-panel");
}


function getSubscriptionAlbumScrollElement(root, subscriptionId = "") {
  const detailPanel = findSubscriptionDetailPanel(root, subscriptionId);
  if (!detailPanel || typeof detailPanel.querySelector !== "function") {
    return null;
  }
  return detailPanel.querySelector(".subscription-albums ul");
}


function saveSubscriptionAlbumScrollPosition(root, subscriptionId = "") {
  const detailPanel = findSubscriptionDetailPanel(root, subscriptionId);
  const albumList = detailPanel?.querySelector?.(".subscription-albums ul") || null;
  const resolvedSubscriptionId = normalizeSelectionKey(subscriptionId || detailPanel?.dataset?.subscriptionId);
  if (!resolvedSubscriptionId || !albumList) {
    return 0;
  }
  const scrollTop = Math.max(0, Number(albumList.scrollTop || 0));
  state.subscriptionAlbumScrollPositions[resolvedSubscriptionId] = scrollTop;
  return scrollTop;
}


function restoreSubscriptionAlbumScrollPosition(root, subscriptionId) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (!subscriptionKey) {
    return 0;
  }
  const albumList = getSubscriptionAlbumScrollElement(root, subscriptionKey);
  if (!albumList) {
    return 0;
  }
  const scrollTop = Math.max(0, Number(state.subscriptionAlbumScrollPositions[subscriptionKey] || 0));
  albumList.scrollTop = scrollTop;
  return scrollTop;
}


function clearSubscriptionAlbumScrollPosition(subscriptionId) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (subscriptionKey) {
    delete state.subscriptionAlbumScrollPositions[subscriptionKey];
  }
}


function clearAllSubscriptionAlbumScrollPositions() {
  state.subscriptionAlbumScrollPositions = {};
}


function getSubscriptionAlbumFilter(subscriptionId) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  const existing = subscriptionKey ? state.subscriptionAlbumFilters[subscriptionKey] : null;
  return {
    status: normalizeSubscriptionAlbumFilterStatus(existing?.status || "all"),
    query: String(existing?.query || ""),
  };
}


function setSubscriptionAlbumFilter(subscriptionId, patch) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (!subscriptionKey) {
    return getSubscriptionAlbumFilter("");
  }
  const current = getSubscriptionAlbumFilter(subscriptionKey);
  const next = {
    status: normalizeSubscriptionAlbumFilterStatus(patch?.status || current.status),
    query: patch?.query === undefined ? current.query : String(patch.query || ""),
  };
  if (next.status === "all" && !next.query.trim()) {
    delete state.subscriptionAlbumFilters[subscriptionKey];
  } else {
    state.subscriptionAlbumFilters[subscriptionKey] = next;
  }
  return next;
}


function clearSubscriptionAlbumFilter(subscriptionId) {
  const subscriptionKey = normalizeSelectionKey(subscriptionId);
  if (subscriptionKey) {
    delete state.subscriptionAlbumFilters[subscriptionKey];
  }
}


function clearAllSubscriptionAlbumFilters() {
  state.subscriptionAlbumFilters = {};
}


function getSelectedSubscriptionAlbumIds(subscriptionId) {
  return [...getSubscriptionAlbumSelection(subscriptionId)];
}


function getSelectableSubscriptionAlbumIds(subscription) {
  const albums = Array.isArray(subscription?.recentAlbums) ? subscription.recentAlbums : [];
  return albums
    .filter(isSelectableSubscriptionAlbum)
    .map((album) => normalizeSelectionKey(album.albumId))
    .filter(Boolean);
}


function pruneSubscriptionAlbumSelection(subscription, selectableAlbumIds = null) {
  const subscriptionId = getSubscriptionId(subscription);
  const selectableIds = new Set(Array.isArray(selectableAlbumIds) ? selectableAlbumIds.map(normalizeSelectionKey).filter(Boolean) : getSelectableSubscriptionAlbumIds(subscription));
  const selectedIds = getSelectedSubscriptionAlbumIds(subscriptionId).filter((albumId) => selectableIds.has(albumId));
  setSubscriptionAlbumSelection(subscriptionId, selectedIds);
  return selectedIds;
}


function selectAllSelectableSubscriptionAlbums(subscription) {
  const albumIds = getSelectableSubscriptionAlbumIds(subscription);
  setSubscriptionAlbumSelection(getSubscriptionId(subscription), albumIds);
  return albumIds;
}


function formatSubscriptionActionSummary(payload) {
  const action = String(payload.action || "");
  const updatedCount = Number(payload.updatedCount || 0);
  if (action === "download") {
    return formatSubscriptionScanSummary(getSingleSubscriptionScanPayload(payload));
  }
  const labels = {
    ignore: "已忽略",
    mark_imported: "已标记导入",
    mark_completed: "已确认完成",
    pending: "已恢复待确认",
  };
  return `${labels[action] || "已更新"} ${updatedCount} 个专辑`;
}


function getSubscriptionId(subscription) {
  return String(subscription?.id || "");
}


function getPreferredSubscriptionId(subscriptions) {
  const list = Array.isArray(subscriptions) ? subscriptions : [];
  const currentId = String(state.activeSubscriptionId || "");
  if (currentId && list.some((subscription) => getSubscriptionId(subscription) === currentId)) {
    return currentId;
  }
  return getSubscriptionId(list[0]);
}


function compareAlbumsByReleaseDateDesc(left, right) {
  const leftDate = String(left?.releaseDate || "");
  const rightDate = String(right?.releaseDate || "");
  if (leftDate && rightDate && leftDate !== rightDate) {
    return rightDate.localeCompare(leftDate);
  }
  if (leftDate && !rightDate) {
    return -1;
  }
  if (!leftDate && rightDate) {
    return 1;
  }
  const leftUpdated = String(left?.updatedAt || "");
  const rightUpdated = String(right?.updatedAt || "");
  if (leftUpdated !== rightUpdated) {
    return rightUpdated.localeCompare(leftUpdated);
  }
  return String(right?.albumId || "").localeCompare(String(left?.albumId || ""));
}


function normalizeSubscriptionAlbumFilterStatus(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["all", "pending", "active", "completed", "failed", "ignored", "imported"].includes(normalized) ? normalized : "all";
}


function albumMatchesSubscriptionStatusFilter(album, statusFilter) {
  const normalizedFilter = normalizeSubscriptionAlbumFilterStatus(statusFilter);
  if (normalizedFilter === "all") {
    return true;
  }
  const detectedStatus = getAlbumDetectedStatus(album);
  const rowStatus = normalizeAlbumStatus(album?.status);
  const userState = normalizeAlbumUserState(album?.userState);
  if (normalizedFilter === "pending") {
    return userState === "pending";
  }
  if (normalizedFilter === "active") {
    return ["queued", "running"].includes(detectedStatus) || ["queued", "running"].includes(rowStatus);
  }
  if (normalizedFilter === "completed") {
    return detectedStatus === "completed" || rowStatus === "completed";
  }
  if (normalizedFilter === "failed") {
    return ["failed_history", "stale_history"].includes(detectedStatus) || rowStatus === "failed";
  }
  if (normalizedFilter === "ignored") {
    return userState === "ignored";
  }
  if (normalizedFilter === "imported") {
    return userState === "imported";
  }
  return true;
}


function albumMatchesSubscriptionSearch(album, query) {
  const normalizedQuery = String(query || "").trim().toLowerCase();
  if (!normalizedQuery) {
    return true;
  }
  const searchable = [
    album?.albumName || "",
    formatAlbumTitleFromUrl(album?.albumUrl || ""),
    album?.albumId || "",
    album?.releaseDate || "",
    getAlbumStatusLabel(getAlbumDetectedStatus(album)),
    getAlbumUserStateLabel(album?.userState),
  ].join(" ").toLowerCase();
  return searchable.includes(normalizedQuery);
}


function filterSubscriptionAlbums(albums, filter) {
  const list = Array.isArray(albums) ? albums : [];
  const normalizedFilter = {
    status: normalizeSubscriptionAlbumFilterStatus(filter?.status || "all"),
    query: String(filter?.query || ""),
  };
  return list.filter((album) => (
    albumMatchesSubscriptionStatusFilter(album, normalizedFilter.status)
    && albumMatchesSubscriptionSearch(album, normalizedFilter.query)
  ));
}


function renderSubscriptionAlbums(subscription) {
  const albums = subscription.recentAlbums;
  if (!Array.isArray(albums) || albums.length === 0) {
    return '<p class="subscription-album-empty">暂无已记录专辑，扫描后显示。</p>';
  }
  const subscriptionId = getSubscriptionId(subscription);
  const sortedAlbums = [...albums].sort(compareAlbumsByReleaseDateDesc);
  const albumFilter = getSubscriptionAlbumFilter(subscriptionId);
  const filteredAlbums = filterSubscriptionAlbums(sortedAlbums, albumFilter);
  const allPendingAlbumIds = sortedAlbums.filter(isSelectableSubscriptionAlbum).map((album) => normalizeSelectionKey(album.albumId)).filter(Boolean);
  const pendingAlbums = filteredAlbums.filter(isSelectableSubscriptionAlbum);
  const visiblePendingAlbumIds = pendingAlbums.map((album) => normalizeSelectionKey(album.albumId)).filter(Boolean);
  const selectedAlbumIds = pruneSubscriptionAlbumSelection(subscription, allPendingAlbumIds);
  const visibleSelectedAlbumIds = selectedAlbumIds.filter((albumId) => visiblePendingAlbumIds.includes(albumId));
  const selectedAlbumIdSet = new Set(visibleSelectedAlbumIds);
  const bulkActionDisabled = visibleSelectedAlbumIds.length === 0 ? " disabled" : "";
  const filterActive = albumFilter.status !== "all" || albumFilter.query.trim();
  const titleText = filterActive ? `筛选专辑 · ${filteredAlbums.length}/${sortedAlbums.length}` : `全部已记录专辑 · ${sortedAlbums.length}`;
  const emptyText = filterActive ? '<p class="subscription-album-empty">没有匹配的专辑</p>' : "";
  const bulkActions = pendingAlbums.length > 0 ? `
    <div class="subscription-album-bulk" data-subscription-id="${escapeHtml(subscriptionId)}">
      <span class="subscription-album-selection-summary">待确认 ${pendingAlbums.length} · 已选 ${visibleSelectedAlbumIds.length}</span>
      <div class="subscription-album-selection-actions">
        <button type="button" class="secondary-button compact-button subscription-selection-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-selection-action="select_all">全选待确认</button>
        <button type="button" class="secondary-button compact-button subscription-selection-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-selection-action="clear"${bulkActionDisabled}>清空选择</button>
      </div>
      <div class="subscription-album-bulk-actions">
        <button type="button" class="secondary-button compact-button subscription-bulk-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-action="download"${bulkActionDisabled}>下载已选</button>
        <button type="button" class="secondary-button compact-button subscription-bulk-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-action="ignore"${bulkActionDisabled}>忽略已选</button>
        <button type="button" class="secondary-button compact-button subscription-bulk-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-action="mark_imported"${bulkActionDisabled}>标记已导入</button>
      </div>
    </div>
  ` : "";
  const items = filteredAlbums.map((album) => {
    const detectedStatus = getAlbumDetectedStatus(album);
    const userState = normalizeAlbumUserState(album.userState);
    const albumTitle = album.albumName || formatAlbumTitleFromUrl(album.albumUrl) || album.albumId || "未知专辑";
    const releaseDate = album.releaseDate ? `<span>${escapeHtml(album.releaseDate)}</span>` : "";
    const albumUrl = album.albumUrl || "";
    const albumId = normalizeSelectionKey(album.albumId);
    const selectable = userState === "pending" && canDownloadAlbum(album);
    const checked = selectable && selectedAlbumIdSet.has(albumId);
    const downloadable = canDownloadAlbum(album);
    const actions = [];
    if (downloadable) {
      actions.push(`<button type="button" class="secondary-button compact-button subscription-album-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" data-action="download">下载</button>`);
      actions.push(`<button type="button" class="secondary-button compact-button subscription-album-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" data-action="mark_completed">确认完成</button>`);
      if (album.canIgnore !== false && userState !== "ignored") {
        actions.push(`<button type="button" class="secondary-button compact-button subscription-album-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" data-action="ignore">忽略</button>`);
      }
      if (album.canMarkImported !== false && userState !== "imported") {
        actions.push(`<button type="button" class="secondary-button compact-button subscription-album-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" data-action="mark_imported">已导入</button>`);
      }
    } else if (!["completed", "queued", "running"].includes(detectedStatus) && !["ignored", "imported"].includes(userState)) {
      actions.push(`<button type="button" class="secondary-button compact-button subscription-album-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" data-action="mark_completed">确认完成</button>`);
    }
    if (canRestoreAlbum(album)) {
      actions.push(`<button type="button" class="secondary-button compact-button subscription-album-action-btn" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" data-action="pending">恢复待确认</button>`);
    }
    return `
      <li class="subscription-album-item">
        <label class="subscription-album-check">
          <input type="checkbox" class="subscription-album-select" data-subscription-id="${escapeHtml(subscriptionId)}" data-album-id="${escapeHtml(albumId)}" aria-label="选择 ${escapeHtml(albumTitle)}" ${selectable ? "" : "disabled"}${checked ? " checked" : ""}>
        </label>
        ${renderArtworkImage(album.artworkUrl, albumTitle, "subscription-album-artwork")}
        <div class="subscription-album-main">
          ${albumUrl ? `<a href="${escapeHtml(albumUrl)}" target="_blank" rel="noreferrer">${escapeHtml(albumTitle)}</a>` : `<strong>${escapeHtml(albumTitle)}</strong>`}
          <p>${releaseDate}<span>ID ${escapeHtml(album.albumId || "-")}</span></p>
        </div>
        <div class="subscription-album-state">
          <span class="badge album-status album-status-${detectedStatus}">${escapeHtml(getAlbumStatusLabel(detectedStatus))}</span>
          <span class="badge album-user-state album-user-state-${userState}">${escapeHtml(getAlbumUserStateLabel(userState))}</span>
        </div>
        <div class="subscription-album-actions">${actions.join("")}</div>
      </li>
    `;
  }).join("");
  return `
    <div class="subscription-albums">
      <div class="subscription-albums-header">
        <div class="subscription-albums-title">${escapeHtml(titleText)}</div>
        <label class="subscription-album-search">
          <span>搜索专辑</span>
          <input class="subscription-album-search-input" type="search" data-subscription-id="${escapeHtml(subscriptionId)}" value="${escapeHtml(albumFilter.query)}" placeholder="名称、ID、日期">
        </label>
      </div>
      ${bulkActions}
      ${emptyText}
      ${filteredAlbums.length > 0 ? `<ul>${items}</ul>` : ""}
    </div>
  `;
}


function getSelectableAlbumIdsFromDetailPanel(detailPanel) {
  if (!detailPanel) {
    return [];
  }
  return [...detailPanel.querySelectorAll(".subscription-album-select:not(:disabled)")]
    .map((input) => normalizeSelectionKey(input.dataset.albumId))
    .filter(Boolean);
}


function syncSubscriptionAlbumCheckboxes(detailPanel, subscriptionId) {
  if (!detailPanel) {
    return;
  }
  const selectedAlbumIds = new Set(getSelectedSubscriptionAlbumIds(subscriptionId));
  for (const input of detailPanel.querySelectorAll(".subscription-album-select")) {
    input.checked = !input.disabled && selectedAlbumIds.has(normalizeSelectionKey(input.dataset.albumId));
  }
}


function updateSubscriptionBulkControls(detailPanel, subscriptionId) {
  if (!detailPanel) {
    return;
  }
  const selectableCount = getSelectableAlbumIdsFromDetailPanel(detailPanel).length;
  const selectedCount = getSelectedSubscriptionAlbumIds(subscriptionId).length;
  const summary = detailPanel.querySelector(".subscription-album-selection-summary");
  if (summary) {
    summary.textContent = `待确认 ${selectableCount} · 已选 ${selectedCount}`;
  }
  for (const btn of detailPanel.querySelectorAll(".subscription-bulk-action-btn")) {
    btn.disabled = selectedCount === 0;
  }
  const clearButton = detailPanel.querySelector(".subscription-selection-action-btn[data-selection-action='clear']");
  if (clearButton) {
    clearButton.disabled = selectedCount === 0;
  }
  const selectAllButton = detailPanel.querySelector(".subscription-selection-action-btn[data-selection-action='select_all']");
  if (selectAllButton) {
    selectAllButton.disabled = selectableCount === 0;
  }
}


function renderSubscriptionList(subscriptions) {
  const container = document.getElementById("subscription-list");
  const list = Array.isArray(subscriptions) ? subscriptions : [];
  if (!container) {
    return;
  }
  saveSubscriptionAlbumScrollPosition(container);
  if (list.length === 0) {
    state.activeSubscriptionId = "";
    clearAllSubscriptionAlbumSelections();
    clearAllSubscriptionAlbumScrollPositions();
    clearAllSubscriptionAlbumFilters();
    container.innerHTML = '<p class="empty-text">暂无订阅</p>';
    return;
  }

  const activeSubscriptionId = getPreferredSubscriptionId(list);
  state.activeSubscriptionId = activeSubscriptionId;
  const activeSubscription = list.find((subscription) => getSubscriptionId(subscription) === activeSubscriptionId) || list[0];
  const activeAlbumFilter = getSubscriptionAlbumFilter(activeSubscriptionId);

  const cards = list.map((subscription) => {
    const subscriptionId = getSubscriptionId(subscription);
    const isActive = subscriptionId === activeSubscriptionId;
    const statusLabel = subscription.enabled ? "已启用" : "已停用";
    return `
      <button type="button" class="subscription-card ${isActive ? "selected" : ""}" data-subscription-id="${escapeHtml(subscriptionId)}" aria-pressed="${isActive ? "true" : "false"}">
        <div class="subscription-card-artwork-wrap">
          ${renderArtworkImage(subscription.artistArtworkUrl, subscription.artistName || "未知歌手", "subscription-card-artwork")}
          <span class="badge subscription-status subscription-card-status">${statusLabel}</span>
        </div>
        <div class="subscription-card-copy">
          <strong>${escapeHtml(subscription.artistName || "未知歌手")}</strong>
          <p>${escapeHtml((subscription.storefront || "").toUpperCase())} · ${escapeHtml(subscription.artistId || "")}</p>
        </div>
        <div class="subscription-card-stats">
          <span>专辑 ${Number(subscription.albumCount || 0)}</span>
          <span>待确认 ${Number(subscription.pendingAlbumCount || 0)}</span>
          <span>进行中 ${Number(subscription.activeAlbumCount || 0)}</span>
        </div>
      </button>
    `;
  }).join("");

  const detailStats = [
    { filter: "all", label: "专辑", value: activeSubscription.albumCount || 0 },
    { filter: "pending", label: "待确认", value: activeSubscription.pendingAlbumCount || 0 },
    { filter: "active", label: "进行中", value: activeSubscription.activeAlbumCount || 0 },
    { filter: "completed", label: "已完成", value: activeSubscription.completedAlbumCount || 0 },
    { filter: "failed", label: "失败", value: activeSubscription.failedAlbumCount || 0 },
    { filter: "ignored", label: "忽略", value: activeSubscription.ignoredAlbumCount || 0 },
    { filter: "imported", label: "已导入", value: activeSubscription.importedAlbumCount || 0 },
  ];

  const detailPanel = `
    <section class="subscription-detail-panel" data-subscription-id="${escapeHtml(activeSubscriptionId)}">
      <div class="subscription-detail-top">
        <div class="subscription-detail-artist">
          ${renderArtworkImage(activeSubscription.artistArtworkUrl, activeSubscription.artistName || "未知歌手", "subscription-detail-artwork")}
          <div class="subscription-detail-copy">
            <p class="subscription-detail-eyebrow">当前订阅</p>
            <strong>${escapeHtml(activeSubscription.artistName || "未知歌手")}</strong>
            <p>${escapeHtml((activeSubscription.storefront || "").toUpperCase())} · ${escapeHtml(activeSubscription.artistId || "")}</p>
          </div>
        </div>
        <div class="subscription-detail-controls">
          <span class="badge subscription-status">${activeSubscription.enabled ? "已启用" : "已停用"}</span>
          <select class="subscription-policy-select" data-subscription-id="${escapeHtml(activeSubscription.id)}" aria-label="新专辑策略">
            <option value="confirm" ${activeSubscription.newAlbumPolicy === "confirm" ? "selected" : ""}>待确认</option>
            <option value="auto" ${activeSubscription.newAlbumPolicy === "auto" ? "selected" : ""}>自动下载</option>
          </select>
          <button type="button" class="secondary-button compact-button subscription-scan-btn" data-subscription-id="${escapeHtml(activeSubscription.id)}">扫描</button>
          <button type="button" class="secondary-button compact-button subscription-delete-btn" data-subscription-id="${escapeHtml(activeSubscription.id)}">删除</button>
        </div>
      </div>
      <div class="subscription-stats subscription-detail-stats">
        ${detailStats.map(({ filter, label, value }) => `
          <button type="button" class="subscription-stat-filter ${activeAlbumFilter.status === filter ? "selected" : ""}" data-subscription-id="${escapeHtml(activeSubscriptionId)}" data-album-filter="${escapeHtml(filter)}" aria-pressed="${activeAlbumFilter.status === filter ? "true" : "false"}">
            ${escapeHtml(label)} ${Number(value || 0)}
          </button>
        `).join("")}
      </div>
      <p class="history-updated">上次扫描：${escapeHtml(activeSubscription.lastCheckedAt || "未扫描")}</p>
      ${activeSubscription.lastError ? `<p class="subscription-error-line">${escapeHtml(activeSubscription.lastError)}</p>` : ""}
      ${renderSubscriptionAlbums(activeSubscription)}
    </section>
  `;

  container.innerHTML = `
    <div class="subscription-card-grid" role="list" aria-label="订阅歌手列表">
      ${cards}
    </div>
    ${detailPanel}
  `;
  attachArtworkFallbackHandlers(container);
  restoreSubscriptionAlbumScrollPosition(container, activeSubscriptionId);

  const renderWithAlbumFilter = (subscriptionId, patch, focusSearch = false, cursorPosition = null) => {
    const normalizedSubscriptionId = normalizeSelectionKey(subscriptionId);
    const albumList = getSubscriptionAlbumScrollElement(container, normalizedSubscriptionId);
    if (albumList) {
      albumList.scrollTop = 0;
    }
    clearSubscriptionAlbumScrollPosition(normalizedSubscriptionId);
    setSubscriptionAlbumFilter(normalizedSubscriptionId, patch);
    renderSubscriptionList(list);
    if (focusSearch) {
      const nextInput = container.querySelector(".subscription-album-search-input");
      if (nextInput && normalizeSelectionKey(nextInput.dataset.subscriptionId) === normalizedSubscriptionId) {
        nextInput.focus();
        if (typeof nextInput.setSelectionRange === "function" && Number.isInteger(cursorPosition)) {
          nextInput.setSelectionRange(cursorPosition, cursorPosition);
        }
      }
    }
  };

  for (const card of container.querySelectorAll(".subscription-card")) {
    card.addEventListener("click", () => {
      const nextId = card.dataset.subscriptionId || "";
      if (!nextId || nextId === state.activeSubscriptionId) {
        return;
      }
      state.activeSubscriptionId = nextId;
      renderSubscriptionList(list);
    });
  }

  for (const btn of container.querySelectorAll(".subscription-stat-filter")) {
    btn.addEventListener("click", () => {
      renderWithAlbumFilter(btn.dataset.subscriptionId || "", { status: btn.dataset.albumFilter || "all" });
    });
  }

  for (const input of container.querySelectorAll(".subscription-album-search-input")) {
    input.addEventListener("input", () => {
      const cursorPosition = typeof input.selectionStart === "number" ? input.selectionStart : input.value.length;
      renderWithAlbumFilter(input.dataset.subscriptionId || "", { query: input.value || "" }, true, cursorPosition);
    });
  }

  for (const select of container.querySelectorAll(".subscription-policy-select")) {
    select.addEventListener("change", () => {
      handleUpdateSubscriptionPolicy(select.dataset.subscriptionId || "", select.value).catch((error) => {
        setSubscriptionError(error.message || "策略更新失败");
      });
    });
  }

  for (const btn of container.querySelectorAll(".subscription-album-action-btn")) {
    btn.addEventListener("click", () => {
      handleSubscriptionAlbumAction(
        btn.dataset.subscriptionId || "",
        [btn.dataset.albumId || ""],
        btn.dataset.action || "",
      ).catch((error) => {
        setSubscriptionError(error.message || "专辑操作失败");
      });
    });
  }

  for (const input of container.querySelectorAll(".subscription-album-select")) {
    input.addEventListener("change", () => {
      const subscriptionId = input.dataset.subscriptionId || "";
      setSubscriptionAlbumSelected(subscriptionId, input.dataset.albumId || "", input.checked);
      updateSubscriptionBulkControls(input.closest(".subscription-detail-panel"), subscriptionId);
    });
  }

  for (const btn of container.querySelectorAll(".subscription-selection-action-btn")) {
    btn.addEventListener("click", () => {
      const subscriptionId = btn.dataset.subscriptionId || "";
      const detailPanel = btn.closest(".subscription-detail-panel");
      const selectionAction = btn.dataset.selectionAction || "";
      if (selectionAction === "select_all") {
        const albumIds = getSelectableAlbumIdsFromDetailPanel(detailPanel);
        setSubscriptionAlbumSelection(subscriptionId, albumIds);
      } else if (selectionAction === "clear") {
        clearSubscriptionAlbumSelection(subscriptionId);
      }
      syncSubscriptionAlbumCheckboxes(detailPanel, subscriptionId);
      updateSubscriptionBulkControls(detailPanel, subscriptionId);
    });
  }

  for (const btn of container.querySelectorAll(".subscription-bulk-action-btn")) {
    btn.addEventListener("click", () => {
      const subscriptionId = btn.dataset.subscriptionId || "";
      const detailPanel = btn.closest(".subscription-detail-panel");
      const selectableAlbumIds = new Set(getSelectableAlbumIdsFromDetailPanel(detailPanel));
      const selectedAlbumIds = getSelectedSubscriptionAlbumIds(subscriptionId).filter((albumId) => selectableAlbumIds.has(albumId));
      setSubscriptionAlbumSelection(subscriptionId, selectedAlbumIds);
      syncSubscriptionAlbumCheckboxes(detailPanel, subscriptionId);
      updateSubscriptionBulkControls(detailPanel, subscriptionId);
      if (selectedAlbumIds.length === 0) {
        setSubscriptionError("请先选择待确认专辑");
        return;
      }
      handleSubscriptionAlbumAction(subscriptionId, selectedAlbumIds, btn.dataset.action || "").catch((error) => {
        setSubscriptionError(error.message || "批量操作失败");
      });
    });
  }

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
  if (state.demoMode) {
    renderSubscriptionList(cloneDemo(demoStore.subscriptions));
    return;
  }
  const subscriptions = await fetchSubscriptions();
  if (Array.isArray(subscriptions) && subscriptions.length > 0) {
    const preferredSubscriptionId = getPreferredSubscriptionId(subscriptions);
    state.activeSubscriptionId = preferredSubscriptionId || state.activeSubscriptionId;
  }
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
  if (responsePayload?.subscription?.id !== undefined && responsePayload?.subscription?.id !== null) {
    state.activeSubscriptionId = String(responsePayload.subscription.id);
  }
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
  setSubscriptionFormExpanded(false);
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


async function handleUpdateSubscriptionPolicy(subscriptionId, newAlbumPolicy) {
  setSubscriptionError("");
  setSubscriptionNote("");
  await updateSubscriptionPolicy(subscriptionId, newAlbumPolicy);
  setSubscriptionNote(newAlbumPolicy === "auto" ? "新专辑策略已切换为自动下载" : "新专辑策略已切换为待确认");
  await refreshSubscriptionList();
}


async function handleSubscriptionAlbumAction(subscriptionId, albumIds, action) {
  setSubscriptionError("");
  setSubscriptionNote("");
  const payload = await applySubscriptionAlbumAction(subscriptionId, albumIds, action);
  clearSubscriptionAlbumSelectionIds(subscriptionId, Array.isArray(payload.updatedAlbumIds) ? payload.updatedAlbumIds : albumIds);
  setSubscriptionNote(formatSubscriptionActionSummary(payload));
  await refreshSubscriptionList();
  await refreshTaskList();
  await refreshHistoryList();
}


async function handleDeleteSubscription(subscriptionId) {
  setSubscriptionError("");
  await deleteSubscription(subscriptionId);
  clearSubscriptionAlbumSelection(subscriptionId);
  clearSubscriptionAlbumScrollPosition(subscriptionId);
  clearSubscriptionAlbumFilter(subscriptionId);
  setSubscriptionNote("已删除订阅");
  await refreshSubscriptionList();
}


async function handleCancelTask(taskId) {
  document.getElementById("form-error").textContent = "";
  setSubmissionNote("");
  const payload = await cancelTask(taskId);
  setSubmissionNote("已取消排队任务");
  if (payload.task && payload.task.taskId === state.selectedTaskId) {
    applySnapshot(payload.task);
  }
  await refreshTaskList();
  await refreshHistoryList();
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

  if (state.demoMode) {
    submitDemoTask(url, force);
    return;
  }

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
  state.demoMode = isDemoModeEnabled();
  if (state.demoMode) {
    document.body.classList.add("demo-mode");
  }
  bindViewNavigation();
  bindSidebarToggle();
  showView(getInitialViewName(), false);

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

  document.getElementById("toggle-subscription-form-button").addEventListener("click", () => {
    setSubscriptionFormExpanded(true);
  });
  bindSubscriptionFormModal();

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
  setSubscriptionFormExpanded(false);
  refreshTaskList().catch(() => {});
  refreshHistoryList().catch(() => {});
  refreshSubscriptionList().catch(() => {});
  startTaskPolling();
  startHistoryPolling();
  startSubscriptionPolling();
  if (state.demoMode) {
    startDemoSimulation();
  }
}

if (typeof module !== "undefined") {
  module.exports = {
    bindViewNavigation,
    bindSidebarToggle,
    albumMatchesSubscriptionSearch,
    albumMatchesSubscriptionStatusFilter,
    clearAllSubscriptionAlbumSelections,
    clearAllSubscriptionAlbumFilters,
    clearAllSubscriptionAlbumScrollPositions,
    clearSubscriptionAlbumFilter,
    clearSubscriptionAlbumSelection,
    compareAlbumsByReleaseDateDesc,
    cancelTask,
    filterSubscriptionAlbums,
    formatSubscriptionScanSummary,
    formatSubscriptionActionSummary,
    formatHistoryRetrySummary,
    formatAlbumTitleFromUrl,
    formatRetryFailedSummary,
    getSafeImageUrl,
    getCachedArtworkUrl,
    getAlbumDetectedStatus,
    getAlbumStatusLabel,
    getAlbumUserStateLabel,
    getSubscriptionAlbumFilter,
    getTaskAlbumName,
    getInitialViewName,
    getSingleSubscriptionScanPayload,
    getSelectedSubscriptionAlbumIds,
    getTaskSummaryCounts,
    isTerminalTaskStatus,
    mergeLogLines,
    normalizeHistoryStatus,
    renderArtworkImage,
    renderArtistSearchResults,
    renderSubscriptionAlbums,
    renderSubscriptionList,
    renderHistoryList,
    renderResult,
    renderTaskList,
    retryFailedTasks,
    retryHistoryFailedTasks,
    retrySingleHistory,
    restoreSubscriptionAlbumScrollPosition,
    saveSubscriptionAlbumScrollPosition,
    selectAllSelectableSubscriptionAlbums,
    setSidebarCollapsed,
    setSubscriptionAlbumFilter,
    setSubscriptionAlbumSelected,
    setSubscriptionAlbumSelection,
    showView,
    shouldOpenNewStream,
  };
}
