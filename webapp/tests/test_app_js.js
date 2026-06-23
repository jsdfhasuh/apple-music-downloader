const test = require("node:test")
const assert = require("node:assert/strict")

const {
  bindViewNavigation,
  bindSidebarToggle,
  clearAllSubscriptionAlbumSelections,
  clearAllSubscriptionAlbumFilters,
  clearAllSubscriptionAlbumScrollPositions,
  compareAlbumsByReleaseDateDesc,
  cancelTask,
  filterSubscriptionAlbums,
  formatAlbumTitleFromUrl,
  formatSubscriptionActionSummary,
  formatSubscriptionScanSummary,
  formatHistoryRetrySummary,
  formatRetryFailedSummary,
  getCachedArtworkUrl,
  getSafeImageUrl,
  getSelectedSubscriptionAlbumIds,
  getTaskAlbumName,
  getInitialViewName,
  getSingleSubscriptionScanPayload,
  getTaskSummaryCounts,
  isTerminalTaskStatus,
  mergeLogLines,
  normalizeHistoryStatus,
  renderArtworkImage,
  renderArtistSearchResults,
  renderHistoryList,
  renderResult,
  renderSubscriptionAlbums,
  renderSubscriptionList,
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
  showView,
  shouldOpenNewStream,
} = require("../static/app.js")

function createFakePanel(view) {
  return {
    dataset: { view },
    hidden: false,
  }
}

function createFakeNavLink(viewTarget) {
  const listeners = {}
  return {
    dataset: { viewTarget },
    listeners,
    classList: {
      classes: new Set(),
      toggle(className, active) {
        if (active) {
          this.classes.add(className)
        } else {
          this.classes.delete(className)
        }
      },
    },
    addEventListener(eventName, handler) {
      listeners[eventName] = handler
    },
  }
}

function withFakeNavigationDom(callback) {
  const originalDocument = global.document
  const originalWindow = global.window
  const panels = ["console", "queue", "subscriptions", "history"].map(createFakePanel)
  const links = ["console", "queue", "subscriptions", "history"].map(createFakeNavLink)
  const hashListeners = {}
  const replaceStateCalls = []

  global.document = {
    querySelectorAll(selector) {
      if (selector === ".view-panel") {
        return panels
      }
      if (selector === ".nav-link[data-view-target]") {
        return links
      }
      return []
    },
  }
  global.window = {
    location: { hash: "#console" },
    history: {
      replaceState(_state, _title, hash) {
        replaceStateCalls.push(hash)
        global.window.location.hash = hash
      },
    },
    addEventListener(eventName, handler) {
      hashListeners[eventName] = handler
    },
  }

  try {
    return callback({ panels, links, hashListeners, replaceStateCalls })
  } finally {
    global.document = originalDocument
    global.window = originalWindow
  }
}

function createFakeClassList() {
  const classes = new Set()
  return {
    classes,
    contains(className) {
      return classes.has(className)
    },
    toggle(className, active) {
      if (active) {
        classes.add(className)
      } else {
        classes.delete(className)
      }
    },
  }
}

function withFakeSidebarDom(callback) {
  const originalDocument = global.document
  const originalWindow = global.window
  const storage = { "amd-sidebar-collapsed": "1" }
  const toggleListeners = {}
  const toggleAttributes = {}
  const toggle = {
    listeners: toggleListeners,
    attributes: toggleAttributes,
    addEventListener(eventName, handler) {
      toggleListeners[eventName] = handler
    },
    setAttribute(name, value) {
      toggleAttributes[name] = value
    },
  }

  global.document = {
    body: {
      classList: createFakeClassList(),
    },
    getElementById(id) {
      return id === "sidebar-toggle" ? toggle : null
    },
  }
  global.window = {
    localStorage: {
      getItem(key) {
        return storage[key] || null
      },
      setItem(key, value) {
        storage[key] = value
      },
    },
  }

  try {
    return callback({ storage, toggle })
  } finally {
    global.document = originalDocument
    global.window = originalWindow
  }
}

function withFakeElement(id, callback) {
  const originalDocument = global.document
  const element = {
    innerHTML: "",
    querySelectorAll() {
      return []
    },
  }

  global.document = {
    getElementById(candidate) {
      return candidate === id ? element : null
    },
  }

  try {
    return callback(element)
  } finally {
    global.document = originalDocument
  }
}

function createFakeSubscriptionScrollRoot(subscriptionId, scrollTop) {
  const albumList = { scrollTop }
  const detailPanel = {
    dataset: { subscriptionId: String(subscriptionId) },
    querySelector(selector) {
      return selector === ".subscription-albums ul" ? albumList : null
    },
  }
  const root = {
    albumList,
    detailPanel,
    querySelector(selector) {
      return selector === ".subscription-detail-panel" ? detailPanel : null
    },
    querySelectorAll(selector) {
      return selector === ".subscription-detail-panel" ? [detailPanel] : []
    },
  }
  return root
}

test("isTerminalTaskStatus returns true for completed and failed", () => {
  assert.equal(isTerminalTaskStatus("completed"), true)
  assert.equal(isTerminalTaskStatus("failed"), true)
  assert.equal(isTerminalTaskStatus("cancelled"), true)
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
    { status: "cancelled" },
  ])

  assert.deepEqual(result, {
    activeCount: 1,
    queueCount: 1,
    successCount: 1,
    failureCount: 1,
  })
})

test("formatAlbumTitleFromUrl humanizes Apple Music album slug", () => {
  assert.equal(
    formatAlbumTitleFromUrl("https://music.apple.com/cn/album/hit-me-hard-and-soft/1739659134?l=en"),
    "Hit Me Hard And Soft"
  )
})

test("getTaskAlbumName prefers explicit album name before URL fallback", () => {
  assert.equal(
    getTaskAlbumName({
      albumName: "Explicit Album",
      url: "https://music.apple.com/cn/album/url-album/1",
    }),
    "Explicit Album"
  )
  assert.equal(
    getTaskAlbumName({
      result: [{ album: "Result Album" }],
      url: "https://music.apple.com/cn/album/url-album/1",
    }),
    "Result Album"
  )
  assert.equal(
    getTaskAlbumName({
      url: "https://music.apple.com/cn/album/url-album/1",
    }),
    "Url Album"
  )
})

test("renderResult escapes downloader metadata", () => {
  withFakeElement("result-list", (element) => {
    renderResult([
      {
        song: '<img src=x onerror="alert(1)">',
        artist: "<b>Artist</b>",
        album: "Album & Friends",
        path: 'C:\\tmp\\" onclick="alert(1)',
      },
    ])

    assert.match(element.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/)
    assert.match(element.innerHTML, /&lt;b&gt;Artist&lt;\/b&gt;/)
    assert.match(element.innerHTML, /Album &amp; Friends/)
    assert.doesNotMatch(element.innerHTML, /<img/)
    assert.doesNotMatch(element.innerHTML, /onclick="alert/)
  })
})

test("renderHistoryList escapes status and URL fields", () => {
  withFakeElement("history-list", (element) => {
    renderHistoryList([{
      status: 'failed" onclick="alert(1)',
      url: 'https://music.apple.com/cn/album/x/1" onclick="alert(1)',
      updated_at: "<script>alert(1)</script>",
    }])

    assert.equal(normalizeHistoryStatus('failed" onclick="alert(1)'), "unknown")
    assert.match(element.innerHTML, /failed&quot; onclick=&quot;alert\(1\)/)
    assert.match(element.innerHTML, /https:\/\/music\.apple\.com\/cn\/album\/x\/1&quot; onclick=&quot;alert\(1\)/)
    assert.match(element.innerHTML, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/)
    assert.doesNotMatch(element.innerHTML, /history-status failed"/)
    assert.doesNotMatch(element.innerHTML, /data-url="https:\/\/music\.apple\.com\/cn\/album\/x\/1" onclick=/)
  })
})

test("normalizeHistoryStatus recognizes cancelled records", () => {
  assert.equal(normalizeHistoryStatus("cancelled"), "cancelled")
})

test("renderTaskList shows cancel action only for queued tasks", () => {
  withFakeElement("task-list", (element) => {
    renderTaskList([
      {
        taskId: "queued-task",
        status: "queued",
        stage: "queued",
        source: "subscription",
        url: "https://music.apple.com/cn/album/queued-album/123",
      },
      {
        taskId: "running-task",
        status: "running",
        stage: "downloading",
        source: "web",
        url: "https://music.apple.com/cn/album/running-album/456",
      },
    ])

    assert.match(element.innerHTML, /task-cancel-btn/)
    assert.match(element.innerHTML, /data-task-id="queued-task"/)
    assert.doesNotMatch(element.innerHTML, /<button type="button" class="task-item/)
  })
})

test("compareAlbumsByReleaseDateDesc sorts newest release first and missing dates last", () => {
  const albums = [
    { albumId: "missing", releaseDate: "", updatedAt: "2026-06-01" },
    { albumId: "old", releaseDate: "2020-07-24", updatedAt: "2026-06-03" },
    { albumId: "new", releaseDate: "2026-06-05", updatedAt: "2026-06-02" },
    { albumId: "middle", releaseDate: "2026-05-18", updatedAt: "2026-06-04" },
  ]

  albums.sort(compareAlbumsByReleaseDateDesc)

  assert.deepEqual(albums.map((album) => album.albumId), ["new", "middle", "old", "missing"])
})

test("getSafeImageUrl only allows http image URLs", () => {
  assert.equal(
    getSafeImageUrl("https://is1-ssl.mzstatic.com/image/thumb/example/512x512bb.jpg"),
    "https://is1-ssl.mzstatic.com/image/thumb/example/512x512bb.jpg"
  )
  assert.equal(getSafeImageUrl("javascript:alert(1)"), "")
  assert.equal(getSafeImageUrl("https://[bad"), "")
})

test("getCachedArtworkUrl builds local artwork cache URLs", () => {
  assert.equal(
    getCachedArtworkUrl("https://is1-ssl.mzstatic.com/image/thumb/example/512x512bb.jpg"),
    "/api/artwork?url=https%3A%2F%2Fis1-ssl.mzstatic.com%2Fimage%2Fthumb%2Fexample%2F512x512bb.jpg"
  )
  assert.equal(getCachedArtworkUrl("javascript:alert(1)"), "")
})

test("renderArtworkImage renders safe images and placeholders", () => {
  const safe = renderArtworkImage(
    "https://is1-ssl.mzstatic.com/image/thumb/example/512x512bb.jpg",
    "Album & Artist",
    "subscription-album-artwork"
  )
  const unsafe = renderArtworkImage("javascript:alert(1)", "Bad", "subscription-album-artwork")

  assert.match(safe, /<img class="subscription-album-artwork"/)
  assert.match(safe, /src="\/api\/artwork\?url=https%3A%2F%2Fis1-ssl\.mzstatic\.com%2Fimage%2Fthumb%2Fexample%2F512x512bb\.jpg"/)
  assert.match(safe, /alt="Album &amp; Artist"/)
  assert.match(unsafe, /artwork-placeholder/)
  assert.doesNotMatch(unsafe, /src=/)
})

test("filterSubscriptionAlbums filters by status and search text", () => {
  const albums = [{
    albumId: "111",
    albumName: "Midnight Rain",
    releaseDate: "2026-06-01",
    userState: "pending",
    detectedStatus: "missing",
  }, {
    albumId: "222",
    albumName: "Live Session",
    releaseDate: "2026-05-01",
    userState: "subscribed",
    detectedStatus: "running",
  }, {
    albumId: "333",
    albumName: "Done Album",
    releaseDate: "2026-04-01",
    userState: "subscribed",
    detectedStatus: "completed",
  }, {
    albumId: "444",
    albumName: "Broken Import",
    releaseDate: "2026-03-01",
    userState: "imported",
    detectedStatus: "failed_history",
  }]

  assert.deepEqual(filterSubscriptionAlbums(albums, { status: "pending" }).map((album) => album.albumId), ["111"])
  assert.deepEqual(filterSubscriptionAlbums(albums, { status: "active" }).map((album) => album.albumId), ["222"])
  assert.deepEqual(filterSubscriptionAlbums(albums, { status: "completed" }).map((album) => album.albumId), ["333"])
  assert.deepEqual(filterSubscriptionAlbums(albums, { status: "failed" }).map((album) => album.albumId), ["444"])
  assert.deepEqual(filterSubscriptionAlbums(albums, { status: "all", query: "midnight" }).map((album) => album.albumId), ["111"])
  assert.deepEqual(filterSubscriptionAlbums(albums, { status: "imported", query: "broken" }).map((album) => album.albumId), ["444"])
})

test("renderSubscriptionAlbums includes album search and applies saved filters", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  setSubscriptionAlbumFilter(7, { query: "second" })

  const html = renderSubscriptionAlbums({
    id: 7,
    recentAlbums: [{
      albumId: "111",
      albumName: "First Album",
      albumUrl: "https://music.apple.com/cn/album/first/111",
      releaseDate: "2026-06-02",
      userState: "pending",
      detectedStatus: "missing",
    }, {
      albumId: "222",
      albumName: "Second Album",
      albumUrl: "https://music.apple.com/cn/album/second/222",
      releaseDate: "2026-06-01",
      userState: "pending",
      detectedStatus: "missing",
    }],
  })

  assert.match(html, /class="subscription-album-search-input"/)
  assert.match(html, /value="second"/)
  assert.match(html, /筛选专辑 · 1\/2/)
  assert.match(html, /Second Album/)
  assert.doesNotMatch(html, /First Album/)

  clearAllSubscriptionAlbumFilters()
})

test("renderSubscriptionAlbums includes album artwork", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  const html = renderSubscriptionAlbums({
    id: 7,
    recentAlbums: [{
      albumId: "111",
      albumName: "New Album",
      albumUrl: "https://music.apple.com/cn/album/new-album/111",
      artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/album-111/512x512bb.jpg",
      releaseDate: "2026-06-01",
      userState: "pending",
      detectedStatus: "missing",
      canDownload: true,
    }],
  })

  assert.match(html, /class="subscription-album-artwork"/)
  assert.match(html, /album-111%2F512x512bb\.jpg/)
  assert.match(html, /New Album/)
})

test("renderSubscriptionAlbums shows bulk selection controls and selected count", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  setSubscriptionAlbumSelected(7, "111", true)

  const html = renderSubscriptionAlbums({
    id: 7,
    recentAlbums: [{
      albumId: "111",
      albumName: "First Pending",
      albumUrl: "https://music.apple.com/cn/album/first/111",
      releaseDate: "2026-06-02",
      userState: "pending",
      detectedStatus: "missing",
    }, {
      albumId: "222",
      albumName: "Second Pending",
      albumUrl: "https://music.apple.com/cn/album/second/222",
      releaseDate: "2026-06-01",
      userState: "pending",
      detectedStatus: "failed_history",
    }, {
      albumId: "333",
      albumName: "Done",
      albumUrl: "https://music.apple.com/cn/album/done/333",
      releaseDate: "2026-05-01",
      userState: "subscribed",
      detectedStatus: "completed",
    }],
  })

  assert.match(html, /待确认 2 · 已选 1/)
  assert.match(html, /全选待确认/)
  assert.match(html, /清空选择/)
  assert.match(html, /下载已选/)
  assert.match(html, /忽略已选/)
  assert.match(html, /标记已导入/)
})

test("renderSubscriptionAlbums restores checked album selections after rerender", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  setSubscriptionAlbumSelected(7, "111", true)
  const subscription = {
    id: 7,
    recentAlbums: [{
      albumId: "111",
      albumName: "Selected Album",
      albumUrl: "https://music.apple.com/cn/album/selected/111",
      releaseDate: "2026-06-01",
      userState: "pending",
      detectedStatus: "missing",
    }],
  }

  const firstRender = renderSubscriptionAlbums(subscription)
  const secondRender = renderSubscriptionAlbums(subscription)

  assert.match(firstRender, /<input[^>]+data-album-id="111"[^>]+checked/)
  assert.match(secondRender, /<input[^>]+data-album-id="111"[^>]+checked/)
  assert.match(secondRender, /待确认 1 · 已选 1/)
})

test("selectAllSelectableSubscriptionAlbums only selects downloadable pending albums", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  const subscription = {
    id: 7,
    recentAlbums: [{
      albumId: "111",
      userState: "pending",
      detectedStatus: "missing",
    }, {
      albumId: "222",
      userState: "pending",
      detectedStatus: "failed_history",
    }, {
      albumId: "333",
      userState: "pending",
      detectedStatus: "completed",
    }, {
      albumId: "444",
      userState: "subscribed",
      detectedStatus: "missing",
    }, {
      albumId: "555",
      userState: "ignored",
      detectedStatus: "missing",
      canDownload: true,
    }, {
      albumId: "666",
      userState: "imported",
      detectedStatus: "failed_history",
      canDownload: true,
    }, {
      albumId: "777",
      userState: "pending",
      detectedStatus: "running",
    }, {
      albumId: "888",
      userState: "pending",
      detectedStatus: "seen",
    }],
  }

  assert.deepEqual(selectAllSelectableSubscriptionAlbums(subscription), ["111", "222"])
  assert.deepEqual(getSelectedSubscriptionAlbumIds(7), ["111", "222"])
})

test("renderSubscriptionAlbums prunes selections that are no longer selectable", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  setSubscriptionAlbumSelected(7, "111", true)
  setSubscriptionAlbumSelected(7, "333", true)

  const html = renderSubscriptionAlbums({
    id: 7,
    recentAlbums: [{
      albumId: "111",
      albumName: "Still Pending",
      albumUrl: "https://music.apple.com/cn/album/still-pending/111",
      userState: "pending",
      detectedStatus: "missing",
    }, {
      albumId: "333",
      albumName: "Now Running",
      albumUrl: "https://music.apple.com/cn/album/now-running/333",
      userState: "pending",
      detectedStatus: "running",
    }],
  })

  assert.deepEqual(getSelectedSubscriptionAlbumIds(7), ["111"])
  assert.match(html, /待确认 1 · 已选 1/)
  assert.doesNotMatch(html, /data-album-id="333"[^>]+checked/)
})

test("subscription album list scroll positions restore per subscription", () => {
  clearAllSubscriptionAlbumScrollPositions()
  const firstRoot = createFakeSubscriptionScrollRoot(7, 238)
  const secondRoot = createFakeSubscriptionScrollRoot(8, 64)

  assert.equal(saveSubscriptionAlbumScrollPosition(firstRoot), 238)
  assert.equal(saveSubscriptionAlbumScrollPosition(secondRoot), 64)

  const rerenderedFirstRoot = createFakeSubscriptionScrollRoot(7, 0)
  const rerenderedSecondRoot = createFakeSubscriptionScrollRoot(8, 0)

  assert.equal(restoreSubscriptionAlbumScrollPosition(rerenderedFirstRoot, 7), 238)
  assert.equal(rerenderedFirstRoot.albumList.scrollTop, 238)
  assert.equal(restoreSubscriptionAlbumScrollPosition(rerenderedSecondRoot, 8), 64)
  assert.equal(rerenderedSecondRoot.albumList.scrollTop, 64)
})

test("renderArtistSearchResults shows direct add subscription actions", () => {
  withFakeElement("artist-search-results", (element) => {
    renderArtistSearchResults([{
      artistId: "12345",
      artistName: "Example Artist",
      storefront: "cn",
      artistUrl: "https://music.apple.com/cn/artist/example/12345",
      artistArtworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/artist-example/512x512bb.jpg",
    }])

    assert.match(element.innerHTML, /搜索结果 · 点击添加订阅/)
    assert.match(element.innerHTML, /class="primary-button compact-button artist-subscribe-btn"/)
    assert.match(element.innerHTML, /添加订阅/)
    assert.match(element.innerHTML, /Example Artist/)
  })
})

test("renderSubscriptionList renders card grid and active detail panel", () => {
  clearAllSubscriptionAlbumSelections()
  clearAllSubscriptionAlbumFilters()
  withFakeElement("subscription-list", (element) => {
    renderSubscriptionList([{
      id: 7,
      artistName: "Example Artist",
      artistId: "12345",
      storefront: "cn",
      artistArtworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/artist-example/512x512bb.jpg",
      enabled: true,
      newAlbumPolicy: "confirm",
      albumCount: 1,
      pendingAlbumCount: 0,
      activeAlbumCount: 0,
      completedAlbumCount: 1,
      failedAlbumCount: 0,
      ignoredAlbumCount: 0,
      importedAlbumCount: 0,
      lastCheckedAt: "2026-06-11 10:00:00",
      recentAlbums: [{
        albumId: "111",
        albumName: "New Album",
        albumUrl: "https://music.apple.com/cn/album/new-album/111",
        artworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/album-111/512x512bb.jpg",
        releaseDate: "2026-06-01",
        userState: "pending",
        detectedStatus: "missing",
        canDownload: true,
      }],
    }, {
      id: 8,
      artistName: "Second Artist",
      artistId: "67890",
      storefront: "us",
      artistArtworkUrl: "https://is1-ssl.mzstatic.com/image/thumb/artist-second/512x512bb.jpg",
      enabled: true,
      newAlbumPolicy: "auto",
      albumCount: 0,
      pendingAlbumCount: 0,
      activeAlbumCount: 0,
      completedAlbumCount: 0,
      failedAlbumCount: 0,
      ignoredAlbumCount: 0,
      importedAlbumCount: 0,
      lastCheckedAt: "",
      recentAlbums: [],
    }])

    assert.match(element.innerHTML, /class="subscription-card-grid"/)
    assert.match(element.innerHTML, /class="subscription-card selected"/)
    assert.match(element.innerHTML, /class="subscription-card-artwork"/)
    assert.match(element.innerHTML, /class="subscription-detail-panel"/)
    assert.match(element.innerHTML, /class="subscription-detail-artwork"/)
    assert.match(element.innerHTML, /class="subscription-stat-filter selected"/)
    assert.match(element.innerHTML, /data-album-filter="active"/)
    assert.match(element.innerHTML, /class="subscription-album-search-input"/)
    assert.match(element.innerHTML, /Example Artist/)
    assert.match(element.innerHTML, /New Album/)
  })
})

test("showView displays only the requested panel", () => {
  withFakeNavigationDom(({ panels, links, replaceStateCalls }) => {
    showView("subscriptions")

    assert.equal(panels.find((panel) => panel.dataset.view === "console").hidden, true)
    assert.equal(panels.find((panel) => panel.dataset.view === "subscriptions").hidden, false)
    assert.equal(links.find((link) => link.dataset.viewTarget === "subscriptions").classList.classes.has("active"), true)
    assert.deepEqual(replaceStateCalls, ["#subscriptions"])
  })
})

test("bindViewNavigation switches panels from sidebar clicks", () => {
  withFakeNavigationDom(({ panels, links }) => {
    bindViewNavigation()
    let defaultPrevented = false

    links.find((link) => link.dataset.viewTarget === "history").listeners.click({
      preventDefault() {
        defaultPrevented = true
      },
    })

    assert.equal(defaultPrevented, true)
    assert.equal(getInitialViewName(), "history")
    assert.equal(panels.find((panel) => panel.dataset.view === "console").hidden, true)
    assert.equal(panels.find((panel) => panel.dataset.view === "history").hidden, false)
  })
})

test("bindSidebarToggle restores and toggles collapsed sidebar", () => {
  withFakeSidebarDom(({ storage, toggle }) => {
    bindSidebarToggle()

    assert.equal(global.document.body.classList.contains("sidebar-collapsed"), true)
    assert.equal(toggle.attributes["aria-expanded"], "false")
    assert.equal(toggle.attributes["aria-label"], "展开侧栏")

    toggle.listeners.click()

    assert.equal(global.document.body.classList.contains("sidebar-collapsed"), false)
    assert.equal(storage["amd-sidebar-collapsed"], "0")
    assert.equal(toggle.attributes["aria-expanded"], "true")
    assert.equal(toggle.attributes["aria-label"], "收起侧栏")
  })
})

test("setSidebarCollapsed updates control state", () => {
  withFakeSidebarDom(({ storage, toggle }) => {
    setSidebarCollapsed(true)

    assert.equal(global.document.body.classList.contains("sidebar-collapsed"), true)
    assert.equal(storage["amd-sidebar-collapsed"], "1")
    assert.equal(toggle.attributes.title, "展开侧栏")
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

  assert.equal(message, "扫描 2 个订阅，发现 8 个专辑，待确认 0 个，入队 3 个，历史跳过 4 个，队列跳过 1 个，忽略 0 个，已导入 0 个，错误 0 个")
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

  assert.equal(message, "扫描 0 个订阅，发现 0 个专辑，待确认 0 个，入队 0 个，历史跳过 0 个，队列跳过 0 个，忽略 0 个，已导入 0 个，错误 0 个")
})

test("formatSubscriptionActionSummary reports manually completed albums", () => {
  const message = formatSubscriptionActionSummary({
    action: "mark_completed",
    updatedCount: 2,
  })

  assert.equal(message, "已确认完成 2 个专辑")
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
    pendingCount: 0,
    skippedCompletedCount: 1,
    skippedActiveCount: 0,
    skippedIgnoredCount: 0,
    skippedImportedCount: 0,
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

test("cancelTask posts to queued task cancel endpoint", async () => {
  const originalFetch = global.fetch
  let capturedUrl = null
  let capturedOptions = null
  global.fetch = async (url, options) => {
    capturedUrl = url
    capturedOptions = options
    return {
      ok: true,
      async json() {
        return { cancelled: true, task: { taskId: "task-1", status: "cancelled" } }
      },
    }
  }

  const payload = await cancelTask("task-1")

  assert.equal(capturedUrl, "/api/tasks/task-1/cancel")
  assert.equal(capturedOptions.method, "POST")
  assert.deepEqual(payload, { cancelled: true, task: { taskId: "task-1", status: "cancelled" } })

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
