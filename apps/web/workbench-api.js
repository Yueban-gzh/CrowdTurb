export async function loadWorkbenchView(runId, view = "baseline") {
  if (!runId) throw new Error("缺少运行编号。");
  async function read(url) {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
    return data;
  }

  const path = `/api/workbench/runs/${encodeURIComponent(runId)}/views/${encodeURIComponent(view)}`;
  const manifest = await read(path);
  if (manifest.schema_version !== "1.0.0" || manifest.run_id !== runId || manifest.view !== view) {
    throw new Error("回放清单与所选运行不一致。");
  }
  const names = ["scene", "trajectory", "field", "analysis"];
  const values = await Promise.all(names.map((name) => read(manifest.artifacts[name])));
  return {manifest, ...Object.fromEntries(names.map((name, index) => [name, values[index]]))};
}
