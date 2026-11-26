"use strict";

// Reposition A's existing controls inside the embedded workbench.
const editorMain = document.querySelector("main");
const editorPanel = document.getElementById("panel");
const editorToolbar = editorPanel.querySelector(".toolbar");
editorMain.before(editorToolbar);
const selectionBox = document.getElementById("selBox");
const geometryHeading = document.getElementById("geoBox").previousElementSibling;
geometryHeading.before(selectionBox.previousElementSibling, selectionBox);

const rangeNotice = document.createElement("p");
rangeNotice.className = "action-notice";
rangeNotice.setAttribute("role", "status");
selectionBox.after(rangeNotice);
const rangeAdjustments = new Map();
const refreshDeclaredParameters = refreshVariableParameters;

function facilityCenterRange(facility, axis) {
  const footprint = facilityPolygon(facility);
  const [length, depth] = scene.geometry.size;
  const region = facility.allowed_region;
  if (!polygonInsideRect(footprint, 0, 0, length, depth)) return null;
  if (region && !polygonInsidePolygon(footprint, region)) return null;

  const footprintAxis = footprint.map((point) => point[axis]);
  const regionAxis = region?.map((point) => point[axis]) || [0, scene.geometry.size[axis]];
  const current = facility.center[axis];
  const low = current + Math.max(0, Math.min(...regionAxis)) - Math.min(...footprintAxis);
  const high = current + Math.min(scene.geometry.size[axis], Math.max(...regionAxis)) - Math.max(...footprintAxis);
  return [Math.min(low, current), Math.max(high, current)];
}

refreshVariableParameters = function () {
  refreshDeclaredParameters();
  const declarations = Array.isArray(scene.variable_parameters) ? scene.variable_parameters : [];
  for (const declaration of declarations) {
    const axis = ["facility_center_x", "facility_center_y"].indexOf(declaration?.type);
    const limits = declaration?.allowed_range;
    if (axis < 0 || !Array.isArray(limits) || limits.length !== 2 ||
        !limits.every(Number.isFinite) || limits[0] > limits[1]) continue;
    const current = declaration.current_value;
    if (!Number.isFinite(current) || (current >= limits[0] - EPS && current <= limits[1] + EPS)) continue;
    const facility = variableParameterTarget(declaration);
    if (!facility?.movable) continue;
    const range = facilityCenterRange(facility, axis);
    if (!range) continue;
    declaration.allowed_range = range;
    dirty = true;
    const label = axis === 0 ? "横坐标" : "纵坐标";
    rangeAdjustments.set(`${facility.id}:${axis}`, `${facility.id} ${label}评估范围已按当前移动区更新为 ${range[0].toFixed(2)}–${range[1].toFixed(2)} 米。`);
  }
  rangeNotice.textContent = [...rangeAdjustments.values()].join(" ");
};

const canvasWrap = document.getElementById("canvasWrap");
new ResizeObserver(([entry]) => {
  const {width, height} = entry.contentRect;
  if (width > 0 && height > 0) {
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(width * dpr) || cv.height !== Math.round(height * dpr)) resizeCanvas();
  }
}).observe(canvasWrap);

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data?.type === "crowdturb:load-scene") {
    rangeAdjustments.clear();
    scene = event.data.scene;
    sel = null;
    problems = null;
    badCells = [];
    dirty = false;
    fitView();
    refreshAll();
    runCheck();
  } else if (event.data?.type === "crowdturb:request-scene") {
    refreshVariableParameters();
    const snapshot = structuredClone(scene);
    if (dirty) {
      snapshot.meta.parent_revision = snapshot.meta.revision;
      snapshot.meta.revision += 1;
    }
    window.parent.postMessage({type: "crowdturb:scene", scene: snapshot}, location.origin);
  }
});

window.parent.postMessage({type: "crowdturb:editor-ready"}, location.origin);
