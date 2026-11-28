# B 可视化原型验证记录

关联：REQ-03 / VT-03 / [PR #5](https://github.com/Yueban-gzh/CrowdTurb/pull/5)（已经由 E 整合进 main）/ NFR-04。
负责人：B。验证日期：2026-11-27 至 2026-11-28。状态：**in_progress**（本地与工作台验证已完成；VT-03 要求的"接入真实 PDE"子项未达成，见"未完成与边界"）。

## 启动方式

方式一（独立原型，B 分支）：

```powershell
python -m http.server 8000
# 打开 http://localhost:8000/apps/web/prototype/
# 数据集切换：?dataset=gym（体育馆）/ ?dataset=legacy-mock（4×3 小样例）
```

方式二（E 工作台集成版，含 STDDN 真实轨迹）：

```powershell
python -m apps.api.crowdturb --viewer-root <B检出根> --team-fixtures <D检出根>
# 打开 http://127.0.0.1:8300/workbench → 人流与热点
```

需要 Python 3.11+；Three.js r169 本地 vendored，无须联网。Chrome / Edge 实测。

## 检查内容与实际结果（对照 VT-03）

### 1. 小场方向 / 时间 / 热力色标检查（可手工判断）

- 4×3 小样例（`?dataset=legacy-mock`）：方向自检 16/16 个有效单元指向最近出口（100%）；
- 出口位置、障碍遮挡、色标范围（0 – 全场峰值密度）人工核对无误；
- 契约校验：schema_version、场景引用、维度、不可通行单元为零，非法数据给出可读错误。

### 2. 大场接入与性能（体育馆 120×80 @ 0.5 m，1900 人，A 场景）

- 数据源：A 场景（经 `tools/export_scene_bundle.py` 转换）+ 合成演示轨迹（`tools/make_demo_trajectory.py`，source=mock）；
- 方向自检 6540/6540 速度采样指向最近出口（100%）；
- 性能处理：墙体 / 障碍 / 出口按连续段合并，3D 热力 3 倍聚合、2D 2 倍降采样、帧数据预降采样缓存——修复了此前逐格绘制导致的页面卡死（根因：每帧插值分配数千数组对象触发 GC 压力，改为零分配的最近帧采样）。

### 3. 时间控制

播放 / 暂停 / 拖动 / 倍速 / 循环；**暂停即全场冻结**（验证方式：暂停后相隔 800 ms 的两张截图字节级一致）。暂停时拖动时间轴，热力与 2D 视图跟随刷新。

### 4. 数据源适配（四种格式）

| 来源 | 格式 | 结果 |
| --- | --- | --- |
| W1 旧样例 | `examples/scene.mock.json` + `field.mock.json` | 回归通过（16/16） |
| A | scene-bundle（场景栅格化 JSON） | 120×80 / 144×96 两个场景正常 |
| C | `contracts/trajectory.schema.json`（逐人轨迹） | 轨迹回放正常 |
| D | `contracts/analysis.schema.json`（指标 / 序列 / 热点） | 指标面板正常 |

### 5. 分析结果展示（D 的输出，B 只读不重算）

- 总体指标卡片（带单位、定义、来源徽章 Mock / STDDN）；
- 时间序列迷你折线图（D 口径 previous_sample：播放到 t 时刻显示最后一个 ≤ t 的采样值标记）；
- 热点在 2D 俯视图上按播放时刻高亮（修正过降采样网格坐标换算导致的越界问题）；
- 状态徽章：正常 / 待校验 / 失败（含原因文本）。

### 6. STDDN 轨迹接入（工作台）

经 E 的 `start_workbench.py --onnx-model` 启动，B 的嵌入回放器通过工作台 API（`/api/workbench/runs/{id}/views/{view}`）加载 STDDN ONNX 运行的轨迹与 D 的聚合场：单层展厅 30×20 m、2 名行人、60 s 仿真，逐人位置与人形展示正常。B 不读取 `.onnx` 文件本身。

## 发现并修复的问题

1. **大网格卡死**：每帧插值分配数千数组对象导致 GC 压力累积，播放数十秒后冻结。改为最近帧零分配采样 + 逐格 Mesh 合并后解决。
2. **降采样坐标未换算**：2D 视图使用降采样网格后，出口（未标绿）与热点（红框越界）坐标未按比例换算。已统一换算。
3. **嵌入加载器适配**：E 的 `team_assets.py` 按锚点改写 B 的加载入口；B 保持锚点稳定，双方口径见 [AI 使用记录](../ai/usage-log.md) 中 E 的"合并原型"条目。

## 未完成与边界（诚实声明）

- **VT-03 的"接入真实 PDE"子项未达成**：当前接入的真实数据是 C 的 STDDN agent 轨迹（经 E 工作台），不是 PDE 连续场。格子场格式的适配层已预留，待 C 的 PDE 输出后补验。
- 全部演示数据 `source=mock` 或 STDDN 模型输出；合成轨迹由直线走向最近出口的脚本生成，不代表真实疏散效果。
- 人形 / 粒子数量与路径不代表真实人数，不构成个体疏散结论（REQ-03 / NFR-04）。
- 团队评审（非作者评审）尚未完成；本记录不作为验收通过依据。
- 仿真方案已由团队调整为 agent / STDDN 路线，REQ-03 与 REQ-02 中"连续场 / PDE"的措辞与实际路线的差异建议走变更 Issue 正式化（团队事项）。

## 结论

REQ-03 的本地实现与验证已完成（Mock 与 STDDN 轨迹）,"接入真实 PDE"子项待 C 输出后补验。建议状态 `planned → in_progress`，证据为本文件；`verified` 待团队评审后由评审人更新。