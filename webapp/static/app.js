const state = {
  selectedTaskId: "",
  manualSelection: false,
  eventSource: null,
  taskPoller: null,
};


function appendLog(message) {
  if (!message) {
    return;
  }
  const logs = document.getElementById("logs-output");
  logs.textContent += `${message}\n`;
  logs.scrollTop = logs.scrollHeight;
}


function clearTaskDetails() {
  document.getElementById("current-url").textContent = "-";
  document.getElementById("logs-output").textContent = "";
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
  const queueCount = tasks.filter((task) => task.status === "running").length;
  const successCount = tasks.filter((task) => task.status === "completed").length;
  const failureCount = tasks.filter((task) => task.status === "failed").length;
  document.getElementById("active-count").textContent = String(queueCount);
  document.getElementById("queue-count").textContent = String(queueCount);
  document.getElementById("success-count").textContent = String(successCount);
  document.getElementById("failure-count").textContent = String(failureCount);
}


function closeStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}


function openStream(taskId) {
  closeStream();
  const eventSource = new EventSource(`/api/tasks/${taskId}/stream`);
  state.eventSource = eventSource;

  eventSource.addEventListener("snapshot", (event) => {
    const data = JSON.parse(event.data);
    applySnapshot(data);
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
  if (isNewSelection) {
    document.getElementById("logs-output").textContent = "";
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
        <span class="badge task-source ${task.source === "telegram" ? "telegram" : "web"}">${task.source}</span>
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


document.getElementById("download-form").addEventListener("submit", (event) => {
  submitTask(event).catch(() => {
    document.getElementById("form-error").textContent = "提交失败";
  });
});

clearTaskDetails();
refreshTaskList().catch(() => {});
startTaskPolling();
