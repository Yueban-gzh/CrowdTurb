"use strict";

window.addEventListener("message", (event) => {
  if (event.origin !== location.origin || event.source !== window.parent) return;
  if (event.data?.type === "crowdturb:load-scene") {
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
