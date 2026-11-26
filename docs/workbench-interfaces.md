# 工作台联调接口

E 提供布局任务和回放加载接口。本地联调已用 C 提供的 ONNX 文件生成轨迹，由 B 回放并交给 D 分析。单层轨迹、场和分析继续使用 D 当前版本的契约。

## B 按运行加载

入口通过查询参数传入 `run` 和 `view`，例如 `?run=preview-…&view=baseline`。基准方案键为 `baseline`，其他方案键由该运行的 `views` 列出。

1. `GET /api/workbench/runs/{run}` 返回场景快照和运行状态。
2. `GET /api/workbench/runs/{run}/views/{view}` 返回版本为 `1.0.0` 的清单，含运行编号、方案键、来源和可用产物地址。运行未完成时返回 409，方案不存在时返回 404。
3. 从 `artifacts` 读取 `scene`、`trajectory`、`field` 和 `analysis`。依次用于几何与出口、逐人回放、D 聚合热力及指标。新 ONNX 运行还可提供 `route`，其中 `points_m` 是从一名代表性行人的起始位置到出口的规划线点列，用于 2D 虚线。B 可复用 [ES 模块加载器](../apps/web/workbench-api.js) 的 `loadWorkbenchView(run, view)`。

运行创建接口 `POST /api/workbench/runs` 接收 `scene` 和可选的 `parameter`。例如 `{"type":"facility_center_x","target_id":"F1"}` 表示只生成 F1 横向位置候选，绑定需存在于该场景的 `variable_parameters`。省略时沿用自动选择。接口返回 202 与已保存的 `run_id`，详情状态为 `queued`、`running`、`succeeded` 或 `failed`。失败记录含 `error.code` 与 `error.message`。服务重启时，遗留的评估标记为中断失败。

## C 的替换点

E 将场景快照、修订、输入哈希、栅格和出口单元格、统一初始人员、时长、步长和种子整理为 `SimulationInput`。后台编排逐个调用仿真适配器，以 D 的 `trajectory 0.2.0` 作为输出。D 根据同一批运行产物计算指标和可比性。

## 启动 ONNX 联调

在仓库根目录执行以下命令，将模型路径替换为本机保存的文件。省略 `--onnx-model` 时，工作台默认使用 Mock，可用于无模型环境的接口联调。

```powershell
python -m pip install -r requirements-stddn.txt
python tools/start_workbench.py --port 8773 --db data/workbench-onnx.sqlite3 --onnx-model "<本地路径>/eth-hotel-joint.onnx"
```

打开 [工作台](http://127.0.0.1:8773/)，确认顶栏显示 `STDDN / ONNX`，点击「评估当前布局」。运行详情和轨迹会记录 `source: stddn` 及模型 SHA-256，权重从本地读取。ONNX 运行失败时会记录失败状态和错误信息。

## 本轮联调清单

- [x] 用原始 ONNX 文件在 CPU 上验证单步二维加速度输出。
- [x] 用 A 的布局初始化人员与目标，每 0.08 秒调用模型，结合栅格寻路、墙体检查和简单的人员间距处理更新位置、速度与邻居，记录轨迹和离场事件。
- [x] 接入 E 的异步评估及运行记录，分别提供基准和一个可运行候选的产物。
- [x] 在浏览器验证 B 的 3D 回放与数据自检，以及 D 的指标和方案比较。

首版默认场景试跑中，基准出口宽 6 m，候选 D0001 宽 4 m。两次均输出 109 帧，分别在 19.60 s、21.04 s 和 19.60 s、22.72 s 记录两人从 E1 离开。D 的期末人数和累计拥堵面积时间均为 0，比较结果为持平，保留基准。

44 人场景复现了出口边缘的卡死。原控制规则在 60 秒结束时有 33 人离场、11 人滞留。修正后优先让靠近出口的人移动，受阻时尝试侧让，继续检查墙体和人员间距。用相同场景和模型重跑，6 m 基准的 44 人均于 21.04 秒内离场，4 m 候选的 44 人均于 22.72 秒内离场。运行记录通过碰撞规则版本区分。

当前 ONNX 原型限单层、最多 100 人和 120 秒仿真。路线跟随、避障和离场逻辑是本轮补充的仿真规则，尚未用真实人流数据校准。

## 44 人展台绕行对照

工作台用八方向栅格路线和直线通行检查选取途经点。可见的出口开口支持斜向直达，障碍会触发绕行与路径拉直。人员移动仍经过墙体和间距检查。原出口卡死场景用新版寻路重跑，44 人在 16.08 秒内全部从基准的 6 米出口离场。

使用已有运行时，选择「方案比较」查看展台位置和指标差值，或在「人流与热点 → 2D 俯视」开启「显示参考路线（虚线）」。切换基准与候选时保留俯视模式、相机和当前时间。默认随工作台启动的 B 兼容页提供这些控件。

在仓库根目录启动 ONNX 工作台后，可以用 [44 人展台样例](../examples/workbench.route-demo.json) 复现。下面的端口应与启动命令一致。

```powershell
$scene = Get-Content -Encoding UTF8 -Raw examples/workbench.route-demo.json | ConvertFrom-Json
$parameter = @{ type = 'facility_center_x'; target_id = 'F1' }
$body = @{ scene = $scene; parameter = $parameter } | ConvertTo-Json -Depth 30
$result = Invoke-RestMethod http://127.0.0.1:8773/api/workbench/runs -Method Post -ContentType 'application/json; charset=utf-8' -Body $body
Start-Process "http://127.0.0.1:8773/?run=$($result.run.run_id)"
```

实跑中，展台中心由 `(15.25, 5)` 移到 `(18.5, 5)` 米后，代表性参考线由绕行变为直达。基准和候选均有 44 人离场，D 的累计拥堵面积时间由 17.08 降为 11.06 m²·s。ONNX 工作台每次最多实际求解一个预算内候选，其余候选按状态显示。

布局编辑时，合法放置的展台如果超出旧的横向或纵向评估范围，工作台会参考当前移动区和展台尺寸更新范围，并在属性区提示。候选仍由 A 和 D 校验几何。通过布局校验后，如果人数超过 ONNX 上限，评估会显示实际人数与上限。
