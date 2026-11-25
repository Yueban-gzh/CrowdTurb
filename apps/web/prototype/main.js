// CrowdTurb — B 模块可视化原型（BOOT-03 / REQ-03，W2 接入 A/C/D 数据）
//
// 分层：
//   adapters.js  数据适配层：把多种来源统一成内部表示（新格式只改那里）
//   main.js      渲染与交互：只消费内部表示，不关心原始格式
//
// 支持的数据集（见 DATASETS）：
//   · legacy-mock  W1 的 4×3 小样例（scene + field），用于单元测试与回归
//   · gym          A 的体育馆场景（120×80）+ 轨迹演示数据 + 分析结果
//
// 坐标映射约定（与 contracts/README.md 一致，不得改变传输中的轴含义）：
//   · 计算平面为 x/y，长度米；网格原点是左下角单元的左下角
//   · 数组采用 [iy][ix]，行从 y 小到大，列从 x 小到大；速度元素是 [vx, vy]
//   · Three.js 渲染映射：world = (x, height, -y)
// - 自检依据：有速度的单元应指向最近的出口单元（通用判定，不假设出口方位）。

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import {
  adaptLegacyScene, adaptLegacyField, adaptSceneBundle, adaptTrajectory,
  adaptAnalysis, adaptTrajectoryScene, trajectoryToField, emptyAnalysis,
  sourceLabel, statusLabel, cellCenter,
} from './adapters.js';

// ---------------------------------------------------------------------------
// 数据集清单：每个数据集声明自己的文件与格式
// ---------------------------------------------------------------------------

const DATASETS = {
  'legacy-mock': {
    label: 'W1 Mock 小样例（4×3）',
    scene: { url: '../../../examples/scene.mock.json', format: 'scene' },
    playback: { url: '../../../examples/field.mock.json', format: 'field' },
    analysis: null,
  },
  gym: {
    label: '示例体育馆（A 场景 + Mock 轨迹）',
    scene: { url: './data/gym.bundle.json', format: 'bundle' },
    playback: { url: './data/gym.trajectory.mock.json', format: 'trajectory' },
    analysis: { url: './data/gym.analysis.mock.json', format: 'analysis' },
  },
};

const DEFAULT_DATASET = 'gym';

const EPS = 1e-9;

// ---------------------------------------------------------------------------
// 数据加载与校验（契约层，与表现无关）
// ---------------------------------------------------------------------------

async function loadJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`加载 ${url} 失败：HTTP ${resp.status}`);
  }
  return resp.json();
}

// 稳健色标上限：取“有人的格子”密度的 85 分位数。
// 人群密度分布高度偏态，用最大值会撑满色标；85 分位让常见高密度区落在红段，
// 少数极端格仍显示为更亮的警示色。只看非零单元，避免大片空地稀释。
function robustDensityMax(frames, quantile = 0.85) {
  const values = [];
  for (const frame of frames) {
    for (const row of frame.density) {
      for (const d of row) if (d > EPS) values.push(d);
    }
  }
  if (values.length === 0) return 1;
  values.sort((a, b) => a - b);
  const idx = Math.min(values.length - 1, Math.floor(values.length * quantile));
  return Math.max(values[idx], 0.1);
}

// 返回 { fatal: string[], warnings: string[], info: string[] }
// scene / playback 均为 adapters.js 归一化后的内部表示
function validate(scene, playback, analysis) {
  const fatal = [];
  const warnings = [];
  const info = [];

  const { nx, ny, dx, dy, origin } = scene.grid;

  const dimsOk =
    Array.isArray(scene.walkable) && scene.walkable.length === ny &&
    scene.walkable.every((row) => Array.isArray(row) && row.length === nx);
  if (!dimsOk) {
    fatal.push(`walkable 维度应为 ${ny} 行 × ${nx} 列`);
  }
  if (dimsOk && !scene.walkable.some((row) => row.some(Boolean))) {
    fatal.push('网格没有任何可通行单元，粒子无投放位置');
  }

  if (playback.frames.length === 0) {
    fatal.push('播放数据 frames 为空');
  }
  if (playback.sceneId !== scene.sceneId || playback.sceneRevision !== scene.revision) {
    warnings.push(
      `播放数据与场景引用不一致：${playback.sceneId}@r${playback.sceneRevision} vs ${scene.sceneId}@r${scene.revision}`,
    );
  }

  // 按播放数据类型校验
  const first = playback.frames[0];
  if (first) {
    if (playback.kind === 'field') {
      for (const frame of playback.frames) {
        if (!dimsOk) break;
        if (frame.density.length !== ny || frame.density.some((r) => r.length !== nx)) {
          fatal.push(`t=${frame.timeS} 的 density 维度与网格不符`);
          break;
        }
        const velOk = frame.velocity.length === ny && frame.velocity.every((row) =>
          row.length === nx && row.every((v) => Array.isArray(v) && v.length === 2)
        );
        if (!velOk) {
          fatal.push(`t=${frame.timeS} 的 velocity 应为 ${ny}×${nx} 个 [vx, vy]`);
          break;
        }
      }
    } else if (playback.kind === 'trajectory') {
      if (!Array.isArray(first.agents)) {
        fatal.push('轨迹帧缺少 agents 数组');
      }
    }
  }

  // 契约：walkable=false 的单元密度与速度为零（仅格子数据可查）
  if (dimsOk && playback.kind === 'field' && playback.frames.length <= 200) {
    for (const frame of playback.frames) {
      let bad = 0;
      for (let iy = 0; iy < ny && bad < 3; iy++) {
        for (let ix = 0; ix < nx && bad < 3; ix++) {
          if (!scene.walkable[iy][ix]) {
            if (frame.density[iy][ix] > EPS || Math.abs(frame.velocity[iy][ix][0]) > EPS || Math.abs(frame.velocity[iy][ix][1]) > EPS) {
              warnings.push(`t=${frame.timeS} 单元 (ix=${ix}, iy=${iy}) 不可通行但密度/速度非零`);
              bad++;
            }
          }
        }
      }
    }
  }

  // 出口单元必须在边界上且可通行
  if (dimsOk) {
    for (const exit of scene.exits) {
      for (const [ix, iy] of exit.cells) {
        if (ix < 0 || ix >= nx || iy < 0 || iy >= ny || !scene.walkable[iy][ix]) {
          warnings.push(`出口 ${exit.exitId} 引用单元 (${ix}, ${iy}) 越界或不可通行`);
        }
      }
    }
  }

  const times = playback.frames.map((f) => f.timeS);
  info.push(`网格 ${nx}×${ny}，单元 ${dx}m×${dy}m，origin=(${origin[0]}, ${origin[1]})`);
  info.push(`${playback.frames.length} 帧，时间范围 ${Math.min(...times)}–${Math.max(...times)} s`);
  info.push(`播放数据：${playback.kind === 'trajectory' ? '逐人轨迹' : '格子场'}，来源 ${sourceLabel(playback.source)}`);
  if (analysis?.available) {
    info.push(`分析结果：${analysis.metrics.length} 项指标、${analysis.series.length} 条序列、${analysis.hotspots.length} 个热点`);
  }
  return { fatal, warnings, info };
}

// ---------------------------------------------------------------------------
// 渲染降采样：大网格时按 stride 聚合，保证每帧工作量与屏幕分辨率匹配
// （只影响显示；悬停读数、指标、契约校验仍用原始数据）
// ---------------------------------------------------------------------------

function chooseStride(grid) {
  const cells = grid.nx * grid.ny;
  if (cells <= 4000) return 1;      // 小网格全精度
  if (cells <= 20000) return 2;     // 例如 120×80 → 60×40
  return Math.max(2, Math.ceil(Math.sqrt(cells / 20000)));
}

// 3D 热力面的聚合尺度：轨迹数据只有几百个人，在细网格上绝大多数格是空的，
// 直接按格上色会得到大片空白 + 散布的孤立色块。热力面改用更粗的块平均，
// 形成连续的人群密度分布（2D 俯视图仍按细网格逐格显示，保留细节）。
function chooseHeatAggregation(grid) {
  const cells = grid.nx * grid.ny;
  if (cells <= 2000) return 1;
  if (cells <= 8000) return 2;
  return 3;   // 120×80 → 40×27，兼顾平滑与细节（再粗会糊成一片）
}

// 把 density/velocity 按 stride 做块平均，返回等价的粗网格
function downsample(grid, density, velocity, stride) {
  if (stride === 1) return { grid, density, velocity };
  const { nx, ny, dx, dy, origin } = grid;
  const rnx = Math.ceil(nx / stride);
  const rny = Math.ceil(ny / stride);
  const outD = Array.from({ length: rny }, () => new Array(rnx).fill(0));
  const outV = Array.from({ length: rny }, () => new Array(rnx).fill(null).map(() => [0, 0]));
  const counts = Array.from({ length: rny }, () => new Array(rnx).fill(0));
  for (let iy = 0; iy < ny; iy++) {
    const ry = Math.floor(iy / stride);
    for (let ix = 0; ix < nx; ix++) {
      const rx = Math.floor(ix / stride);
      const d = density[iy][ix];
      const v = velocity[iy][ix];
      outD[ry][rx] += d;
      outV[ry][rx][0] += v[0];
      outV[ry][rx][1] += v[1];
      counts[ry][rx] += 1;
    }
  }
  for (let ry = 0; ry < rny; ry++) {
    for (let rx = 0; rx < rnx; rx++) {
      const c = counts[ry][rx] || 1;
      outD[ry][rx] /= c;
      outV[ry][rx][0] /= c;
      outV[ry][rx][1] /= c;
    }
  }
  return {
    grid: { nx: rnx, ny: rny, dx: dx * stride, dy: dy * stride, origin },
    density: outD,
    velocity: outV,
  };
}

// ---------------------------------------------------------------------------
// 场数据插值（帧间线性插值，仅用于播放演示）
// ---------------------------------------------------------------------------

function lerpMatrix(a, b, t) {
  return a.map((row, iy) => row.map((v, ix) => v + (b[iy][ix] - v) * t));
}

function lerpVelocity(a, b, t) {
  return a.map((row, iy) => row.map((v, ix) => [
    v[0] + (b[iy][ix][0] - v[0]) * t,
    v[1] + (b[iy][ix][1] - v[1]) * t,
  ]));
}

// 返回 { density, velocity, frameLo, frameHi, alpha }
function sampleFrames(frames, timeS) {
  if (frames.length === 1) {
    return { density: frames[0].density, velocity: frames[0].velocity, frameLo: 0, frameHi: 0, alpha: 0 };
  }
  let k = 0;
  while (k < frames.length - 2 && timeS > frames[k + 1].timeS) k++;
  const lo = frames[k];
  const hi = frames[k + 1];
  const span = hi.timeS - lo.timeS;
  const alpha = span > 0 ? Math.min(1, Math.max(0, (timeS - lo.timeS) / span)) : 0;
  return {
    density: lerpMatrix(lo.density, hi.density, alpha),
    velocity: lerpVelocity(lo.velocity, hi.velocity, alpha),
    frameLo: k,
    frameHi: k + 1,
    alpha,
  };
}

// ---------------------------------------------------------------------------
// 密度 → 颜色（数据编码，3D / 2D / 图例共用同一色标）
// ---------------------------------------------------------------------------

const HEAT_LOW = [38, 68, 120];     // #264478 深蓝（低密度）
const HEAT_MID = [235, 190, 70];    // #EBBE46 黄（中密度）
const HEAT_HIGH = [205, 45, 35];    // #CD2D23 红（高密度，浓郁配色）
const HEAT_ALARM = [255, 72, 61];   // 更亮的警示色：仅超过阈值的部分向它过渡
const DENSITY_WARNING_THRESHOLD = 3.0; // 人/m²：警示阈值（暂取常用参考值，待 BOOT-06 与 D 确认）
const EXIT_COLOR = [57, 214, 160];  // #39D6A0 出口色

function densityColor(d, dMax) {
  // 用平方根映射而不是线性：人群密度分布高度偏态（多数格子低、少数极高），
  // 线性映射会让常见区间全部堆在最暗的蓝色段。平方根拉伸低值区，
  // 让 0–dMax 内的差异在视觉上可分辨；高值区仍然明显偏红。
  const t = dMax > EPS ? Math.sqrt(Math.min(1, Math.max(0, d / dMax))) : 0;
  let r, g, b;
  if (t < 0.5) {
    const u = t / 0.5;
    r = HEAT_LOW[0] + (HEAT_MID[0] - HEAT_LOW[0]) * u;
    g = HEAT_LOW[1] + (HEAT_MID[1] - HEAT_LOW[1]) * u;
    b = HEAT_LOW[2] + (HEAT_MID[2] - HEAT_LOW[2]) * u;
  } else {
    const u = (t - 0.5) / 0.5;
    r = HEAT_MID[0] + (HEAT_HIGH[0] - HEAT_MID[0]) * u;
    g = HEAT_MID[1] + (HEAT_HIGH[1] - HEAT_MID[1]) * u;
    b = HEAT_MID[2] + (HEAT_HIGH[2] - HEAT_MID[2]) * u;
  }
  // 警示过渡：超过阈值的部分才向鲜明警示色混合（数据最大值未超阈值时不触发）
  if (dMax > DENSITY_WARNING_THRESHOLD && d > DENSITY_WARNING_THRESHOLD) {
    const w = Math.min(1, (d - DENSITY_WARNING_THRESHOLD) / (dMax - DENSITY_WARNING_THRESHOLD));
    r += (HEAT_ALARM[0] - r) * w;
    g += (HEAT_ALARM[1] - g) * w;
    b += (HEAT_ALARM[2] - b) * w;
  }
  return [r / 255, g / 255, b / 255];
}

// ---------------------------------------------------------------------------
// 3D 场景（表现层 v2：半透明玻璃壳 + 平滑热力面 + 密度着色粒子）
// ---------------------------------------------------------------------------

function makeExitLabel(text) {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = 'rgba(10, 14, 20, 0.82)';
  ctx.beginPath();
  ctx.roundRect(6, 10, 244, 44, 22);
  ctx.fill();
  ctx.strokeStyle = 'rgba(57, 214, 160, 0.9)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.roundRect(6, 10, 244, 44, 22);
  ctx.stroke();
  ctx.fillStyle = '#8ff0cd';
  ctx.font = '600 22px "Microsoft YaHei", sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, 128, 33);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, toneMapped: false }));
  sprite.scale.set(1.6, 0.4, 1);
  return sprite;
}

// 粒子圆形贴图：象牙白主体 + 深色描边环，在热力图任意密度配色上都清晰可辨
// （无外部资源）
function makeCircleTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext('2d');
  const grad = ctx.createRadialGradient(32, 32, 0, 32, 32, 30);
  grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
  grad.addColorStop(0.55, 'rgba(255, 255, 255, 1)');
  grad.addColorStop(0.72, 'rgba(238, 242, 247, 1)');
  grad.addColorStop(0.8, 'rgba(24, 29, 38, 1)');   // 深色描边环
  grad.addColorStop(0.9, 'rgba(24, 29, 38, 0.55)');
  grad.addColorStop(1, 'rgba(24, 29, 38, 0)');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(32, 32, 30, 0, Math.PI * 2);
  ctx.fill();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

// 平滑热力面：整个网格共用 (nx+1)×(ny+1) 个角点顶点，
// 角点颜色取相邻可通行单元密度的均值，双线性过渡替代逐单元平涂。
// 仅表现层变化：输入 density 与单位与契约完全一致。
function buildHeatSurface(scene) {
  const { nx, ny, dx, dy, origin } = scene.grid;
  const positions = [];
  for (let j = 0; j <= ny; j++) {
    for (let i = 0; i <= nx; i++) {
      positions.push(origin[0] + i * dx, 0.02, -(origin[1] + j * dy));
    }
  }
  const indices = [];
  for (let iy = 0; iy < ny; iy++) {
    for (let ix = 0; ix < nx; ix++) {
      if (!scene.walkable[iy][ix]) continue;
      const v = (i, j) => j * (nx + 1) + i;
      const a = v(ix, iy);
      const b = v(ix + 1, iy);
      const c = v(ix + 1, iy + 1);
      const d = v(ix, iy + 1);
      indices.push(a, b, c, a, c, d);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(positions.length), 3));
  geometry.setIndex(indices);
  return geometry;
}

function updateHeatSurface(mesh, scene, density, dMax) {
  const { nx, ny } = scene.grid;
  const colorAttr = mesh.geometry.getAttribute('color');
  for (let j = 0; j <= ny; j++) {
    for (let i = 0; i <= nx; i++) {
      let sum = 0;
      let n = 0;
      for (const [ci, cj] of [[i - 1, j - 1], [i, j - 1], [i - 1, j], [i, j]]) {
        if (ci < 0 || cj < 0 || ci >= nx || cj >= ny) continue;
        if (!scene.walkable[cj][ci]) continue;
        sum += density[cj][ci];
        n++;
      }
      const c = n > 0 ? densityColor(sum / n, dMax) : [0.08, 0.09, 0.11];
      colorAttr.setXYZ(j * (nx + 1) + i, c[0], c[1], c[2]);
    }
  }
  colorAttr.needsUpdate = true;
}

function buildStaticStructure(scene, root, exitCells) {
  const { nx, ny, dx, dy, origin } = scene.grid;
  const wallH = 1.8;

  // 地面（接收阴影）
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(nx * dx + 14, ny * dy + 14),
    new THREE.MeshStandardMaterial({ color: 0x0e1218, roughness: 0.96, metalness: 0 })
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(origin[0] + (nx * dx) / 2, -0.01, -(origin[1] + (ny * dy) / 2));
  ground.receiveShadow = true;
  root.add(ground);

  // 网格线（计算网格的直观参照）。密集网格（如 120×80）下逐格线会把热力颜色冲淡，
  // 因此按步长抽稀，保证任意网格规模下线条密度大致稳定（约 40 条）。
  if (nx * ny <= 4000) {
    const stepI = Math.max(1, Math.ceil(nx / 40));
    const stepJ = Math.max(1, Math.ceil(ny / 40));
    const linePts = [];
    for (let i = 0; i <= nx; i += stepI) {
      const x = origin[0] + i * dx;
      linePts.push(x, 0.008, -origin[1], x, 0.008, -(origin[1] + ny * dy));
    }
    for (let j = 0; j <= ny; j += stepJ) {
      const y = origin[1] + j * dy;
      linePts.push(origin[0], 0.008, -y, origin[0] + nx * dx, 0.008, -y);
    }
    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute('position', new THREE.Float32BufferAttribute(linePts, 3));
    root.add(new THREE.LineSegments(lineGeom, new THREE.LineBasicMaterial({
      color: 0x243043, transparent: true, opacity: 0.35,
    })));
  }

  // 墙体：半透明玻璃壳 + 描边，任意角度都能看到馆内（出口侧留空）
  const wallMat = new THREE.MeshPhysicalMaterial({
    color: 0xaac4de,
    transparent: true,
    opacity: 0.22,
    roughness: 0.35,
    metalness: 0,
    depthWrite: false,
  });
  const wallEdgeMat = new THREE.LineBasicMaterial({ color: 0x7f95ab, transparent: true, opacity: 0.5 });
  const thin = 0.14;
  // 边界墙按连续段合并：同一侧连续的格子合成一个长条，
  // 避免密集网格（120×80）产生数千个独立 Mesh 拖慢渲染。
  const wallSegments = [];

  function mergeRuns(indices, side) {
    let start = null;
    let prev = null;
    const flush = () => {
      if (start !== null) wallSegments.push({ side, from: start, to: prev });
    };
    for (const k of indices) {
      if (start === null) { start = k; prev = k; }
      else if (k === prev + 1) { prev = k; }
      else { flush(); start = k; prev = k; }
    }
    flush();
  }

  const westRun = [];
  const eastRun = [];
  const southRun = [];
  const northRun = [];
  for (let iy = 0; iy < ny; iy++) {
    for (let ix = 0; ix < nx; ix++) {
      if (!scene.walkable[iy][ix] || exitCells.has(`${ix},${iy}`)) continue;
      if (ix === 0) westRun.push(iy);
      if (ix === nx - 1) eastRun.push(iy);
      if (iy === 0) southRun.push(ix);
      if (iy === ny - 1) northRun.push(ix);
    }
  }
  mergeRuns(westRun, 'W');
  mergeRuns(eastRun, 'E');
  mergeRuns(southRun, 'S');
  mergeRuns(northRun, 'N');

  for (const seg of wallSegments) {
    const y0 = origin[1] + seg.from * dy;
    const y1 = origin[1] + (seg.to + 1) * dy;
    const x0 = origin[0] + seg.from * dx;
    const x1 = origin[0] + (seg.to + 1) * dx;
    let cx, cz, sx, sz;
    if (seg.side === 'W') { cx = origin[0] - thin / 2; cz = -(y0 + y1) / 2; sx = thin; sz = y1 - y0; }
    else if (seg.side === 'E') { cx = origin[0] + nx * dx + thin / 2; cz = -(y0 + y1) / 2; sx = thin; sz = y1 - y0; }
    else if (seg.side === 'S') { cx = (x0 + x1) / 2; cz = -origin[1] + thin / 2; sx = x1 - x0; sz = thin; }
    else { cx = (x0 + x1) / 2; cz = -(origin[1] + ny * dy) - thin / 2; sx = x1 - x0; sz = thin; }
    const wall = new THREE.Mesh(new THREE.BoxGeometry(sx, wallH, sz), wallMat);
    wall.position.set(cx, wallH / 2, cz);
    root.add(wall);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(wall.geometry), wallEdgeMat);
    edges.position.copy(wall.position);
    root.add(edges);
  }

  // 障碍：不可通行单元。按行合并连续段，避免密集网格产生大量 Mesh。
  const obstacleMat = new THREE.MeshStandardMaterial({ color: 0x363f4b, roughness: 0.85, metalness: 0.05 });
  const obstacleEdgeMat = new THREE.LineBasicMaterial({ color: 0x55606c, transparent: true, opacity: 0.7 });
  for (let iy = 0; iy < ny; iy++) {
    let runStart = null;
    for (let ix = 0; ix <= nx; ix++) {
      const solid = ix < nx && !scene.walkable[iy][ix];
      if (solid && runStart === null) runStart = ix;
      if (!solid && runStart !== null) {
        const w = (ix - runStart) * dx;
        const cx = origin[0] + runStart * dx + w / 2;
        const cy = origin[1] + (iy + 0.5) * dy;
        const block = new THREE.Mesh(new THREE.BoxGeometry(w * 0.95, wallH, dy * 0.92), obstacleMat);
        block.position.set(cx, wallH / 2, -cy);
        block.castShadow = true;
        root.add(block);
        const edges = new THREE.LineSegments(new THREE.EdgesGeometry(block.geometry), obstacleEdgeMat);
        edges.position.copy(block.position);
        root.add(edges);
        runStart = null;
      }
    }
  }

  // 出口：地面高亮垫 + 边界门洞光片 + 标签
  const exitPadMat = new THREE.MeshBasicMaterial({ color: 0x39d6a0, toneMapped: false });
  const doorMat = new THREE.MeshBasicMaterial({
    color: 0x39d6a0,
    transparent: true,
    opacity: 0.3,
    side: THREE.DoubleSide,
    depthWrite: false,
    toneMapped: false,
  });
  const doorH = 1.25;
  for (const exit of scene.exits) {
    let sx = 0;
    let sy = 0;
    // 每格的地面高亮垫用一个大平面覆盖该出口所有格子（按包围盒），
    // 避免密集网格下每个出口产生十几个小 Mesh。
    let minIx = Infinity, maxIx = -Infinity, minIy = Infinity, maxIy = -Infinity;
    for (const [ix, iy] of exit.cells) {
      const cx = origin[0] + (ix + 0.5) * dx;
      const cy = origin[1] + (iy + 0.5) * dy;
      sx += cx;
      sy += cy;
      minIx = Math.min(minIx, ix); maxIx = Math.max(maxIx, ix);
      minIy = Math.min(minIy, iy); maxIy = Math.max(maxIy, iy);
    }
    if (exit.cells.length > 0) {
      const w = (maxIx - minIx + 1) * dx;
      const h = (maxIy - minIy + 1) * dy;
      const cx = origin[0] + minIx * dx + w / 2;
      const cy = origin[1] + minIy * dy + h / 2;
      const pad = new THREE.Mesh(new THREE.BoxGeometry(w * 0.98, 0.04, h * 0.98), exitPadMat);
      pad.position.set(cx, 0.03, -cy);
      root.add(pad);
    }

    // 贴网格边界的侧面挂半透明"门洞"光片，远看也能识别出口位置
    for (const [ix, iy] of exit.cells) {
      const x0 = origin[0] + ix * dx;
      const x1 = x0 + dx;
      const y0 = origin[1] + iy * dy;
      const y1 = y0 + dy;
      const doors = [];
      if (ix === 0) doors.push([x0 - 0.02, -(y0 + dy / 2), dy, Math.PI / 2]);
      if (ix === nx - 1) doors.push([x1 + 0.02, -(y0 + dy / 2), dy, Math.PI / 2]);
      if (iy === 0) doors.push([x0 + dx / 2, -y0 + 0.02, dx, 0]);
      if (iy === ny - 1) doors.push([x0 + dx / 2, -y1 - 0.02, dx, 0]);
      for (const [px, pz, w, rotY] of doors) {
        const panel = new THREE.Mesh(new THREE.PlaneGeometry(w, doorH), doorMat);
        panel.position.set(px, doorH / 2, pz);
        panel.rotation.y = rotY;
        root.add(panel);
      }
    }
    const label = makeExitLabel(exit.exitId);
    label.position.set(sx / exit.cells.length, 2.1, -(sy / exit.cells.length));
    root.add(label);
  }
}

// ---------------------------------------------------------------------------
// 示意粒子：跟随连续速度场（象牙白 + 深色描边，与热力图全色域保持明度对比；
// 密度信息由地面色标承担。数量与路径仅示意，不代表真实人数）
// ---------------------------------------------------------------------------

const PARTICLE_COUNT = 400;
// 固定横向微扰的最大比例（相对局部流速）：Mock 场是单元平均值，纯平流会让
// 同一流路上的所有示踪粒子汇聚成一条理想直线（如 Mock (0,1) 绕行单元的出口带），
// 加上亚单元尺度的个体差异后粒子带才接近人群形态。只影响示意粒子，不改动场数据。
const PARTICLE_JITTER_REL = 0.15;

function listWalkableCells(scene) {
  const { nx, ny } = scene.grid;
  const cells = [];
  for (let iy = 0; iy < ny; iy++) {
    for (let ix = 0; ix < nx; ix++) {
      if (scene.walkable[iy][ix]) cells.push([ix, iy]);
    }
  }
  return cells;
}

function createParticles(scene) {
  const walkableCells = listWalkableCells(scene); // validate 已保证至少一个可通行单元
  const positions = new Float32Array(PARTICLE_COUNT * 3);
  const state = []; // {x, y}（米）+ 固定微扰 (jx, jy)，随局部流速缩放后叠加

  for (let i = 0; i < PARTICLE_COUNT; i++) {
    state.push(spawnInCell(scene, walkableCells[Math.floor(Math.random() * walkableCells.length)]));
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  const material = new THREE.PointsMaterial({
    map: makeCircleTexture(),
    color: 0xeef2f7, // 象牙白：热力图全色域中最亮为黄（明度约 0.6），白色始终形成明度差
    size: 0.14,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.95,
    alphaTest: 0.05,
    depthWrite: false,
    toneMapped: false,
  });
  const points = new THREE.Points(geometry, material);
  return { points, state, positions, spawnWeighted: createWeightedSpawner(walkableCells) };
}

// ---------------------------------------------------------------------------
// 轨迹驱动的个体点（C 输出的真实个体位置，不是示意粒子）
// ---------------------------------------------------------------------------

// 轨迹回放：按时间在相邻两帧之间线性插值每个 agent 的位置。
// agent 可能在两帧之间出场（exit_events），因此以 id 求交集，缺席者不画。
function createTrajectoryPoints(playback, colorHex) {
  const maxAgents = playback.frames.reduce((m, f) => Math.max(m, f.agents.length), 0);
  const positions = new Float32Array(Math.max(maxAgents, 1) * 3);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setDrawRange(0, 0);
  const material = new THREE.PointsMaterial({
    map: makeCircleTexture(),
    color: colorHex,
    size: 0.16,
    sizeAttenuation: true,
    transparent: true,
    opacity: 0.97,
    alphaTest: 0.05,
    depthWrite: false,
    toneMapped: false,
  });
  const points = new THREE.Points(geometry, material);
  return { points, positions, maxAgents, count: 0 };
}

// 在 playback.frames 上采样 t 时刻的全部个体位置，写入 positions
function updateTrajectoryPoints(holder, playback, timeS) {
  const frames = playback.frames;
  const { positions } = holder;
  let n = 0;
  if (frames.length > 0) {
    // 定位区间：最后一帧保持到结束
    let k = 0;
    while (k < frames.length - 2 && timeS > frames[k + 1].timeS) k++;
    const lo = frames[k];
    const hi = frames[Math.min(k + 1, frames.length - 1)];
    const span = hi.timeS - lo.timeS;
    const a = span > 0 ? Math.min(1, Math.max(0, (timeS - lo.timeS) / span)) : 0;

    const hiMap = new Map(hi.agents.map((ag) => [ag.id, ag]));
    for (const agent of lo.agents) {
      const next = hiMap.get(agent.id);
      const x = next ? agent.x + (next.x - agent.x) * a : agent.x;
      const y = next ? agent.y + (next.y - agent.y) * a : agent.y;
      if (n >= holder.maxAgents) break;
      positions[n * 3] = x;
      positions[n * 3 + 1] = 0.55;
      positions[n * 3 + 2] = -y;
      n++;
    }
  }
  holder.points.geometry.setDrawRange(0, n);
  holder.points.geometry.getAttribute('position').needsUpdate = true;
  holder.count = n;
}

// 在单元内随机取点生成粒子状态；附带固定横向微扰（生成时随机、此后不变）。
// 重投放也走本函数，保证任何时刻的粒子状态都携带微扰字段——
// 若丢失，下一帧 NaN 坐标会让动画循环崩溃（真实教训）。
function spawnInCell(scene, [ix, iy]) {
  const { dx, dy, origin } = scene.grid;
  const ang = Math.random() * Math.PI * 2;
  const mag = PARTICLE_JITTER_REL * Math.random();
  return {
    x: origin[0] + (ix + 0.15 + Math.random() * 0.7) * dx,
    y: origin[1] + (iy + 0.15 + Math.random() * 0.7) * dy,
    jx: Math.cos(ang) * mag,
    jy: Math.sin(ang) * mag,
  };
}

function cellOf(scene, x, y) {
  const { nx, ny, dx, dy, origin } = scene.grid;
  const ix = Math.floor((x - origin[0]) / dx);
  const iy = Math.floor((y - origin[1]) / dy);
  // Number.isFinite 拦截 NaN：NaN 与任何比较都是 false，不能只靠边界判断
  if (!Number.isFinite(ix) || !Number.isFinite(iy) || ix < 0 || ix >= nx || iy < 0 || iy >= ny) return null;
  return [ix, iy];
}

// 按当前密度加权重投放。权重累积表只在密度数组变化时惰性重建一次
// （插值后的 density 每帧都是新数组，因此每帧至多重建一次），
// 单次重投放用二分查找定位单元，避免大网格上每次重投放都全网格扫描。
function createWeightedSpawner(cells) {
  let cacheDensity = null;
  let cacheDMax = -1;
  const cumulative = new Float64Array(cells.length);

  return function spawnWeighted(density, dMax) {
    if (density !== cacheDensity || dMax !== cacheDMax) {
      let total = 0;
      for (let i = 0; i < cells.length; i++) {
        const [ix, iy] = cells[i];
        total += Math.max(density[iy][ix], dMax * 0.05);
        cumulative[i] = total;
      }
      cacheDensity = density;
      cacheDMax = dMax;
    }
    const r = Math.random() * cumulative[cells.length - 1];
    let lo = 0;
    let hi = cells.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (r <= cumulative[mid]) hi = mid;
      else lo = mid + 1;
    }
    return cells[lo];
  };
}

function updateParticles(particles, scene, density, velocity, dMax, dt) {
  const { points, state, positions } = particles;
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    let p = state[i];
    const cell = cellOf(scene, p.x, p.y);
    if (!cell || !scene.walkable[cell[1]][cell[0]]) {
      // 走出边界 / 进入障碍：按当前密度加权重新投放
      state[i] = p = spawnInCell(scene, particles.spawnWeighted(density, dMax));
    } else {
      const [ix, iy] = cell;
      const [vx, vy] = velocity[iy][ix];
      // 微扰按局部流速缩放：场数据方向与单位不变，仅示意粒子带有个体横向差异
      const speed = Math.hypot(vx, vy);
      p.x += (vx + p.jx * speed) * dt;
      p.y += (vy + p.jy * speed) * dt;
    }
    positions[i * 3] = p.x;
    positions[i * 3 + 1] = 0.55;
    positions[i * 3 + 2] = -p.y;
  }
  points.geometry.getAttribute('position').needsUpdate = true;
}

// ---------------------------------------------------------------------------
// 2D 俯视图（数据坐标：x → 右，y → 上；canvas y 轴向下，需翻转）
// ---------------------------------------------------------------------------

function draw2d(canvas, scene, density, velocity, dMax, hover, exitCells, analysis, timeS) {
  const ctx = canvas.getContext('2d');
  const { nx, ny, dx, dy, origin } = scene.grid;
  const W = canvas.width;
  const H = canvas.height;
  const cs = Math.min((W - 70) / nx, (H - 70) / ny); // 留出坐标轴空间
  const ox = (W - cs * nx) / 2 + 25;
  const oy = (H - cs * ny) / 2 + 25; // canvas 顶部留轴标签

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0b0e14';
  ctx.fillRect(0, 0, W, H);

  // 单元（canvas 行号 = ny-1-iy，保证 y 向上）
  for (let iy = 0; iy < ny; iy++) {
    for (let ix = 0; ix < nx; ix++) {
      const px = ox + ix * cs;
      const py = oy + (ny - 1 - iy) * cs;
      if (exitCells.has(`${ix},${iy}`)) {
        ctx.save();
        ctx.shadowColor = `rgba(${EXIT_COLOR[0]}, ${EXIT_COLOR[1]}, ${EXIT_COLOR[2]}, 0.8)`;
        ctx.shadowBlur = 8;
        ctx.fillStyle = `rgb(${EXIT_COLOR[0]}, ${EXIT_COLOR[1]}, ${EXIT_COLOR[2]})`;
        ctx.fillRect(px, py, cs, cs);
        ctx.restore();
      } else if (scene.walkable[iy][ix]) {
        const [r, g, b] = densityColor(density[iy][ix], dMax);
        ctx.fillStyle = `rgb(${r * 255 | 0}, ${g * 255 | 0}, ${b * 255 | 0})`;
        ctx.fillRect(px, py, cs, cs);
      } else {
        ctx.fillStyle = '#39434f';
        ctx.fillRect(px, py, cs, cs);
      }
      ctx.strokeStyle = 'rgba(4, 6, 10, 0.4)';
      ctx.lineWidth = 1;
      ctx.strokeRect(px + 0.5, py + 0.5, cs - 1, cs - 1);
    }
  }

  // 速度箭头
  ctx.strokeStyle = 'rgba(233, 241, 250, 0.92)';
  ctx.fillStyle = 'rgba(233, 241, 250, 0.92)';
  ctx.lineWidth = 1.4;
  for (let iy = 0; iy < ny; iy++) {
    for (let ix = 0; ix < nx; ix++) {
      if (!scene.walkable[iy][ix]) continue;
      const [vx, vy] = velocity[iy][ix];
      const mag = Math.hypot(vx, vy);
      if (mag < 0.02) continue;
      const cx = ox + (ix + 0.5) * cs;
      const cy = oy + (ny - 0.5 - iy) * cs;
      const len = Math.min(cs * 0.42, mag * 60);
      const ux = (vx / mag) * len;
      const uy = -(vy / mag) * len; // canvas y 翻转
      const ex = cx + ux;
      const ey = cy + uy;
      ctx.beginPath();
      ctx.moveTo(cx - ux, cy - uy);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      // 箭头头部
      const ang = Math.atan2(uy, ux);
      ctx.beginPath();
      ctx.moveTo(ex, ey);
      ctx.lineTo(ex - 5 * Math.cos(ang - 0.4), ey - 5 * Math.sin(ang - 0.4));
      ctx.lineTo(ex - 5 * Math.cos(ang + 0.4), ey - 5 * Math.sin(ang + 0.4));
      ctx.closePath();
      ctx.fill();
    }
  }

  // 坐标轴指示（方向自检的直观参照）
  ctx.strokeStyle = '#38bdf8';
  ctx.fillStyle = '#38bdf8';
  ctx.font = '13px sans-serif';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(ox, oy + ny * cs + 14);
  ctx.lineTo(ox + 40, oy + ny * cs + 14);
  ctx.stroke();
  ctx.fillText('x', ox + 46, oy + ny * cs + 18);
  ctx.beginPath();
  ctx.moveTo(ox - 14, oy + ny * cs);
  ctx.lineTo(ox - 14, oy + ny * cs - 40);
  ctx.stroke();
  ctx.fillText('y', ox - 22, oy + ny * cs - 46);
  ctx.font = '11px sans-serif';
  ctx.fillStyle = '#66738a';
  ctx.fillText('绿色为出口，速度箭头应指向出口', ox, oy - 8);

  // 分析热点叠加：仅显示当前时刻处于持续区间内的热点（D 的 ix/iy + 时段）
  // 热点的 ix/iy 是聚合格的代表栅格，按 2 m 尺度绘制（与演示数据的聚合一致）。
  // 通用化留待 D 在样例里显式给出聚合尺度后改为读字段。
  if (analysis?.available && analysis.hotspots.length > 0 && Number.isFinite(timeS)) {
    const HOTSPOT_CELLS = 4;  // 演示数据：4 个 0.5 m 栅格 = 2 m 聚合格
    const sizePx = Math.max(cs * HOTSPOT_CELLS, 4);
    for (const h of analysis.hotspots) {
      if (timeS < h.startS || timeS > h.endS) continue;
      const px = ox + h.ix * cs;
      const py = oy + (ny - 1 - h.iy - HOTSPOT_CELLS + 1) * cs;
      ctx.fillStyle = 'rgba(255, 72, 61, 0.22)';
      ctx.fillRect(px, py, sizePx, sizePx);
      ctx.strokeStyle = 'rgba(255, 72, 61, 0.9)';
      ctx.lineWidth = Math.max(1, cs * 0.12);
      ctx.strokeRect(px, py, sizePx, sizePx);
    }
  }

  // 悬停高亮
  if (hover) {
    ctx.fillStyle = 'rgba(255, 255, 255, 0.1)';
    ctx.fillRect(ox + hover.ix * cs, oy + (ny - 1 - hover.iy) * cs, cs, cs);
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2;
    ctx.strokeRect(ox + hover.ix * cs, oy + (ny - 1 - hover.iy) * cs, cs, cs);
  }
}

function cellFromMouse(canvas, scene, evt) {
  const { nx, ny } = scene.grid;
  const rect = canvas.getBoundingClientRect();
  const W = canvas.width;
  const H = canvas.height;
  const cs = Math.min((W - 70) / nx, (H - 70) / ny);
  const ox = (W - cs * nx) / 2 + 25;
  const oy = (H - cs * ny) / 2 + 25;
  const mx = (evt.clientX - rect.left) * (W / rect.width);
  const my = (evt.clientY - rect.top) * (H / rect.height);
  const ix = Math.floor((mx - ox) / cs);
  const iy = ny - 1 - Math.floor((my - oy) / cs);
  if (ix < 0 || ix >= nx || iy < 0 || iy >= ny) return null;
  return { ix, iy };
}

// ---------------------------------------------------------------------------
// 自检（W1 必做：A/B 用已知方向验证坐标映射）
// 通用判定：有速度的单元应指向最近出口；不假设出口在哪个方向，
// 便于 W2 直接复用于 C 输出的真实场数据与任意场馆。
// ---------------------------------------------------------------------------

const SELF_CHECK_SPEED_MIN = 0.02; // m/s：低于此值视为静止单元，不参与判定
const SELF_CHECK_PASS_RATIO = 0.8; // 指向出口的单元占比达标线

function directionSelfCheck(scene, playback) {
  const { nx, ny, dx, dy, origin } = scene.grid;
  const exitCenters = [];
  for (const exit of scene.exits) {
    for (const [ix, iy] of exit.cells) {
      exitCenters.push([origin[0] + (ix + 0.5) * dx, origin[1] + (iy + 0.5) * dy]);
    }
  }
  const exitCellKeys = new Set(scene.exits.flatMap((e) => e.cells.map(([ix, iy]) => `${ix},${iy}`)));

  // 把“位置 + 速度”的采样点收集成统一形式，再统一判定
  const samples = [];
  if (playback.kind === 'trajectory') {
    for (const frame of playback.frames) {
      for (const a of frame.agents) samples.push([a.x, a.y, a.vx, a.vy]);
    }
  } else {
    for (const frame of playback.frames) {
      for (let iy = 0; iy < ny; iy++) {
        for (let ix = 0; ix < nx; ix++) {
          if (!scene.walkable[iy][ix] || exitCellKeys.has(`${ix},${iy}`)) continue;
          const [vx, vy] = frame.velocity[iy][ix];
          samples.push([origin[0] + (ix + 0.5) * dx, origin[1] + (iy + 0.5) * dy, vx, vy]);
        }
      }
    }
  }

  let aligned = 0;
  let considered = 0;
  for (const [cx, cy, vx, vy] of samples) {
    const speed = Math.hypot(vx, vy);
    if (speed < SELF_CHECK_SPEED_MIN) continue;
    let bestDot = -Infinity;
    for (const [ex, ey] of exitCenters) {
      const tx = ex - cx;
      const ty = ey - cy;
      const len = Math.hypot(tx, ty);
      if (len < EPS) continue;
      bestDot = Math.max(bestDot, (vx * tx + vy * ty) / (speed * len));
    }
    if (bestDot === -Infinity) continue; // 无可用出口方向（如场景没有出口）
    considered++;
    if (bestDot > 0) aligned++; // 速度有指向最近出口的分量
  }

  if (considered === 0) {
    return {
      pass: false,
      message: '方向自检失败：没有可参与判定的速度采样（速度全为零或场景没有出口）',
    };
  }
  const ratio = aligned / considered;
  const pass = ratio >= SELF_CHECK_PASS_RATIO;
  const unit = playback.kind === 'trajectory' ? '速度采样' : '有效单元';
  const detail = `${aligned}/${considered} 个${unit}指向最近出口（${(ratio * 100).toFixed(0)}%）`;
  return {
    pass,
    message: pass
      ? `方向自检：${detail} — 轴映射正确`
      : `方向自检失败：仅 ${detail}，请检查轴映射或出口数据`,
  };
}

// ---------------------------------------------------------------------------
// 分析指标面板（D 的输出：metrics / series / hotspots）
// 原则：B 只显示，不重算；来源与状态逐项标注（NFR-04）
// ---------------------------------------------------------------------------

function renderAnalysisPanel(scene, analysis) {
  const body = document.getElementById('analysis-body');
  const srcBadge = document.getElementById('analysis-source');
  if (!body) return;

  if (!srcBadge) return;
  if (!analysis.available) {
    srcBadge.textContent = '无分析结果';
    srcBadge.className = 'status-badge warn';
    body.innerHTML = '<p class="hint">当前数据集未提供分析结果。指标由 D 输出，B 只负责展示。</p>';
    return;
  }

  const st = statusLabel(analysis.status);
  srcBadge.textContent = sourceLabel(analysis.source);
  srcBadge.className = `status-badge ${st.kind}`;
  body.innerHTML = '';

  // --- 总体指标 ---
  if (analysis.metrics.length > 0) {
    const grid = document.createElement('div');
    grid.className = 'metric-grid';
    for (const m of analysis.metrics) {
      const card = document.createElement('div');
      card.className = 'metric-card';
      const s = statusLabel(m.status);
      card.innerHTML = `
        <div class="metric-value">${formatNum(m.value)}<span class="metric-unit">${escapeHtml(m.unit)}</span></div>
        <div class="metric-label">${escapeHtml(metricLabel(m.id))}</div>
        <div class="metric-meta"><span class="dot-inline ${s.kind}"></span>${s.text} · ${sourceLabel(m.source)}</div>`;
      if (m.definition) card.title = m.definition;
      grid.appendChild(card);
    }
    body.appendChild(grid);
  }

  // --- 时间序列 ---
  if (analysis.series.length > 0) {
    const wrap = document.createElement('div');
    wrap.className = 'series-wrap';
    for (const s of analysis.series) {
      const row = document.createElement('div');
      row.className = 'series-row';
      const title = document.createElement('div');
      title.className = 'series-title';
      title.textContent = `${metricLabel(s.id)}（${s.unit}）`;
      row.appendChild(title);
      row.appendChild(makeSparkline(s));
      wrap.appendChild(row);
    }
    body.appendChild(wrap);
  }

  // --- 热点 ---
  if (analysis.hotspots.length > 0) {
    const hs = document.createElement('div');
    hs.className = 'hotspot-summary';
    hs.textContent = `热点 ${analysis.hotspots.length} 处 · 播放到对应时段时在 2D 俯视图上高亮`;
    body.appendChild(hs);
  }

  if (analysis.threshold) {
    const th = document.createElement('p');
    th.className = 'hint';
    th.textContent = `阈值 ${analysis.threshold.value} ${analysis.threshold.unit}（${analysis.threshold.basis || '来源未注明'}）`;
    body.appendChild(th);
  }
}

function metricLabel(id) {
  const map = {
    remaining_at_end: '剩余人数',
    congestion_area_time: '累计拥堵量',
    exited_people: '已疏散人数',
    remaining_people: '剩余人数',
    congested_area: '拥堵面积',
  };
  if (map[id]) return map[id];
  if (id.startsWith('exit_cumulative:')) return `${id.split(':')[1]} 累计疏散`;
  return id;
}

function formatNum(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2).replace(/\.?0+$/, '');
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// 迷你折线图：纯 Canvas，不引第三方图表库
function makeSparkline(series) {
  const W = 280;
  const H = 46;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  canvas.className = 'sparkline';
  const ctx = canvas.getContext('2d');

  const ts = series.timeS;
  const vs = series.values;
  if (ts.length < 2) return canvas;
  const t0 = ts[0];
  const t1 = ts[ts.length - 1];
  const vMax = Math.max(...vs, 1e-9);
  const xOf = (t) => 4 + ((t - t0) / Math.max(t1 - t0, 1e-9)) * (W - 8);
  const yOf = (v) => H - 6 - (v / vMax) * (H - 12);

  ctx.strokeStyle = 'rgba(56, 189, 248, 0.22)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(4, H - 6);
  ctx.lineTo(W - 4, H - 6);
  ctx.stroke();

  ctx.strokeStyle = '#38bdf8';
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  for (let i = 0; i < ts.length; i++) {
    const x = xOf(ts[i]);
    const y = yOf(vs[i]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  ctx.fillStyle = '#66738a';
  ctx.font = '10px ui-monospace, monospace';
  ctx.textAlign = 'right';
  ctx.fillText(String(vMax), W - 4, 10);
  return canvas;
}

// ---------------------------------------------------------------------------
// 状态面板
// ---------------------------------------------------------------------------

const statusEl = document.getElementById('status');
const statusToggle = document.getElementById('status-toggle');
if (statusToggle) {
  statusToggle.addEventListener('click', () => {
    const hidden = statusEl.classList.toggle('hidden');
    statusToggle.textContent = hidden ? '详情' : '收起';
    statusToggle.setAttribute('aria-expanded', String(!hidden));
  });
}

function addStatusItem(kind, text) {
  const li = document.createElement('li');
  const dot = document.createElement('span');
  dot.className = `dot ${kind}`;
  dot.textContent = kind === 'ok' ? '✓' : kind === 'fail' ? '✗' : '·';
  const span = document.createElement('span');
  span.textContent = text;
  li.appendChild(dot);
  li.appendChild(span);
  statusEl.appendChild(li);
}

// ---------------------------------------------------------------------------
// 主流程
// ---------------------------------------------------------------------------

main().catch((err) => {
  statusEl.classList.remove('hidden'); // 失败详情自动展开
  statusEl.innerHTML = '';
  const li = document.createElement('li');
  li.className = 'pending';
  li.textContent = `加载失败：${err.message}`;
  statusEl.appendChild(li);
  const hint = document.createElement('li');
  hint.className = 'pending';
  hint.textContent = '提示：请通过 HTTP 服务器打开本页面（在仓库根目录运行 python -m http.server 8000），不能直接双击 index.html（fetch 受 file:// 限制）。';
  statusEl.appendChild(hint);
  console.error(err);
});

async function main() {
  const params = new URLSearchParams(location.search);
  const datasetId = DATASETS[params.get('dataset')] ? params.get('dataset') : DEFAULT_DATASET;
  const dataset = DATASETS[datasetId];

  // 数据集切换控件：同步当前选中并支持跳转
  const select = document.getElementById('dataset-select');
  if (select) {
    select.value = datasetId;
    select.addEventListener('change', () => {
      const url = new URL(location.href);
      url.searchParams.set('dataset', select.value);
      location.href = url.toString();
    });
  }

  const [rawScene, rawPlayback, rawAnalysis] = await Promise.all([
    loadJson(dataset.scene.url),
    loadJson(dataset.playback.url),
    dataset.analysis ? loadJson(dataset.analysis.url).catch(() => null) : Promise.resolve(null),
  ]);

  // --- 适配层：原始数据 → 内部表示 ---
  const scene = dataset.scene.format === 'bundle'
    ? adaptSceneBundle(rawScene)
    : adaptLegacyScene(rawScene);
  const playback = dataset.playback.format === 'trajectory'
    ? adaptTrajectory(rawPlayback)
    : adaptLegacyField(rawPlayback);
  const analysis = rawAnalysis ? adaptAnalysis(rawAnalysis) : emptyAnalysis();

  // 热力图数据：轨迹先按格子聚合（仅展示用；指标计算归 D）
  const fieldForHeat = playback.kind === 'trajectory'
    ? trajectoryToField(playback, scene)
    : playback;

  const { fatal, warnings, info } = validate(scene, playback, analysis);
  statusEl.innerHTML = '';
  for (const w of warnings) addStatusItem('fail', `警告：${w}`);
  for (const i of info) addStatusItem('info', i);
  if (fatal.length > 0) {
    for (const f of fatal) addStatusItem('fail', f);
    throw new Error('契约校验未通过，请查看状态面板');
  }
  addStatusItem('ok', `数据集「${dataset.label}」加载完成`);

  const check = directionSelfCheck(scene, playback);
  addStatusItem(check.pass ? 'ok' : 'fail', check.message);

  // 折叠面板的摘要徽章：不展开也能看到自检结论
  const summary = document.getElementById('status-summary');
  if (summary) {
    if (fatal.length > 0) { summary.textContent = '✗ 未通过'; summary.className = 'status-badge fail'; }
    else if (warnings.length > 0) { summary.textContent = '⚠ 有警告'; summary.className = 'status-badge warn'; }
    else { summary.textContent = '✓ 通过'; summary.className = 'status-badge ok'; }
  }

  // 色标上限：基于 3D 热力面实际使用的聚合尺度计算分位数。
  // 原始细网格与聚合网格的密度量级不同（聚合后更平滑），必须与显示数据同尺度。
  const aggForScale = chooseHeatAggregation(scene.grid);
  const scaleSource = aggForScale === 1
    ? fieldForHeat
    : {
      ...fieldForHeat,
      frames: fieldForHeat.frames.map((f) => {
        const ds = downsample(scene.grid, f.density, f.velocity, aggForScale);
        return { timeS: f.timeS, density: ds.density, velocity: ds.velocity };
      }),
    };
  const dMax = robustDensityMax(scaleSource.frames);
  const legendDmax = document.getElementById('legend-dmax');
  if (legendDmax) legendDmax.textContent = dMax.toFixed(2);
  // 警示阈值刻度：优先用 D 分析结果里的阈值，否则用页面默认值
  const thresholdValue = analysis.threshold?.value ?? DENSITY_WARNING_THRESHOLD;
  const notch = document.getElementById('legend-threshold');
  if (notch) {
    if (dMax > thresholdValue) {
      notch.hidden = false;
      notch.style.left = `${((thresholdValue / dMax) * 100).toFixed(1)}%`;
      notch.title = `警示阈值 ${thresholdValue} ${analysis.threshold?.unit || '人/m²'}`;
    } else {
      notch.hidden = true;
    }
  }

  renderAnalysisPanel(scene, analysis);
  init3d(scene, playback, fieldForHeat, dMax, analysis);
}

// ---------------------------------------------------------------------------
// 3D 初始化与动画循环
// ---------------------------------------------------------------------------

function init3d(scene, playback, fieldForHeat, dMax, analysis) {
  const container = document.getElementById('viewport-3d');
  const field = fieldForHeat;          // 热力图与 2D 速度箭头用（轨迹已聚合）
  const { nx, ny, dx, dy, origin } = scene.grid;
  const worldW = nx * dx;
  const worldD = ny * dy;
  const centerX = origin[0] + worldW / 2;
  const centerZ = -(origin[1] + worldD / 2);
  const maxDim = Math.max(worldW, worldD);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.appendChild(renderer.domElement);

  const sceneRoot = new THREE.Scene();
  sceneRoot.background = null; // 透明背景，交给 CSS 渐变

  // 相机预设：透视 / 俯视，过渡用缓动补间
  const VIEWS = {
    persp: {
      pos: [centerX + worldW * 0.5, maxDim * 1.05 + 2, centerZ + worldD * 0.95],
      target: [centerX, 0, centerZ],
    },
    top: {
      pos: [centerX, maxDim * 1.5 + 4, centerZ + maxDim * 0.02],
      target: [centerX, 0, centerZ],
    },
  };

  const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 300);
  camera.position.set(...VIEWS.persp.pos);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(...VIEWS.persp.target);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.495;
  controls.minDistance = 1.5;
  controls.maxDistance = 80;
  controls.update();

  // 灯光：半球环境光 + 投影主光 + 补光
  sceneRoot.add(new THREE.HemisphereLight(0x93b4d6, 0x11151b, 0.85));
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.35);
  keyLight.position.set(centerX + worldW * 0.4, maxDim * 1.2 + 6, centerZ + worldD * 0.5);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(1024, 1024);
  const shadowExtent = maxDim * 1.4;
  keyLight.shadow.camera.left = -shadowExtent;
  keyLight.shadow.camera.right = shadowExtent;
  keyLight.shadow.camera.top = shadowExtent;
  keyLight.shadow.camera.bottom = -shadowExtent;
  keyLight.shadow.camera.near = 1;
  keyLight.shadow.camera.far = maxDim * 4 + 30;
  keyLight.shadow.bias = -0.0005;
  sceneRoot.add(keyLight);
  const fillLight = new THREE.DirectionalLight(0x88aacc, 0.3);
  fillLight.position.set(centerX - worldW * 0.5, maxDim * 0.8 + 4, centerZ - worldD * 0.6);
  sceneRoot.add(fillLight);

  // 出口单元集合：3D 高亮与 2D 俯视图共用
  const exitCells = new Set(scene.exits.flatMap((e) => e.cells.map(([ix, iy]) => `${ix},${iy}`)));

  buildStaticStructure(scene, sceneRoot, exitCells);

  // 2D 俯视图用细网格（保留单格细节），3D 热力面用更粗的聚合（形成连续人群分布）。
  // 两者都只影响显示精度，数据本身不变。
  const stride2d = chooseStride(scene.grid);            // 2D 视图
  const agg3d = chooseHeatAggregation(scene.grid);      // 3D 热力面

  const makeAggregated = (agg) => (agg === 1 ? scene : {
    ...scene,
    grid: {
      nx: Math.ceil(scene.grid.nx / agg),
      ny: Math.ceil(scene.grid.ny / agg),
      dx: scene.grid.dx * agg,
      dy: scene.grid.dy * agg,
      origin: scene.grid.origin,
    },
    walkable: (() => {
      const { nx, ny } = scene.grid;
      const rnx = Math.ceil(nx / agg);
      const rny = Math.ceil(ny / agg);
      const out = Array.from({ length: rny }, () => new Array(rnx).fill(false));
      for (let iy = 0; iy < ny; iy++) {
        for (let ix = 0; ix < nx; ix++) {
          if (scene.walkable[iy][ix]) out[Math.floor(iy / agg)][Math.floor(ix / agg)] = true;
        }
      }
      return out;
    })(),
  });

  const scene2d = makeAggregated(stride2d);
  const sceneHeat = makeAggregated(agg3d);

  const heat = new THREE.Mesh(
    buildHeatSurface(sceneHeat),
    new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false })
  );
  sceneRoot.add(heat);

  // 大网格：预先把每帧降采样并缓存，播放时只在粗网格上插值（性能关键）
  const cacheFor = (agg) => (agg === 1 ? field : {
    ...field,
    frames: field.frames.map((f) => {
      const ds = downsample(scene.grid, f.density, f.velocity, agg);
      return { timeS: f.timeS, density: ds.density, velocity: ds.velocity };
    }),
  });
  const field2d = cacheFor(stride2d);
  const fieldHeat = cacheFor(agg3d);

  const particles = playback.kind === 'trajectory'
    ? null                                    // 轨迹模式：不用示意粒子
    : createParticles(scene);
  if (particles) sceneRoot.add(particles.points);

  // 轨迹模式：真实个体点（C 输出的逐人位置，不是示意粒子）
  const trajectoryPoints = playback.kind === 'trajectory'
    ? createTrajectoryPoints(playback, 0x8ff0cd)
    : null;
  if (trajectoryPoints) sceneRoot.add(trajectoryPoints.points);

  // --- 视角切换 ---
  let camTween = null; // { fromPos, toPos, fromTarget, toTarget, start, dur }
  function setView(name) {
    const view = VIEWS[name];
    camTween = {
      fromPos: camera.position.clone(),
      toPos: new THREE.Vector3(...view.pos),
      fromTarget: controls.target.clone(),
      toTarget: new THREE.Vector3(...view.target),
      start: performance.now(),
      dur: 450,
    };
  }
  const btnPersp = document.getElementById('btn-view-persp');
  const btnTop = document.getElementById('btn-view-top');
  const btnReset = document.getElementById('btn-view-reset');
  function markActive(name) {
    btnPersp.classList.toggle('active', name === 'persp');
    btnTop.classList.toggle('active', name === 'top');
  }
  btnPersp.addEventListener('click', () => {
    setView('persp');
    markActive('persp');
  });
  btnTop.addEventListener('click', () => {
    setView('top');
    markActive('top');
  });
  btnReset.addEventListener('click', () => {
    setView('persp');
    markActive('persp');
  });

  // --- 时间轴状态 ---
  const times = field.frames.map((f) => f.timeS);
  const tMax = Math.max(...times);
  const tMin = Math.min(...times);
  const span = Math.max(tMax - tMin, EPS);

  // 底部播放横栏的时间边界
  const timeMinEl = document.getElementById('time-min');
  const timeMaxEl = document.getElementById('time-max');
  if (timeMinEl) timeMinEl.textContent = `${tMin.toFixed(1)} s`;
  if (timeMaxEl) timeMaxEl.textContent = `${tMax.toFixed(1)} s`;

  let playing = true;
  let speed = 1;
  let tau = tMin; // 当前时间（秒）
  let lastClock = performance.now();

  const btnPlay = document.getElementById('btn-play');
  const slider = document.getElementById('timeline');
  const speedSel = document.getElementById('speed');
  const timeLabel = document.getElementById('time-label');
  const canvas2d = document.getElementById('view-2d');
  const cellInfo = document.getElementById('cell-info');
  let hover = null;

  btnPlay.addEventListener('click', () => {
    playing = !playing;
    btnPlay.textContent = playing ? '暂停' : '播放';
  });
  slider.addEventListener('input', () => {
    tau = tMin + (Number(slider.value) / 1000) * span;
    playing = false;
    btnPlay.textContent = '播放';
    lastClock = performance.now();
  });
  speedSel.addEventListener('change', () => {
    speed = Number(speedSel.value);
  });
  canvas2d.addEventListener('mousemove', (evt) => {
    hover = cellFromMouse(canvas2d, scene, evt);
  });
  canvas2d.addEventListener('mouseleave', () => {
    hover = null;
  });

  window.addEventListener('resize', () => {
    const w = container.clientWidth;
    const h = container.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  });

  function tick(now) {
    const dt = Math.min((now - lastClock) / 1000, 0.1);
    lastClock = now;

    if (playing) {
      tau += dt * speed;
      if (tau > tMax) tau = tMin; // 循环播放
      slider.value = String(Math.round(((tau - tMin) / span) * 1000));
    }

    // 渲染用的帧数据已是粗网格：2D 与 3D 各用自己的聚合尺度
    const sampleHeat = sampleFrames(fieldHeat.frames, tau);
    const sample2d = stride2d === agg3d ? sampleHeat : sampleFrames(field2d.frames, tau);

    updateHeatSurface(heat, sceneHeat, sampleHeat.density, dMax);
    if (trajectoryPoints) {
      // 轨迹模式：位置直接来自 C 的轨迹（只受时间轴驱动，不自行积分）
      updateTrajectoryPoints(trajectoryPoints, playback, tau);
    } else if (playing) {
      // 暂停即全场冻结：粒子不推进、不重投放；
      // 热力图与 2D 视图仍每帧刷新，保证暂停时拖动时间轴 / 悬停查看有效
      updateParticles(particles, scene, sample2d.density, sample2d.velocity, dMax, dt * speed);
    }
    draw2d(canvas2d, scene2d,
      sample2d.density, sample2d.velocity, dMax, hover, exitCells, analysis, tau);

    // 相机补间（透视 / 俯视切换）
    if (camTween) {
      const k = Math.min(1, (now - camTween.start) / camTween.dur);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      camera.position.lerpVectors(camTween.fromPos, camTween.toPos, e);
      controls.target.lerpVectors(camTween.fromTarget, camTween.toTarget, e);
      if (k >= 1) camTween = null;
    }
    controls.update();

    timeLabel.textContent = `t = ${tau.toFixed(2)} s（帧 ${sample2d.frameLo}→${sample2d.frameHi}，插值 α=${sample2d.alpha.toFixed(2)}）`;
    if (hover) {
      // 悬停读数对应 2D 视图实际显示的数据（细网格），不是 3D 的聚合结果
      const [vx, vy] = sample2d.velocity[hover.iy][hover.ix];
      cellInfo.textContent = `单元 (ix=${hover.ix}, iy=${hover.iy})：density=${sample2d.density[hover.iy][hover.ix].toFixed(2)} 人/m²，v=[${vx.toFixed(2)}, ${vy.toFixed(2)}] m/s${scene2d.walkable[hover.iy][hover.ix] ? '' : '（不可通行）'}`;
    } else {
      cellInfo.textContent = '将鼠标移到俯视图上查看单元数据。';
    }

    renderer.render(sceneRoot, camera);
    requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}
