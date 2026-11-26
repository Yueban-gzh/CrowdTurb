"use strict";

const state = {
  projects: [],
  detail: null,
  result: null,
  pollToken: 0,
  toastTimer: null,
};

class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const request = {
    method: options.method || "GET",
    headers: {},
  };
  if (options.body !== undefined) {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiError("服务返回了无法解析的响应。", "invalid_response", response.status);
    }
  }
  if (!response.ok) {
    const error = data.error || {};
    throw new ApiError(error.message || `请求失败（HTTP ${response.status}）`, error.code, response.status);
  }
  return data;
}

function setServiceState(kind, text) {
  const element = byId("service-state");
  element.textContent = text;
  element.classList.remove("is-online", "is-offline");
  if (kind) element.classList.add(kind);
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 4200);
}

function setBusy(button, busy) {
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? "处理中…" : button.dataset.label;
}

function sourceLabel(source) {
  if (source === "mock") return "Mock 联调";
  if (source === "pde") return "PDE";
  if (source === "fno") return "FNO";
  return source || "未知来源";
}

function formatTimeRange(result) {
  if (!result || !Array.isArray(result.frames) || result.frames.length === 0) return "—";
  const first = result.frames[0].time_s;
  const last = result.frames[result.frames.length - 1].time_s;
  return `${first}s → ${last}s`;
}

function renderProjectList() {
  const list = byId("project-list");
  list.replaceChildren();
  if (state.projects.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "还没有项目。点击右上角＋创建。";
    list.append(empty);
    return;
  }
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-button";
    button.classList.toggle("is-active", state.detail?.project?.project_id === project.project_id);
    const name = document.createElement("strong");
    name.textContent = project.name;
    const meta = document.createElement("span");
    meta.textContent = `revision ${project.current_revision} · ${project.project_id}`;
    button.append(name, meta);
    button.addEventListener("click", () => {
      selectProject(project.project_id).catch(handleError);
    });
    list.append(button);
  }
}

function renderScene() {
  if (!state.detail) {
    byId("scene-editor").value = "";
    byId("scene-revision").textContent = "revision —";
    return;
  }
  const { scene, project } = state.detail;
  byId("scene-editor").value = JSON.stringify(scene, null, 2);
  byId("scene-revision").textContent = `revision ${scene.revision}`;
  byId("hero-project").textContent = project.name;
  byId("hero-scene").textContent = `r${scene.revision} · ${scene.scene_id}`;
}

function renderTasks() {
  const feed = byId("task-feed");
  feed.replaceChildren();
  const tasks = state.detail?.tasks || [];
  byId("hero-task").textContent = tasks.length ? tasks[0].status : "—";
  if (tasks.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "尚未运行任务。选择 Mock 后点击“运行任务”，可验证成功链路；选择 PDE/FNO 可验证明确的失败反馈。";
    feed.append(empty);
    return;
  }
  for (const task of tasks) {
    const card = document.createElement("article");
    card.className = "task-card";
    const title = document.createElement("strong");
    title.textContent = `${sourceLabel(task.source)} · ${task.task_id}`;
    const meta = document.createElement("small");
    meta.textContent = `scene r${task.scene_revision}`;
    const status = document.createElement("span");
    status.className = `status status--${task.status}`;
    status.textContent = task.status;
    card.append(title, status, meta);
    if (task.error) {
      const error = document.createElement("p");
      error.textContent = `${task.error.code}: ${task.error.message}`;
      card.append(error);
    }
    feed.append(card);
  }
}

function renderResult() {
  const summary = byId("result-summary");
  summary.replaceChildren();
  const result = state.result;
  const stats = result
    ? [
        ["result_id", result.result_id],
        ["source", sourceLabel(result.source)],
        ["frames", `${result.frames.length} 帧`],
        ["time range", formatTimeRange(result)],
      ]
    : [
        ["result_id", "—"],
        ["source", "尚无结果"],
        ["frames", "—"],
        ["time range", "—"],
      ];
  for (const [label, value] of stats) {
    const item = document.createElement("div");
    item.className = "result-stat";
    const key = document.createElement("span");
    key.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    item.append(key, strong);
    summary.append(item);
  }
  byId("result-state").textContent = result ? `${sourceLabel(result.source)} · ${result.frames.length} 帧` : "尚无结果";
  byId("result-state").classList.toggle("tag--warm", Boolean(result));
  byId("result-json").textContent = result
    ? JSON.stringify(result, null, 2)
    : "运行 Mock 任务后在此查看。";

  const viewSlot = byId("view-slot");
  const analysisSlot = byId("analysis-slot");
  viewSlot.querySelector("small").textContent = result ? "场结果已就绪，等待 B 渲染" : "读取 field result 与 scene";
  analysisSlot.querySelector("small").textContent = result ? "场结果已就绪，等待 D 指标" : "读取 field result，输出指标";
}

function updateFlowForTask(task) {
  const compute = document.querySelector('[data-step="compute"]');
  if (!compute || !task) return;
  compute.classList.remove("is-pending");
  const stateLabel = compute.querySelector(".flow-state");
  if (task.status === "succeeded") {
    compute.classList.add("is-ready");
    stateLabel.textContent = `${sourceLabel(task.source)} 已保存`;
    document.querySelector('[data-step="view"] .flow-state').textContent = "结果就绪";
    document.querySelector('[data-step="analysis"] .flow-state').textContent = "结果就绪";
  } else if (task.status === "failed") {
    compute.classList.remove("is-ready");
    stateLabel.textContent = "失败有记录";
  } else {
    stateLabel.textContent = task.status;
  }
}

function resetFlowForScene() {
  const compute = document.querySelector('[data-step="compute"]');
  if (!compute) return;
  compute.classList.remove("is-ready", "is-pending");
  compute.querySelector(".flow-state").textContent = "Mock 调度可用";
  document.querySelector('[data-step="view"] .flow-state').textContent = "待接入";
  document.querySelector('[data-step="analysis"] .flow-state').textContent = "待接入";
}

async function refreshProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  renderProjectList();
}

async function refreshDetail() {
  if (!state.detail) return;
  const id = state.detail.project.project_id;
  const detail = await api(`/api/projects/${encodeURIComponent(id)}`);
  state.detail = detail;
  renderProjectList();
  renderScene();
  renderTasks();
  const latest = (detail.tasks || []).find(
    (task) => task.status === "succeeded" && task.result_id && task.scene_revision === detail.scene.revision,
  );
  if (latest) {
    updateFlowForTask(latest);
    if (!state.result || state.result.result_id !== latest.result_id) {
      const resultData = await api(`/api/results/${encodeURIComponent(latest.result_id)}`);
      state.result = resultData.result;
    }
  } else {
    state.result = null;
    const newest = (detail.tasks || [])[0];
    if (!newest || newest.scene_revision !== detail.scene.revision) {
      resetFlowForScene();
    }
  }
  renderResult();
}

async function selectProject(projectId) {
  state.pollToken += 1;
  state.result = null;
  resetFlowForScene();
  const detail = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  state.detail = detail;
  renderProjectList();
  renderScene();
  renderTasks();
  renderResult();
  const latest = (detail.tasks || []).find(
    (task) => task.status === "succeeded" && task.result_id && task.scene_revision === detail.scene.revision,
  );
  if (latest) {
    updateFlowForTask(latest);
    const resultData = await api(`/api/results/${encodeURIComponent(latest.result_id)}`);
    state.result = resultData.result;
    renderResult();
  }
  const newest = (detail.tasks || [])[0];
  if (newest && newest.scene_revision === detail.scene.revision) {
    updateFlowForTask(newest);
  }
}

async function createProject() {
  const suggested = `体育馆方案 ${state.projects.length + 1}`;
  const name = window.prompt("新项目名称", suggested);
  if (name === null) return;
  try {
    const data = await api("/api/projects", { method: "POST", body: { name } });
    await refreshProjects();
    await selectProject(data.project.project_id);
    showToast(`已创建项目：${data.project.name}`);
  } catch (error) {
    handleError(error);
  }
}

async function saveScene() {
  if (!state.detail) {
    showToast("请先选择项目。", true);
    return;
  }
  const button = byId("save-scene");
  let scene;
  try {
    scene = JSON.parse(byId("scene-editor").value);
  } catch (error) {
    showToast(`场景 JSON 无法解析：${error.message}`, true);
    return;
  }
  setBusy(button, true);
  try {
    const data = await api(`/api/projects/${encodeURIComponent(state.detail.project.project_id)}/scenes`, {
      method: "POST",
      body: { scene },
    });
    await refreshDetail();
    showToast(`已保存场景 revision ${data.scene.revision}`);
  } catch (error) {
    handleError(error);
  } finally {
    setBusy(button, false);
  }
}

async function runTask() {
  if (!state.detail) {
    showToast("请先选择项目。", true);
    return;
  }
  const button = byId("run-task");
  const source = byId("task-source").value;
  setBusy(button, true);
  try {
    const data = await api(`/api/projects/${encodeURIComponent(state.detail.project.project_id)}/tasks`, {
      method: "POST",
      body: { source, scene_revision: state.detail.scene.revision },
    });
    const task = data.task;
    updateFlowForTask(task);
    await refreshDetail();
    showToast(`任务 ${task.task_id} 已入队（${sourceLabel(source)}）`);
    const token = ++state.pollToken;
    pollTask(task.task_id, token).catch(handleError);
  } catch (error) {
    handleError(error);
  } finally {
    setBusy(button, false);
  }
}

async function pollTask(taskId, token) {
  const terminal = new Set(["succeeded", "failed"]);
  while (token === state.pollToken) {
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
    const task = data.task;
    updateFlowForTask(task);
    if (terminal.has(task.status)) {
      await refreshDetail();
      if (task.status === "failed") {
        showToast(task.error?.message || "任务失败。", true);
      } else {
        showToast(`任务完成：${task.result_id}`);
      }
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 650));
  }
}

function handleError(error) {
  console.error(error);
  const message = error instanceof ApiError ? error.message : `客户端错误：${error.message || error}`;
  showToast(message, true);
}

function wireEvents() {
  byId("new-project").addEventListener("click", createProject);
  byId("save-scene").addEventListener("click", saveScene);
  byId("run-task").addEventListener("click", runTask);
}

async function init() {
  wireEvents();
  try {
    const health = await api("/api/health");
    setServiceState("is-online", `服务在线 · ${health.service}`);
    const adapter = health.adapters || {};
    byId("adapter-state").textContent = `Mock ${adapter.mock || "unknown"} · PDE ${adapter.pde || "unknown"}`;
  } catch (error) {
    setServiceState("is-offline", "服务不可用");
    handleError(error);
    return;
  }

  try {
    await refreshProjects();
    if (state.projects.length === 0) {
      const created = await api("/api/projects", {
        method: "POST",
        body: { name: "体育馆示例（Mock 联调）" },
      });
      await refreshProjects();
      await selectProject(created.project.project_id);
    } else {
      await selectProject(state.projects[0].project_id);
    }
  } catch (error) {
    handleError(error);
  }
}

document.addEventListener("DOMContentLoaded", init);
