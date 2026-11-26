# A 模块：初始场配置（含参数化设施 v0.2）

2026-11-25 D 扩展：Python 核心兼容 0.1/0.2/0.3，新增独立通道宽度，见 [通道扩展](PASSAGES.md)。下文为 A 原 v0.2 说明；原网页编辑器尚不支持 v0.3。仿真方向已由用户改为 STDDN，见 [ADR 0002](../../../docs/decisions/0002-stddn-trajectory-and-passage.md)。

> 负责人：成员 A ｜ 模块：初始场配置 ｜ 目标：**场馆、初始场与可移动设施配置可用**
> 对接说明见 [对接文档](对接文档.md)。本模块使用 A-local v0.2，不修改共享契约 0.1.0。
> 仿真方案：密度（连续场 PDE/FNO）。人群按“分布（区域+人数）”描述，不存个体；
> C 如需 agent 对比实验，自行从分布采样个体；粒子是 B 的展示层，与 A 无关。

## 一、目录结构

```
apps/web/scene-config/
├── README.md                          ← 本文件
├── 对接文档.md                         ← A/B/C/D/E 对接边界与 D 清单回复
├── requirements.txt                   ← Python 运行依赖
├── schema/scene_schema_v0.1.json      ← 旧格式：固定障碍，编辑器可兼容加载
├── schema/scene_schema_v0.2.json      ← 新格式：参数化设施 + variable_parameters（B 对接声明）
├── scenes/gym_example.json            ← v0.1 体育馆兼容示例
├── scenes/gym_facilities_example.json ← v0.2 体育馆 + 两个可移动临时展台
├── scenes/arena_complex_example.json  ← v0.2 复杂场馆：分区、环廊、固定房间、四个展台
├── scenes/candidates_example.json     ← D 的候选布局示例（含重复/非法候选）
├── editor/editor.html                 ← v0.2 网页编辑器：设施/旋转/允许区域/分层地图
├── python/geometry.py                 ← 旋转矩形、SAT 重叠、包含、距离
├── python/scene_io.py                 ← 加载/校验/栅格化/哈希/导出
├── python/layout_eval.py              ← 候选布局：应用修改、校验、掩码去重、CLI
├── python/export_rho.py               ← 命令行导出（校验 + .npz + 预览图）
├── tests/test_facility_layout.py      ← 35 个回归测试
└── scenes/output/                     ← 本地导出产物（.npz / 预览图），不提交
```

## 二、快速上手

以下命令均在 `apps/web/scene-config` 目录执行。Python 需要 3.11 或更新版本。

```bash
python -m pip install -r requirements.txt
```

### 1. 网页编辑器（v0.2）

双击打开 `editor/editor.html`，或在本目录启动静态服务器。默认载入复杂场馆示例，可编辑人群区、出口、固定障碍和可移动展台，支持旋转、允许区域、图层切换、缩放和 JSON 保存/加载。

### 2. 命令行流程

```
python python/export_rho.py scenes/gym_example.json --preview
python python/export_rho.py scenes/arena_complex_example.json --out scenes/output/arena_complex.npz --preview
```

### 3. 新流程（v0.2，参数化设施 + 候选布局）

```bash
# 校验并导出基准场景（含掩码和哈希）
python python/export_rho.py scenes/gym_facilities_example.json --out scenes/output/gym_facilities.npz

# 批量评价 D 的候选：几何约束 + 掩码去重
python python/layout_eval.py scenes/gym_facilities_example.json scenes/candidates_example.json \
    --out scenes/output/layout_report.json \
    --write-scene scenes/output/selected_scene.json

# 回归测试
python -m unittest discover -s tests -v
```

退出码：`export_rho.py` 0=通过已导出 / 1=有 error 未导出；`layout_eval.py`
0=至少一个可行候选 / 1=没有可行候选。报告里的 `solve_required=False` 表示
不需要 C 重复跑 PDE（非法、重复或与基准掩码相同）。

## 三、v0.2 设施对象（D 方案的核心）

固定设施仍用 `geometry.obstacles`（轴对齐、不可移动）。可优化设施放在
`geometry.facilities`，第一版只支持**固定尺寸、不可穿越、允许移动的旋转矩形**：

```json
{
  "id": "F1",
  "name": "临时展台 A",
  "kind": "exhibit",
  "effect": "block",
  "center": [30.0, 7.5],
  "length": 4.0,
  "width": 2.0,
  "rotation_deg": 15.0,
  "movable": true,
  "resizable": false,
  "allowed_region": [[18, 6], [42, 6], [42, 9], [18, 9]]
}
```

- `center`：中心 `[x, y]`，米。
- `length` / `width`：沿设施局部 x / y 轴，米；`rotation_deg` 绕中心逆时针。
- `movable`：是否允许 D 修改 `center` / `rotation_deg`。
- `resizable`：是否允许修改 `length` / `width`；为 `true` 时必须给
  `size_limits: {"length": [min,max], "width": [min,max]}`。
- `allowed_region`：允许移动区域多边形；设施矩形必须**完整**落在其中。
  省略表示整个场地。初始人群区应作为禁放区。
- `effect`：第一版只接受 `"block"`。服务点（到达需求/服务能力/排队）和
  “可通行但降速”区域不在本版；后续扩展时需要新增字段和新版本。

### 栅格化规则

- 格心落在旋转矩形内（含边界）→ 该格不可通行。规则编码为 `cell_rule="center"`。
- 移动小于约一个 `resolution` 时，视觉位姿可能不同但掩码相同；A 用
  `mask_hash` / `input_hash` 识别，D 不应重复求解。报告会给出
  `below_grid_resolution` 与 `duplicate_of`。
- 掩码顺序：固定障碍 → 内部隔墙 → 边界墙（出口开口）→ 参数化设施；
  `walkable = ~(obstacle_mask | wall_mask)`。

## 四、A 提供的新接口

### 1. 修改设施/出口 → 新场景

`layout_eval.apply_layout(scene, changes)`：

- 输入是 v0.2 场景和修改列表：
  ```json
  [{"facility_id": "F1", "center": [22.0, 7.5]},
   {"facility_id": "F2", "rotation_deg": 90.0}]
  ```
- 只允许改 `center`、`rotation_deg`（需 `movable=true`）和 `length`、`width`
  （需 `resizable=true` 且在 `size_limits` 内）；出口只允许改 `width`。
- 若场景声明了 `variable_parameters`，修改后自动刷新 `current_value` 和
  `scene_diff`，超出 `allowed_range` 的候选直接判为非法。
- 返回深拷贝的新场景，`meta.revision` 递增并记录 `parent_revision`；原场景不变。
- 非法修改抛 `LayoutError`（未知设施、固定障碍、越权改尺寸、未知字段等）。

### 2. 批量评价候选

`layout_eval.evaluate_candidates(scene, candidates)` 返回每个候选：

| 字段 | 含义 |
| --- | --- |
| `feasible` | 无 error 级几何问题；只有 warning 仍算可行，D 可自行过滤 |
| `problems` | 与 `validate()` 相同结构：`{level, msg}` |
| `scene_revision` | 新场景修订号 |
| `layout` | 修改后的全部设施位姿（按 id 排序） |
| `changed_facilities` / `changed_exits` | 本候选实际修改的设施和出口 ID |
| `pose_delta` / `move_distance_m` / `rotation_delta_deg` | 设施修改量；A 只提供原始量，排序由 D 决定 |
| `exit_width_deltas` | 出口宽度修改前后的米制差值 |
| `variable_parameters` | 修改后的完整可变参数声明，可直接交给 B 展示 |
| `variable_parameter_deltas` | 每个声明的前值、后值、差值和是否变化；`scene_diff` 已刷新为修改后的场景 |
| `mask_changed` / `below_grid_resolution` | 障碍掩码是否变化；后者说明设施修改被栅格“吃掉” |
| `mask_hash` / `walkable_hash` / `input_hash` | 障碍掩码 / 可行走 / C 实际输入的指纹 |
| `solve_required` | 可行且不与基准/其他候选重复，需要 C 求解 |
| `duplicate_of` / `duplicate_mask_of` | 与哪个候选（或 `baseline`）重复 |
| `metrics` | 可行走格、障碍格、设施格、压人群格、出口数等 |

报告顶层还有 `base_*_hash`、`candidate_count`、`feasible_count`、
`unique_solve_count`、`duplicates_skipped` 和基准 `variable_parameters`。D 只需对
`solve_required=True` 的候选调用 C。出口宽度变化可能不改变障碍 `mask_hash`，
但会改变 `walkable_hash` 和 `input_hash`，去重以 `input_hash` 为准。

### 3. 交给 C 的 .npz

在原有 `rho0 / walkable / zone_id / dx / params / exits_grid / exit_ids` 之外新增：

- `base_walkable`：不含设施的可行走掩码（用于核对设施加入前后的差异）；
- `obstacle_mask` / `fixed_obstacle_mask` / `facility_mask`；
- `mask_hash` / `walkable_hash` / `input_hash` / `layout_hash`；
- `scene_version` / `scene_revision` / `layout_json` / `layout_policy_json`；
- `variable_parameters_json`：JSON 字符串，供 B 读取可变参数声明；v0.1 为 `[]`；
- `exit_cells_json`（每个出口的格坐标）与 `crowd_covered_cells`。

C 更新求解输入时，必须用新的 `walkable`（或 `obstacle_mask`）重建障碍/通行
计算，而不是只移动 3D 模型；`input_hash` 可用于确认本次 PDE 输入确实变化。

## 五、可变参数声明（给 B）

v0.2 场景可增加顶层 `variable_parameters`，用来说明“哪些建筑参数允许变化、
当前值是多少、变化后对应哪个场景”。它只供 B 展示参数前后差异，不直接参与
栅格化，也不改变 `mask_hash` / `walkable_hash` / `input_hash` / `layout_hash`。

```json
"variable_parameters": [
  {
    "name": "南侧主出口宽度（m）",
    "type": "exit_width",
    "target_id": "E1",
    "unit": "m",
    "allowed_range": [4.0, 10.0],
    "current_value": 6.0,
    "scene_diff": [{ "exit_id": "E1", "width": 6.0 }]
  }
]
```

- `name`：展示名，必须唯一。
- `type` / `target_id`：参数类型和真实出口/设施 ID；同一对象的同一类型只能声明一次。
- `unit` / `allowed_range` / `current_value`：单位、允许范围和当前值；当前值必须
  与场景实际值一致，且在范围内。
- `scene_description` 或 `scene_diff`：必须且只能提供一个。前者是文字说明，后者是
  可直接交给 `apply_layout()` 的修改；候选评价后 A 会刷新它。
- 当前支持 `exit_width`、`facility_center_x`、`facility_center_y`、
  `facility_rotation`、`facility_length`、`facility_width`。
- 设施参数仍受 `movable` / `resizable` 和 `size_limits` 约束；通道位置/宽度、
  固定障碍形变不在本版支持范围内。
- B 可从候选报告的 `variable_parameter_deltas` 直接读取 `before_value`、
  `after_value`、`delta`、`changed`，无需自己反推场景差异。

## 六、校验规则

**error（必须修）**

- 固定障碍：超界、互相重叠；出口贴墙角/超出墙面/同侧重叠。
- 人群区：顶点超界、完全被障碍/墙覆盖、无法到达任何出口。
- 出口：开口不足一个栅格；被设施覆盖。
- 设施：位姿/尺寸非有限正数；超出场地；超出 `allowed_region`；与固定障碍/
  其他设施重叠；穿过内部隔墙；距出口小于 `exit_clearance`（默认 0.5m）；
  压到初始人群区；造成通路宽度小于 `min_passage_width`（默认 0.9m）。
- 设施造成的“切断通路”用栅格 BFS 检查；窄通道用 8 邻域净空近似检查。
- 可变参数：缺少/未知字段、类型或单位不支持、名称/绑定重复、范围非法、当前值
  不一致或越界、目标不存在、权限不足、描述与差异同时提供或同时缺失。

**warning（提示）**

- 人群区局部死角；区域互相覆盖（密度已归一化）；密度超过 `jam_density`；
- 设施在栅格上不可见（尺寸小于分辨率）；
- 修改后掩码未变化（D 应跳过重复求解）。

`geometry.layout_policy` 可覆盖默认约束：

```json
"layout_policy": {"min_passage_width": 0.9, "exit_clearance": 0.5}
```

## 七、和 D 的五点逐条对应

| D 的问题 | A 的实现 |
| --- | --- |
| 设施不能都当障碍 | v0.2 只接受 `effect="block"` 的矩形设施；服务点/降速区明确留到后续版本，不混入第一版 |
| A 把设施参数化 | `id / center / length / width / rotation_deg / movable / resizable / allowed_region`，并有结构校验 |
| A 生成新配置并校验 | `apply_layout` 深拷贝 + 版本递增；`validate` 覆盖越界、重叠、出口、通路、压人群 |
| C 让修改进入计算 | `rasterize` 重算 `walkable/obstacle_mask`；`.npz` 带 `input_hash`；C 必须用新掩码重解 |
| 识别重复掩码 | `mask_hash`/`input_hash` + `evaluate_candidates` 去重，报告 `duplicate_of` 与 `solve_required` |
| 初始人群不能被覆盖 | 设施压到可行走人群格 → error，并估算影响人数；第一版不做重分配 |
| 移动幅度可被网格识别 | 报告 `mask_changed` / `below_grid_resolution`；D 跳过相同掩码候选 |

如果后续用 FNO 初筛，仍需 C 验证模型是否适用于这些障碍布局；A 不默认
FNO 已支持。

## 八、已知限制（演示时要说明）

- 编辑器已支持 v0.2 设施，但允许区域的可视化编辑目前是“按当前设施外扩 2m”的矩形；
  凹多边形或精确边界仍需在 JSON 中精修。
- 设施只支持**不可穿越的旋转矩形**；不支持服务点排队、速度影响区和非矩形轮廓。
  编辑器可新增/删除设施，但 D 的第一版候选优化仍固定数量和尺寸。
- 初始人群区是**禁放区**；不支持“重新分配人群并保持总人数”的候选。
- 栅格规则是格心采样；极薄设施可能不可见，移动小于分辨率可能不改变掩码。
- 最小通路宽度是 8 邻域离散近似，不是精确欧氏净空；阈值需与 C/D 联调。
- 共享契约 `CrowdTurb/contracts/scene.schema.json` 仍是 0.1.0；v0.2 只在
  A 模块内部使用，升级共享契约需组内评审。

## 九、后几周路线

- **第 2 周**：完善 v0.2 编辑器的允许区域精修与拖拽移动；补充
  IFC→场景 JSON 的一次性转换脚本（只抽楼层轮廓/墙/门/空间语义）。
- **第 3 周**：把设施候选接入 E 的任务调度，输出“原方案 vs 候选”场景版本；
  与 C 确认 PDE 输入契约和 `input_hash` 复现记录。
- **第 4 周**：异常输入修复、回归测试和演示材料。
