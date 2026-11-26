"""Serve prepared team assets and assemble the pinned B compatibility view."""

import base64
import hashlib
import re

B_FILES = {
    "index.html": "text/html; charset=utf-8",
    "main.js": "text/javascript; charset=utf-8",
    "adapters.js": "text/javascript; charset=utf-8",
    "workbench-api.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "lib/OrbitControls.js": "text/javascript; charset=utf-8",
    "lib/three.module.js": "text/javascript; charset=utf-8",
}


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("B 加载器已改变，请更新联调适配或使用 B 的新版接口。")
    return source.replace(old, new, 1)


def viewer_script(source):
    source = 'import { loadWorkbenchView, readWorkbenchPose } from "./workbench-api.js";\n' + source
    source = replace_once(source, "adaptAnalysis, adaptTrajectoryScene", "adaptAnalysis, adaptDerivedField, adaptTrajectoryScene")
    source = replace_once(
        source, "  const dataset = DATASETS[datasetId];",
        """  const {scene: rawScene, trajectory: rawPlayback, analysis: rawAnalysis, field, route} =
    await loadWorkbenchView(params.get('run'), params.get('view') || 'baseline');
  const dataset = {label: 'CrowdTurb 预演', scene: {format: 'bundle'}, playback: {format: 'trajectory'}};""",
    )
    source = replace_once(
        source,
        """  const [rawScene, rawPlayback, rawAnalysis] = await Promise.all([
    loadJson(dataset.scene.url),
    loadJson(dataset.playback.url),
    dataset.analysis ? loadJson(dataset.analysis.url).catch(() => null) : Promise.resolve(null),
  ]);
""",
        "",
    )
    source = replace_once(
        source,
        "  const fieldForHeat = playback.kind === 'trajectory'\n    ? trajectoryToField(playback, scene)\n    : playback;",
        "  const fieldForHeat = adaptDerivedField(field);",
    )
    return viewer_comparison(source)


def viewer_comparison(source):
    source = replace_once(source, "init3d(scene, playback, fieldForHeat, dMax, analysis);",
                          """if (params.get('paired') === '1') {
    initPairedPlan(scene, playback, fieldForHeat, analysis, route);
    return;
  }
  init3d(scene, playback, fieldForHeat, dMax, analysis, route, readWorkbenchPose(params));""")
    source = replace_once(source, "main().catch((err) => {", """main().catch((err) => {
  if (new URLSearchParams(location.search).get('paired') === '1') {
    window.parent.postMessage({type: 'crowdturb:pair-error'}, location.origin);
  }""")
    source += """

function initPairedPlan(scene, playback, field, analysis, route) {
  document.documentElement.classList.add('ct-paired-view');
  document.getElementById('view-2d-pane').classList.remove('hidden');
  const canvas = document.getElementById('view-2d');
  const exitCells = new Set(scene.exits.flatMap((exit) => exit.cells.map(([x, y]) => `${x},${y}`)));
  const start = field.frames[0].timeS;
  const end = field.frames.at(-1).timeS;
  const densityMax = robustDensityMax(field.frames);
  let lastField, lastPeople, lastScale;
  function drawAt(time, scale) {
    const sampled = field.frames.findLast((frame) => frame.timeS <= time) || field.frames[0];
    const people = playback.frames.findLast((frame) => frame.timeS <= time) || playback.frames[0];
    if (sampled === lastField && people === lastPeople && scale === lastScale) return;
    draw2d(canvas, scene, sampled.density, sampled.velocity, scale, null, exitCells,
      analysis, sampled.timeS, people.agents, scene.grid, route?.points_m);
    lastField = sampled;
    lastPeople = people;
    lastScale = scale;
  }
  window.crowdTurbPairedPlan = {start, end, densityMax, drawAt};
  window.parent.postMessage({type: 'crowdturb:pair-ready'}, location.origin);
}
"""
    source = replace_once(source, "function init3d(scene, playback, fieldForHeat, dMax, analysis) {",
                          "function init3d(scene, playback, fieldForHeat, dMax, analysis, route, pose) {")
    source = replace_once(source, "function markActive(name) {",
                          "let preset = 'persp';\n  function markActive(name) {\n    preset = name;")
    source = replace_once(source, "  window.addEventListener('resize', () => {", """  const routeToggle = document.getElementById('reference-route');
  routeToggle.closest('label').hidden = !route?.points_m?.length;
  routeToggle.checked = pose.reference === true;
  if (pose.position && pose.target) {
    camera.position.fromArray(pose.position);
    controls.target.fromArray(pose.target);
    controls.update();
    markActive(pose.preset === 'top' ? 'top' : 'persp');
  }
  setView2dEnabled(pose.mode === '2d');
  if (Number.isFinite(pose.time)) {
    tau = Math.min(tMax, Math.max(tMin, pose.time));
    slider.value = String(Math.round((tau - tMin) / span * 1000));
    playing = pose.playing === true;
    btnPlay.textContent = playing ? '暂停' : '播放';
  }
  window.crowdTurbViewState = () => ({
    mode: activeView,
    position: (camTween?.toPos || camera.position).toArray(),
    target: (camTween?.toTarget || controls.target).toArray(),
    preset,
    time: tau,
    playing,
    reference: routeToggle.checked,
  });

  window.addEventListener('resize', () => {""")
    source = replace_once(source, "agents, sceneSrcGrid) {", "agents, sceneSrcGrid, referenceRoute) {")
    source = replace_once(source, "frame2d ? frame2d.agents : null, scene.grid);",
                          "frame2d ? frame2d.agents : null, scene.grid, routeToggle.checked ? route?.points_m : null);")
    return replace_once(source, "  if (agents && agents.length > 0) {", """  if (referenceRoute?.length > 1) {
    const { dx, dy, origin } = scene.grid;
    ctx.save();
    ctx.beginPath();
    for (const [x, y] of referenceRoute) {
      ctx.lineTo(ox + (x - origin[0]) / dx * cs, oy + ny * cs - (y - origin[1]) / dy * cs);
    }
    ctx.setLineDash([7, 5]);
    ctx.lineWidth = 4;
    ctx.strokeStyle = '#0d2338';
    ctx.stroke();
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#c6e9ff';
    ctx.stroke();
    ctx.restore();
  }
  if (agents && agents.length > 0) {""")


def embedded_policy(html):
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.I | re.S)
    hashes = []
    for script in scripts:
        if script:
            digest = hashlib.sha256(script.encode("utf-8")).digest()
            hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    return (
        f"default-src 'self'; script-src 'self' {' '.join(hashes)}; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'self'"
    )


def viewer_page(html):
    html = re.sub(r'<header class="app-header">.*?</header>', "", html, count=1, flags=re.S)
    html = re.sub(r'<footer class="page-footer">.*?</footer>', "", html, count=1, flags=re.S)
    html = replace_once(
        html, '<canvas id="view-2d" width="1200" height="900"></canvas>',
        '<label class="hint cell-info-bar" hidden><input type="checkbox" id="reference-route"> '
        '显示参考路线（虚线）</label>\n        <canvas id="view-2d" width="1200" height="900"></canvas>',
    )
    return re.sub(
        r'<div class="mock-banner" role="note">.*?</div>',
        '<div class="mock-banner" role="note">运行轨迹 · 热力来自 D 的聚合结果</div>',
        html, count=1, flags=re.S,
    )
