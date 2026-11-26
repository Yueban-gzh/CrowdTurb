"use strict";

const element = (id) => document.getElementById(id);
const labels = { playback: "轨迹与分析", comparison: "方案比较" };
const collection = "/api/review-runs";

async function request(url, body) {
  const response = await fetch(url, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
  return data;
}

function listItem(text) {
  const item = document.createElement("li");
  if (text) item.textContent = text;
  return item;
}

async function showRun(runId) {
  const { run } = await request(`${collection}/${encodeURIComponent(runId)}`);
  element("review-title").textContent = `${labels[run.kind]} · Mock`;
  const metadata = [
    ["运行编号", run.run_id],
    ["场景", run.scene_id],
    ["场景版本", run.scene_revision ?? "比较样例未声明"],
    ["输入哈希", run.input_hash],
  ];
  if (run.baseline_result_id) metadata.push(["基准结果编号", run.baseline_result_id]);
  const details = element("review-metadata");
  details.replaceChildren();
  for (const [label, value] of metadata) {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    details.append(term, description);
  }
  const links = element("review-artifacts");
  links.replaceChildren();
  for (const [name, artifact] of Object.entries(run.artifacts)) {
    const item = listItem();
    const link = document.createElement("a");
    link.href = artifact.url;
    link.textContent = `${name} ${artifact.schema_version} · JSON`;
    item.append(link);
    links.append(item);
  }
  const summary = element("review-summary");
  const artifact = run.artifacts.analysis || run.artifacts.comparison;
  const data = await request(artifact.url);
  const items = run.kind === "playback"
    ? [`分析时间 ${data.time_range_s.join("–")} s · 热点 ${data.hotspots.length} 处`,
       ...Object.entries(data.metrics).map(([name, metric]) => `${name} · ${metric.value} ${metric.unit}`)]
    : [`Mock 推荐结果 · ${data.recommendation ?? "未给出"}`,
       ...data.candidates.map((candidate) =>
         [candidate.candidate_id, candidate.status, ...candidate.reasons].join(" · "))];
  summary.replaceChildren(...items.map(listItem));
  element("review-detail").hidden = false;
}

async function refreshRuns() {
  const { runs } = await request(collection);
  const list = element("review-runs");
  list.replaceChildren();
  for (const run of runs) {
    const item = listItem();
    const link = document.createElement("a");
    link.href = `/review?run=${encodeURIComponent(run.run_id)}`;
    link.textContent = `${labels[run.kind]} · ${run.scene_id} · ${run.created_at}`;
    item.append(link);
    list.append(item);
  }
}

function reportError(error) {
  element("review-status").textContent = error.message;
}

for (const kind of Object.keys(labels)) {
  const button = element(`import-${kind}`);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const { run } = await request(collection, { kind });
      window.location.assign(`/review?run=${encodeURIComponent(run.run_id)}`);
    } catch (error) {
      reportError(error);
      button.disabled = false;
    }
  });
}

refreshRuns().catch(reportError);
const selected = new URLSearchParams(window.location.search).get("run");
if (selected) showRun(selected).catch(reportError);
