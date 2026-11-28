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
    source = 'import { loadWorkbenchView } from "./workbench-api.js";\n' + source
    source = replace_once(source, "adaptAnalysis, adaptTrajectoryScene", "adaptAnalysis, adaptDerivedField, adaptTrajectoryScene")
    source = replace_once(
        source, "  const dataset = DATASETS[datasetId];",
        """  const {scene: rawScene, trajectory: rawPlayback, analysis: rawAnalysis, field} =
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
    return replace_once(
        source,
        "  const fieldForHeat = playback.kind === 'trajectory'\n    ? trajectoryToField(playback, scene)\n    : playback;",
        "  const fieldForHeat = adaptDerivedField(field);",
    )


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
    return re.sub(
        r'<div class="mock-banner" role="note">.*?</div>',
        '<div class="mock-banner" role="note">合成轨迹 · 热力来自 D 的聚合结果</div>',
        html, count=1, flags=re.S,
    )
