"use strict";

const byId = (id) => document.getElementById(id);
const base = "/api/workbench";
const editor = byId("editor");
const viewer = byId("viewer");
const runButton = byId("run");
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
const runLabels = {queued: "排队中", running: "评估中", succeeded: "已完成", failed: "失败"};

async function api(path, scene) {
  const response = await fetch(base + path, scene ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({scene}),
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
  for (const button of tabs) {
    const active = button.dataset.tab === tab;
    button.setAttribute("aria-pressed", String(active));
    byId(button.dataset.tab).hidden = !active;
  }
}

function selectView(view) {
  choice.value = view;
  showTab("view");
  viewer.src = `/viewer/index.html?run=${encodeURIComponent(currentRun.run_id)}&view=${encodeURIComponent(view)}`;
}

function changeLabel(change) {
  if (change.exit_id) return `${change.exit_id} 出口宽 ${change.width} m`;
  if (change.facility_id && change.center) return `${change.facility_id} 展台位置 (${change.center.join(", ")}) m`;
  if (change.passage_id) return `${change.passage_id} 通道宽 ${change.width} m`;
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
      highlight.textContent = "示例推荐";
      title.append(highlight);
    }
    const changes = document.createElement("p");
    changes.textContent = candidate.changes.map(changeLabel).join(" / ");
    content.append(title, changes);
    item.append(content);
    if (candidate.metrics) {
      const values = document.createElement("p");
      values.textContent = Object.entries(candidate.metrics).map(([key, metric]) => {
        const delta = candidate.deltas?.[key];
        const diff = typeof delta === "number" && delta !== 0 ? `（${delta > 0 ? "+" : ""}${Number(delta.toPrecision(4))}）` : "";
        return `${metricNames[key] || key} ${metric.value} ${units[metric.unit] || metric.unit}${diff}`;
      }).join(" · ");
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
  showScene(currentScene);
  for (const button of tabs) button.disabled = false;
  const report = run.comparison;
  const recommended = report.candidates.find((candidate) => candidate.candidate_id === report.recommendation);
  status("Mock 评估完成", recommended ? `示例推荐 ${recommended.candidate_id} · 共 ${report.candidates.length} 个候选`
    : `保留基准 · 共 ${report.candidates.length} 个候选`);
  const revision = byId("revision");
  revision.hidden = false;
  revision.textContent = `场景版本 ${run.scene_revision} · 输入指纹 ${run.input_hash.slice(0, 12)}`;
  byId("comparison-note").textContent = `共 ${report.candidates.length} 个候选 · 使用合成轨迹 · 括号内为相对基准的变化`;

  byId("metric-panel").hidden = false;
  byId("metrics").replaceChildren(...Object.entries(report.baseline_metrics).map(([key, metric]) => {
    const item = document.createElement("div");
    item.className = "metric";
    item.textContent = metricNames[key] || key;
    const value = document.createElement("strong");
    value.textContent = `${metric.value} ${units[metric.unit] || metric.unit}`;
    item.append(value);
    return item;
  }));
  filter.value = "all";
  showCandidates(run);
  choice.replaceChildren(...run.views.map((view) => new Option(view === "baseline" ? "基准" : view, view)));
  selectView("baseline");
}

async function refreshHistory() {
  const {runs} = await api("/runs");
  byId("history-empty").hidden = runs.length > 0;
  byId("runs").replaceChildren(...runs.slice(0, 8).map((run) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.href = `/?run=${encodeURIComponent(run.run_id)}`;
    link.textContent = `${run.scene_name} · ${runLabels[run.status]}`;
    if (run.run_id === activeRunId) link.setAttribute("aria-current", "page");
    const time = document.createElement("time");
    time.dateTime = run.created_at;
    time.textContent = new Date(run.created_at).toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"});
    link.append(time);
    item.append(link);
    return item;
  }));
}

function setBusy(busy) {
  runButton.disabled = busy || !editorReady;
  editor.inert = busy;
}

function showRunProgress(run) {
  currentRun = null;
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
    const {run} = await api("/runs", event.data.scene);
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
  status("正在预演", "正在校验场景、生成候选和聚合 Mock 轨迹。", "running");
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
