# 统一 Web 入口（E）

状态：W1 实现待评审。页面由原生 HTML / CSS / JavaScript 提供，不需要前端构建，随系统 API 同源启动。

## E 已实现

- 项目切换与新建、场景 JSON 查看与新版本保存。
- Mock / PDE / FNO 来源选择，任务提交、轮询与失败原因展示。
- 连续场结果元数据、原始 JSON 和结果交接说明。
- 来源诚实提示：Mock 不代表 PDE / FNO，也不构成真实疏散结论。

## A-D 接入点

| 接入点 | 负责人 | 当前内容 | 建议接入方式 |
| --- | --- | --- | --- |
| 场景编辑器区域 | A | JSON 文本编辑 | 替换编辑控件，仍提交 `scene` 契约对象 |
| 3D / 俯视 `#view-slot` | B | 占位说明 | 读取 `/api/projects/{id}` 与 `/api/results/{id}` |
| 拥堵诊断 `#analysis-slot` | D | 占位说明 | 消费连续场，显示指标与阈值来源 |
| 候选优化 `#optimize-slot` | D/E | 占位说明 | 通过 E 的任务接口运行与保存 |
| 同条件对比 `#compare-slot` | B/D | 占位说明 | 组合两个场景版本及各自结果 |

模块接入时不得改变计算轴、单位或结果来源语义；跨模块改动先更新 [契约](../../contracts/README.md) 并邀请生产方、消费方评审。
# Web 模块（A：场景配置 / B：3D 与回放 / E：统一入口）

B 负责 3D 场馆、2D 俯视、热力、示意粒子、时间轴和比较展示；A 负责场景配置；E 协调统一入口。当前 A 的编辑功能与 E 的入口尚未实现。

## B 的 W1 + W2 接入（BOOT-03，关联 REQ-03）

状态：W1 原型 + A/C/D 数据接入，待评审。技术栈尚未在 BOOT-08 锁定，因此本原型使用原生 ES Module + Three.js 0.169（本地 vendored 于 `prototype/lib/`，无构建步骤、无框架、无锁文件）；BOOT-08 确认 React + TypeScript 后再迁移。

### 架构：渲染层与数据格式解耦

```text
adapters.js   数据适配层：原始格式 → 内部表示（新格式只改这个文件）
main.js       渲染与交互：只消费内部表示，不关心原始格式
```

已支持的输入形态：

| 来源 | 格式 | 用途 |
|---|---|---|
| W1 | `examples/scene.mock.json` + `field.mock.json` | 小样例回归（源数据网格 `[iy][ix]`） |
| A | `data/*.bundle.json`（由 A 场景栅格化导出） | 120×80 / 144×96 真实场馆几何 |
| C | `data/*.trajectory.mock.json`（`contracts/trajectory.schema.json`） | 逐人轨迹回放 |
| D | `data/*.analysis.mock.json`（`contracts/analysis.schema.json`） | 指标 / 时间序列 / 热点 |

**B 不读取 `.onnx` 模型文件**：推理由 C 在 Python 侧完成，B 只消费其输出的轨迹数据。

### 已实现

- 数据集切换（顶栏下拉）：`示例体育馆`（A 场景 + Mock 轨迹）与 `W1 Mock 小样例`（4×3）
- 契约一致性校验：网格维度、场景/播放数据引用一致、可通行单元约束
- 布局：3D 主视图占左侧主要区域，2D 俯视图、图例、分析指标与契约自检集中于右侧栏，播放控制置于底部横栏
- 3D 场馆：半透明玻璃壳墙体、障碍实体投影、出口地面高亮 + 边界门洞光片、轨道相机与"透视 / 俯视 / 重置"视角预设
- 密度热力图：3D 为角点插值平滑热力面，2D 为逐格网格；色标为稳健分位数上限 + 平方根映射（人群密度高度偏态，线性映射会让常见区间堆在最暗色段）
- **轨迹回放**：逐人位置来自 C 的轨迹数据，帧间线性插值；agent 出场后不再绘制。3D 以人形展示（旋转成型的胶囊身体 + 圆球头，约 1.3 m，沿地面滑动、无走路动画，参考演示视频样式），2D 以圆点标注个体
- **分析指标面板**：读 D 的 `analysis`，显示总体指标（带单位 + 来源 + 状态徽章）、时间序列迷你折线图（播放时按 previous_sample 口径标注当前值）、热点统计；热点在 2D 俯视图上按播放时刻高亮
- 示意粒子（仅格子场数据）：象牙白 + 深色描边 + 固定个体微扰
- 时间轴：播放 / 暂停 / 拖动 / 倍速 / 循环，暂停即全场冻结
- 坐标方向自检：对格子场用格子速度、对轨迹用 agent 速度，统一判定是否指向最近出口

### 性能处理（大网格）

A 的场景为 120×80 / 144×96，逐格绘制会导致数千 draw call 与每帧上万次矩阵运算，已做：

- 墙体、障碍、出口按连续段合并为长条（而非逐格 Mesh）
- 3D 热力面按 `chooseHeatAggregation` 聚合（120×80 → 40×27），2D 视图按 `chooseStride` 降采样到 60×40
- 每帧数据在初始化时预降采样并缓存，播放时只在粗网格上插值
- 网格线在密集网格下降采样为约 40 条

降采样只影响显示精度；契约校验、悬停读数、指标计算仍基于原始数据。

### 生成演示数据（A 场景 → 浏览器可读）

A 的 `export_rho.py` 输出 `.npz`（浏览器不可读），B 侧提供转换：

```powershell
# A 场景 JSON → scene-bundle JSON
python tools/export_scene_bundle.py apps/web/scene-config/scenes/gym_example.json `
    -o apps/web/prototype/data/gym.bundle.json

# 生成 gym 规模的 Mock 轨迹 + 分析（合成演示数据，非模型输出）
python tools/make_demo_trajectory.py apps/web/prototype/data/gym.bundle.json `
    -o apps/web/prototype/data/gym.trajectory.mock.json
```

> 转换脚本读取 `apps/web/scene-config/python/scene_io.py`（A 模块），需先将 A 分支检出到该目录。

### 运行

在仓库根目录（需要本地 HTTP 服务器，`fetch` 不支持 `file://`；Three.js 已本地化，无须联网）：

```powershell
python -m http.server 8000
# 浏览器打开 http://localhost:8000/apps/web/prototype/
# 切换数据集：http://localhost:8000/apps/web/prototype/index.html?dataset=legacy-mock
```

建议使用 Chrome / Edge 等现代浏览器。

### 坐标映射约定（与 [契约](../../contracts/README.md) 一致）

- 计算平面 x/y，单位米；数组 `[iy][ix]`，行从 y 小到大；速度元素 `[vx, vy]`
- 网格原点是左下角单元的左下角，单元中心为 `origin + ((ix+0.5)*dx, (iy+0.5)*dy)`
- Three.js 渲染映射：`world = (x, height, -y)`；2D 俯视图直接按数据坐标绘制（y 向上）
- 页面状态面板的"方向自检"应显示 PASS；若热力 / 箭头 / 粒子方向与出口不符，说明映射写反

### 边界说明

- 页面顶部显著标注 Mock 数据；粒子数量与路径仅为示意，不代表真实人数，不构成个体疏散结论（NFR-04）
- W2 计划：将数据源从 Mock 切换为 C 输出的真实 PDE 场结果，并记录播放性能；方向自检与契约校验不依赖 Mock 的东向出口，可直接复用

### 第三方库（本地 vendored，无构建步骤；来源与校验值按协作约定记录）

- `lib/three.module.js` — three.js r169（npm 包 `three@0.169` 的 module 构建），SHA-256 `0a3368c165eea773490aec7b77c22de70e3eac288503409256fdbf4d12578416`
- `lib/OrbitControls.js` — three.js r169 官方 examples 控制器（本地适配版），SHA-256 `80efaadea4f8a636a65fb0bd08bfef62f3d93a0bb94e2e7500f23176c5c07f4e`
- 技术栈最终去留由 BOOT-08 评审决定；确认后若引入包管理，以上文件改由锁文件管理
# Web 模块

A 负责场景配置，B 负责 3D / 俯视与回放，E 协调统一入口。

A 的本地实现位于 [scene-config](scene-config/README.md)，包含静态网页编辑器、场景校验、栅格化和候选布局接口；它使用 A-local v0.2，不修改共享契约。B 的 3D/俯视与回放、E 的统一入口和产品框架仍待实现。

先联合评审 [契约](../../contracts/README.md)，使用 [Mock 样例](../../examples/README.md) 独立推进；技术栈确认后增加产品代码、锁文件、启动步骤和对应测试。场景编辑、场数据读取和显示层应保持清晰边界。
