// CrowdTurb — B 模块数据适配层
//
// 职责：把仓库里多种来源的数据统一成渲染层可用的内部表示。
// 渲染层只认 InternalScene / InternalPlayback / InternalAnalysis，
// 新数据格式只改本文件的适配函数，不动渲染与交互代码。
//
// 支持的数据形态：
//   1. W1 旧 Mock：scene.mock.json + field.mock.json（格子密度/速度）
//   2. A 导出：<name>.bundle.json（场景体素化后的 geometry + initial_density）
//   3. C 输出：trajectory（contracts/trajectory.schema.json，逐人轨迹）
//   4. D 输出：derived-field（格子密度）+ analysis（指标/序列/热点）
//
// 坐标约定（所有来源一致，来自 contracts/README.md）：
//   x 右、y 上，米；数组 [iy][ix]，行 y 从小到大；
//   网格原点是左下角单元左下角；单元中心 = origin + ((ix+0.5)*dx, (iy+0.5)*dy)
//
// InternalScene:
//   { sceneId, revision, grid:{nx,ny,dx,dy,origin},
//     walkable[iy][ix]:bool, obstacle[iy][ix]:bool, initialDensity[iy][ix]:number,
//     exits:[{exitId, cells:[[ix,iy]], widthM}], kind, provenance }
//
// InternalPlayback（二选一）：
//   { kind:'field', frames:[{timeS, density[iy][ix], velocity[iy][ix]}], source }
//   { kind:'trajectory', frames:[{timeS, agents:[{id,x,y,vx,vy}]}], source, exitEvents }
//
// InternalAnalysis:
//   { available, status, source, timeRange:[t0,t1], threshold:{value,unit},
//     metrics:[{id,value,unit,definition,status}],
//     series:[{id,timeS[],values[],unit,status,interpolation}],
//     hotspots:[{ix,iy,startS,endS,durationS,peakDensity,unit,status}] }

export const SOURCE_LABELS = {
  mock: 'Mock',
  stddn: 'STDDN 模型',
  pde: 'PDE',
  fno: 'FNO',
  'scene-bundle': 'A 场景',
};

// ---------------------------------------------------------------------------
// 通用工具
// ---------------------------------------------------------------------------

function gridOf(src) {
  const g = src.grid;
  if (!g) throw new Error('数据缺少 grid 字段');
  return {
    nx: g.nx, ny: g.ny, dx: g.dx, dy: g.dy,
    origin: g.origin || [0, 0],
  };
}

function cellCenter(grid, ix, iy) {
  return [
    grid.origin[0] + (ix + 0.5) * grid.dx,
    grid.origin[1] + (iy + 0.5) * grid.dy,
  ];
}

// 从 walkable 掩码生成障碍矩阵（不可通行即障碍；边界墙不在 wiggle 之外单独建，
// 由渲染层按“可通行单元贴边”的规则补墙体）
function obstacleFromWalkable(walkable) {
  return walkable.map((row) => row.map((v) => !v));
}

function emptyMatrix(ny, nx, fill) {
  return Array.from({ length: ny }, () => new Array(nx).fill(fill));
}

// ---------------------------------------------------------------------------
// 场景适配
// ---------------------------------------------------------------------------

// W1 旧格式：contracts/scene.schema.json
export function adaptLegacyScene(scene) {
  const grid = gridOf(scene);
  const walkable = scene.walkable.map((row) => row.map(Boolean));
  return {
    sceneId: scene.scene_id,
    revision: scene.revision,
    grid,
    walkable,
    obstacle: obstacleFromWalkable(walkable),
    initialDensity: scene.initial_density
      ? scene.initial_density.map((row) => row.map(Number))
      : emptyMatrix(grid.ny, grid.nx, 0),
    exits: (scene.exits || []).map((e) => ({
      exitId: e.exit_id,
      cells: e.cells.map(([ix, iy]) => [ix, iy]),
      widthM: e.width_m ?? null,
    })),
    kind: 'legacy-scene',
    provenance: { source: 'mock', schemaVersion: scene.schema_version },
  };
}

// A 导出：tools/export_scene_bundle.py 产出的 scene-bundle
export function adaptSceneBundle(bundle) {
  const grid = gridOf(bundle);
  const walkable = bundle.walkable.map((row) => row.map(Boolean));
  return {
    sceneId: bundle.scene_id,
    revision: bundle.scene_revision,
    grid,
    walkable,
    obstacle: bundle.obstacle_mask
      ? bundle.obstacle_mask.map((row) => row.map(Boolean))
      : obstacleFromWalkable(walkable),
    initialDensity: bundle.initial_density
      ? bundle.initial_density.map((row) => row.map(Number))
      : emptyMatrix(grid.ny, grid.nx, 0),
    exits: (bundle.exits || []).map((e) => ({
      exitId: e.exit_id,
      cells: e.cells.map(([ix, iy]) => [ix, iy]),
      widthM: e.width_m ?? null,
    })),
    kind: 'scene-bundle',
    provenance: {
      source: 'scene-bundle',
      schemaVersion: bundle.schema_version,
      maskHash: bundle.provenance?.mask_hash,
    },
  };
}

// C 输出：trajectory 自带 grid/walkable/exit_ids，可作为场景来源
// （出口单元由 exit_ids 与网格边界推得；见 deriveExitsFromTrajectory）
export function adaptTrajectoryScene(traj) {
  const grid = gridOf(traj);
  const walkable = traj.walkable.map((row) => row.map(Boolean));
  return {
    sceneId: traj.scene_id,
    revision: traj.scene_revision,
    grid,
    walkable,
    obstacle: obstacleFromWalkable(walkable),
    initialDensity: emptyMatrix(grid.ny, grid.nx, 0),
    exits: deriveExitsFromTrajectory(traj, grid, walkable),
    kind: 'trajectory-scene',
    provenance: {
      source: traj.source,
      schemaVersion: traj.schema_version,
      resultId: traj.result_id,
      inputHash: traj.input_hash,
    },
  };
}

// trajectory 只给 exit_ids，不给出口单元。用可行走掩码的边界单元推断：
// 每个出口 id 依次分配到边界上连续的可通行单元段（数量按 id 排序稳定）。
function deriveExitsFromTrajectory(traj, grid, walkable) {
  const { nx, ny } = grid;
  const boundary = [];
  for (let iy = 0; iy < ny; iy++) {
    for (let ix = 0; ix < nx; ix++) {
      if (!walkable[iy][ix]) continue;
      const isEdge = ix === 0 || iy === 0 || ix === nx - 1 || iy === ny - 1;
      if (isEdge) boundary.push([ix, iy]);
    }
  }
  const ids = traj.exit_ids || [];
  if (ids.length === 0) return [];
  // 按角度把边界单元分给各出口：以场馆中心为原点，按方位角排序后均分。
  // 这是无出口位置信息时的保守近似，真实出口位置应由 A 的 bundle 提供。
  const cx = (nx - 1) / 2;
  const cy = (ny - 1) / 2;
  boundary.sort(([ax, ay], [bx, by]) =>
    Math.atan2(ay - cy, ax - cx) - Math.atan2(by - cy, bx - cx));
  const per = boundary.length / ids.length;
  return ids.map((id, k) => ({
    exitId: id,
    cells: boundary.slice(Math.floor(k * per), Math.floor((k + 1) * per)),
    widthM: null,
    derived: true,
  }));
}

// ---------------------------------------------------------------------------
// 播放数据适配
// ---------------------------------------------------------------------------

// W1 旧格式：contracts/field-result.schema.json
export function adaptLegacyField(field) {
  return {
    kind: 'field',
    source: field.source,
    sceneId: field.scene_id,
    sceneRevision: field.scene_revision,
    frames: field.frames.map((f) => ({
      timeS: f.time_s,
      density: f.density.map((row) => row.map(Number)),
      velocity: f.velocity.map((row) => row.map((v) => [Number(v[0]), Number(v[1])])),
    })),
  };
}

// D 输出：derived-field（格子密度/速度），结构接近旧 field-result
export function adaptDerivedField(df, sourceLabel) {
  return {
    kind: 'field',
    source: sourceLabel || df.source,
    sceneId: df.scene_id,
    sceneRevision: df.scene_revision,
    frames: df.frames.map((f) => ({
      timeS: f.time_s,
      density: f.density.map((row) => row.map(Number)),
      velocity: (f.velocity || f.velocity_mps || []).map(
        (row) => row.map((v) => [Number(v[0]), Number(v[1])])),
    })),
  };
}

// C 输出：trajectory（逐人轨迹）
export function adaptTrajectory(traj) {
  return {
    kind: 'trajectory',
    source: traj.source,
    sceneId: traj.scene_id,
    sceneRevision: traj.scene_revision,
    exitEvents: (traj.exit_events || []).map((e) => ({
      agentId: e.agent_id,
      exitId: e.exit_id,
      timeS: e.time_s,
    })),
    frames: traj.frames.map((f) => ({
      timeS: f.time_s,
      agents: (f.agents || []).map((a) => ({
        id: a.agent_id,
        x: a.position_m[0],
        y: a.position_m[1],
        vx: (a.velocity_mps || [0, 0])[0],
        vy: (a.velocity_mps || [0, 0])[1],
      })),
    })),
  };
}

// 轨迹 → 格子密度/速度（仅用于热力图显示；指标计算仍归 D）
export function trajectoryToField(playback, scene) {
  const { nx, ny, dx, dy, origin } = scene.grid;
  const cellArea = dx * dy;
  return {
    kind: 'field',
    source: playback.source,
    sceneId: playback.sceneId,
    sceneRevision: playback.sceneRevision,
    frames: playback.frames.map((f) => {
      const count = emptyMatrix(ny, nx, 0);
      const sumVx = emptyMatrix(ny, nx, 0);
      const sumVy = emptyMatrix(ny, nx, 0);
      for (const a of f.agents) {
        const ix = Math.floor((a.x - origin[0]) / dx);
        const iy = Math.floor((a.y - origin[1]) / dy);
        if (ix < 0 || ix >= nx || iy < 0 || iy >= ny) continue;
        if (!scene.walkable[iy][ix]) continue;
        count[iy][ix] += 1;
        sumVx[iy][ix] += a.vx;
        sumVy[iy][ix] += a.vy;
      }
      const density = count.map((row) => row.map((c) => c / cellArea));
      const velocity = count.map((row, iy) => row.map((c, ix) => (
        c > 0 ? [sumVx[iy][ix] / c, sumVy[iy][ix] / c] : [0, 0]
      )));
      return { timeS: f.timeS, density, velocity };
    }),
  };
}

// ---------------------------------------------------------------------------
// 分析结果适配
// ---------------------------------------------------------------------------

export function emptyAnalysis() {
  return {
    available: false,
    status: 'absent',
    source: null,
    timeRange: null,
    threshold: null,
    metrics: [],
    series: [],
    hotspots: [],
  };
}

export function adaptAnalysis(a) {
  return {
    available: true,
    status: a.status || 'normal',
    source: a.source,
    // 热点 ix/iy 所在的网格尺寸：2D 视图若降采样，需按此换算
    gridNx: a.grid?.nx ?? null,
    gridNy: a.grid?.ny ?? null,
    timeRange: a.time_range_s || null,
    threshold: a.threshold
      ? { value: a.threshold.value, unit: a.threshold.unit, basis: a.threshold.basis }
      : null,
    metrics: Object.entries(a.metrics || {}).map(([id, m]) => ({
      id,
      value: m.value,
      unit: m.unit,
      definition: m.definition,
      source: m.source || a.source,
      status: m.status || a.status || 'normal',
    })),
    series: (a.series || []).map((s) => ({
      id: s.metric_id,
      timeS: s.time_s.slice(),
      values: s.values.slice(),
      unit: s.unit,
      source: s.source || a.source,
      status: s.status || 'normal',
      interpolation: s.interpolation || 'previous_sample',
    })),
    hotspots: (a.hotspots || []).map((h) => ({
      ix: h.ix,
      iy: h.iy,
      startS: h.start_time_s,
      endS: h.end_time_s,
      durationS: h.observed_duration_s,
      peakDensity: h.peak_density,
      unit: h.unit,
      source: h.source || a.source,
      status: h.status || 'normal',
    })),
  };
}

// ---------------------------------------------------------------------------
// 来源与状态标签（NFR-04：Mock / 真实结果 必须可区分）
// ---------------------------------------------------------------------------

export function sourceLabel(source) {
  return SOURCE_LABELS[source] || source || '未知来源';
}

export function statusLabel(status) {
  const map = {
    normal: { text: '正常', kind: 'ok' },
    pending: { text: '待校验', kind: 'warn' },
    to_evaluate: { text: '待评价', kind: 'warn' },
    failed: { text: '失败', kind: 'fail' },
    illegal: { text: '非法', kind: 'fail' },
    absent: { text: '无分析结果', kind: 'warn' },
  };
  return map[status] || { text: status, kind: 'warn' };
}

export { cellCenter };