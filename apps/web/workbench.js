"use strict";

const byId = (id) => document.getElementById(id);
const base = "/api/workbench";
const editor = byId("editor");
const viewer = byId("viewer");
const runButton = byId("run");
const parameter = byId("parameter");
const choice = byId("view-choice");
const filter = byId("candidate-filter");
const tabs = [...document.querySelectorAll("[data-tab]")];
const metricNames = {
  remaining_at_end: "结束时在场人数",
  congestion_area_time: "累计拥堵面积时间",
  exited_people: "已离开人数",
};
const candidateStatus = {
  improved: "改善", worsened: "变差", unchanged: "持平", tradeoff: "各有优劣",
  pending: "待结果", failed: "失败", incomparable: "不可比较",
  duplicate: "等效输入", over_budget: "超出预算", invalid: "几何无效",
};
const attentionStatuses = new Set(["pending", "failed", "incomparable", "invalid", "over_budget", "infeasible"]);
const units = {person: "人", "m2*s": "m²·s"};
let currentScene;
let currentRun;
let receivingScene = false;
let editorTimer;
let activeRunId;
let editorReady = false;
let configuredSource = "mock";
let replayPose;
const runLabels = {queued: "排队中", running: "评估中", succeeded: "已完成", failed: "失败"};

async function api(path, payload) {
  const response = await fetch(base + path, payload ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  } : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
  return data;
}

function status(title, message, state = "ready") {
  byId("status-title").textContent = title;
  byId("status").textContent = message;
  byId("status").classList.toggle("error", state === "error");
  byId("run-indicator").dataset.state = state;
}

function showScene(scene) {
  const [width, depth] = scene.geometry.size;
  const name = document.createElement("strong");
  name.textContent = scene.meta.name;
  const details = document.createElement("span");
  details.textContent = `${width} × ${depth} m · ${scene.exits.length} 个出口 · ${scene.geometry.facilities?.length || 0} 处设施`;
  byId("scene-facts").replaceChildren(name, details);
  const selected = parameter.value;
  const options = (scene.variable_parameters || []).map((item) =>
    new Option(item.name, JSON.stringify({type: item.type, target_id: item.target_id})));
  parameter.replaceChildren(new Option("自动选择", ""), ...options);
  if (options.some((option) => option.value === selected)) parameter.value = selected;
  byId("parameter-label").hidden = !options.length;
}

function showSource(source) {
  const model = source === "stddn";
  const label = byId("run-source");
  label.textContent = model ? "STDDN / ONNX" : "Mock 演示";
  label.dataset.source = source;
  byId("replay-source").textContent = model ? "ONNX 仿真轨迹 · 密度热力" : "合成轨迹 · 密度热力";
}

function applyFrameTheme(frame, themeName) {
  const page = frame.contentDocument;
  if (!page?.head || page.getElementById("workbench-theme")) return;
  page.documentElement.classList.add(themeName);
  const theme = page.createElement("link");
  theme.id = "workbench-theme";
  theme.rel = "stylesheet";
  theme.href = "/workbench.css";
  page.head.append(theme);
}

function showTab(tab) {
  const workspace = document.querySelector(".workspace");
  if (workspace.dataset.activeTab !== tab) {
    workspace.dataset.activeTab = tab;
    byId("history").open = tab !== "edit";
  }
  for (const button of tabs) {
    const active = button.dataset.tab === tab;
    button.setAttribute("aria-pressed", String(active));
    byId(button.dataset.tab).hidden = !active;
  }
}

function selectView(view, preservePose = true) {
  if (preservePose) replayPose = viewer.contentWindow?.crowdTurbViewState?.() ?? replayPose;
  choice.value = view;
  showTab("view");
  const params = new URLSearchParams({run: currentRun.run_id, view});
  if (replayPose) params.set("pose", JSON.stringify(replayPose));
  viewer.src = `/viewer/index.html?${params}`;
}

const formatNumber = (value) => typeof value === "number" ? Number(value.toPrecision(6)) : value ?? "—";
const locationLabel = (point) => `(${point.map(formatNumber).join(", ")})`;

function metricTile(key, metric, delta) {
  const item = document.createElement("div");
  item.className = "metric";
  const label = document.createElement("span");
  label.className = "metric-label";
  label.textContent = metricNames[key] || key;
  const value = document.createElement("strong");
  value.className = "metric-value";
  value.textContent = formatNumber(metric.value);
  const unit = document.createElement("span");
  unit.className = "metric-unit";
  unit.textContent = units[metric.unit] || metric.unit;
  value.append(" ", unit);
  item.append(label, value);
  if (typeof delta === "number") {
    const change = document.createElement("span");
    change.className = "metric-delta";
    change.textContent = `${delta > 0 ? "+" : ""}${formatNumber(delta)}`;
    if (delta !== 0) {
      const improved = key === "exited_people" ? delta > 0 : delta < 0;
      change.dataset.trend = improved ? "better" : "worse";
    }
    item.append(change);
  }
  return item;
}

function changeLabel(change) {
  if (change.exit_id || change.passage_id) {
    const collection = change.exit_id ? currentScene.exits : currentScene.geometry.passages;
    const id = change.exit_id || change.passage_id;
    const previous = collection?.find((item) => item.id === id)?.width;
    const width = previous == null ? formatNumber(change.width) : `${formatNumber(previous)} → ${formatNumber(change.width)}`;
    return `${id} ${change.exit_id ? "出口" : "通道"}宽 ${width} m`;
  }
  if (change.facility_id && change.center) {
    const original = currentScene.geometry.facilities?.find((item) => item.id === change.facility_id);
    const from = original ? `${locationLabel(original.center)} → ` : "";
    return `${original?.name || change.facility_id} 位置 ${from}${locationLabel(change.center)} m`;
  }
  return change.facility_id || "布局变化";
}

function showCandidates(run) {
  const report = run.comparison;
  const match = filter.value === "attention"
    ? (candidate) => attentionStatuses.has(candidate.status)
    : (candidate) => filter.value === "all" || candidate.status === filter.value;
  const nodes = report.candidates.filter(match).map((candidate) => {
    const item = document.createElement("li");
    item.className = "candidate";
    const recommended = candidate.candidate_id === report.recommendation;
    item.classList.toggle("is-recommended", recommended);
    const content = document.createElement("div");
    const title = document.createElement("div");
    title.className = "candidate-title";
    const name = document.createElement("strong");
    name.textContent = candidate.candidate_id;
    const badge = document.createElement("span");
    badge.className = "candidate-badge";
    badge.dataset.status = candidate.status;
    badge.textContent = candidateStatus[candidate.status] || candidate.status;
    title.append(name, badge);
    if (recommended) {
      const highlight = document.createElement("span");
      highlight.className = "candidate-badge";
      highlight.textContent = run.source === "stddn" ? "指标推荐" : "示例推荐";
      title.append(highlight);
    }
    const changes = document.createElement("p");
    changes.className = "candidate-change";
    changes.textContent = candidate.changes.map(changeLabel).join(" / ");
    content.append(title, changes);
    item.append(content);
    if (candidate.metrics) {
      const values = document.createElement("div");
      values.className = "metrics candidate-metrics";
      values.append(...Object.entries(candidate.metrics).map(([key, metric]) => {
        const baseline = report.baseline_metrics[key]?.value;
        const delta = candidate.deltas?.[key] ?? (
          typeof baseline === "number" && typeof metric.value === "number" ? metric.value - baseline : null
        );
        return metricTile(key, metric, delta);
      }));
      content.append(values);
    }
    if (candidate.reasons.length) {
      const reasons = document.createElement("p");
      reasons.textContent = candidate.reasons.join(" · ");
      content.append(reasons);
    }
    if (run.views.includes(candidate.candidate_id)) {
      const button = document.createElement("button");
      button.textContent = "回放此方案";
      button.addEventListener("click", () => selectView(candidate.candidate_id));
      item.append(button);
    }
    return item;
  });
  if (!nodes.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "当前条件下没有候选。";
    nodes.push(empty);
  }
  byId("candidates").replaceChildren(...nodes);
}

function showRun(run) {
  currentRun = run;
  replayPose = undefined;
  showSource(run.source);
  showScene(currentScene);
  if (run.parameter) {
    const selected = JSON.stringify({type: run.parameter.type, target_id: run.parameter.target_id});
    if ([...parameter.options].some((option) => option.value === selected)) parameter.value = selected;
  }
  for (const button of tabs) button.disabled = false;
  const report = run.comparison;
  const recommended = report.candidates.find((candidate) => candidate.candidate_id === report.recommendation);
  const model = run.source === "stddn";
  status(model ? "ONNX 评估完成" : "Mock 评估完成", recommended
    ? `${model ? "指标推荐" : "示例推荐"} ${recommended.candidate_id} · 共 ${report.candidates.length} 个候选`
    : `保留基准 · 共 ${report.candidates.length} 个候选`);
  const revision = byId("revision");
  revision.hidden = false;
  revision.textContent = `场景版本 ${run.scene_revision} · 输入指纹 ${run.input_hash.slice(0, 12)}`;
  byId("comparison-note").textContent = `共 ${report.candidates.length} 个候选 · 指标差值相对基准`;

  byId("metric-panel").hidden = false;
  byId("metrics").replaceChildren(...Object.entries(report.baseline_metrics).map(([key, metric]) => metricTile(key, metric)));
  filter.value = "all";
  showCandidates(run);
  choice.replaceChildren(...run.views.map((view) => new Option(view === "baseline" ? "基准" : view, view)));
  selectView("baseline", false);
}

async function refreshHistory() {
  const {runs} = await api("/runs");
  byId("history-empty").hidden = runs.length > 0;
  byId("runs").replaceChildren(...runs.slice(0, 8).map((run) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `/?run=${encodeURIComponent(run.run_id)}`;
    const summary = document.createElement("span");
    summary.className = "history-summary";
    const name = document.createElement("strong");
    name.textContent = run.scene_name;
    const label = document.createElement("span");
    label.className = "history-state";
    label.dataset.state = run.status;
    label.textContent = runLabels[run.status];
    summary.append(name, label);
    if (run.run_id === activeRunId) link.setAttribute("aria-current", "page");
    const time = document.createElement("time");
    time.dateTime = run.created_at;
    time.textContent = new Date(run.created_at).toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"});
    link.append(summary, time);
    item.append(link);
    return item;
  }));
}

function setBusy(busy) {
  runButton.disabled = busy || !editorReady;
  parameter.disabled = busy;
  editor.inert = busy;
}

function showRunProgress(run) {
  currentRun = null;
  showSource(run.source);
  byId("metric-panel").hidden = true;
  byId("revision").hidden = false;
  byId("revision").textContent = run.run_id;
  for (const button of tabs) button.disabled = button.dataset.tab !== "edit";
  showTab("edit");
  viewer.removeAttribute("src");
  if (run.status === "failed") {
    status("评估失败", run.error?.message || "没有可用结果，请检查场景后重试。", "error");
  } else {
    status(runLabels[run.status], "运行已保存，刷新页面后仍可查看。", "running");
  }
}

async function followRun(initial) {
  let run = initial;
  activeRunId = run.run_id;
  const path = `/runs/${encodeURIComponent(run.run_id)}`;
  while (run.status === "queued" || run.status === "running") {
    showRunProgress(run);
    await new Promise((resolve) => window.setTimeout(resolve, 650));
    const data = await api(path);
    run = data.run;
    currentScene = data.scene;
  }
  if (run.status === "succeeded") {
    editor.contentWindow.postMessage({type: "crowdturb:load-scene", scene: currentScene}, location.origin);
    showRun(run);
  } else {
    showRunProgress(run);
  }
  await refreshHistory();
}

window.addEventListener("message", async (event) => {
  if (event.origin !== location.origin || event.source !== editor.contentWindow) return;
  if (event.data?.type === "crowdturb:editor-ready") {
    editorReady = true;
    applyFrameTheme(editor, "ct-editor");
    editor.contentWindow.postMessage({type: "crowdturb:load-scene", scene: currentScene}, location.origin);
    runButton.disabled = editor.inert;
    if (!activeRunId) {
      status("可编辑布局", "修改出口或展台后，可评估布局并比较拥堵指标。");
    }
    return;
  }
  if (event.data?.type !== "crowdturb:scene" || !receivingScene) return;
  receivingScene = false;
  window.clearTimeout(editorTimer);
  try {
    const {run} = await api("/runs", {
      scene: event.data.scene,
      parameter: parameter.value ? JSON.parse(parameter.value) : null,
    });
    currentScene = event.data.scene;
    history.replaceState({}, "", `/?run=${encodeURIComponent(run.run_id)}`);
    await followRun(run);
  } catch (error) {
    status("无法读取评估状态", error.message, "error");
    showTab("edit");
  } finally {
    setBusy(false);
  }
});

for (const button of tabs) button.addEventListener("click", () => showTab(button.dataset.tab));
viewer.addEventListener("load", () => applyFrameTheme(viewer, "ct-viewer"));
choice.addEventListener("change", () => selectView(choice.value));
filter.addEventListener("change", () => showCandidates(currentRun));
runButton.addEventListener("click", () => {
  setBusy(true);
  receivingScene = true;
  showSource(configuredSource);
  status("正在提交评估", "正在读取场景并创建评估任务。", "running");
  editor.contentWindow.postMessage({type: "crowdturb:request-scene"}, location.origin);
  editorTimer = window.setTimeout(() => {
    receivingScene = false;
    setBusy(false);
    status("编辑器未响应", "请刷新页面并重试。", "error");
  }, 4000);
});

async function start() {
  const saved = new URLSearchParams(location.search).get("run");
  const data = await api(saved ? `/runs/${encodeURIComponent(saved)}` : "/scene");
  configuredSource = data.source || "mock";
  showSource(data.run?.source || configuredSource);
  currentScene = data.scene?.meta && data.scene?.geometry ? data.scene : (await api("/scene")).scene;
  showScene(currentScene);
  activeRunId = data.run?.run_id;
  editor.src = "/editor/index.html";
  if (!data.run) {
    await refreshHistory();
    return;
  }
  setBusy(true);
  try {
    await followRun(data.run);
  } finally {
    setBusy(false);
  }
}

start().catch((error) => status("工作台加载失败", error.message, "error"));
