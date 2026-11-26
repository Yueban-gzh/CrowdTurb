# 工作台联调接口

E 提供布局任务和回放加载接口。B/C 的实际接入待双方联测。单层轨迹、场和分析继续使用 D 当前版本的契约。

## B 按运行加载

入口通过查询参数传入 `run` 和 `view`，例如 `?run=preview-…&view=baseline`。基准方案键为 `baseline`，其他方案键由该运行的 `views` 列出。

1. `GET /api/workbench/runs/{run}` 返回场景快照和运行状态。
2. `GET /api/workbench/runs/{run}/views/{view}` 返回版本为 `1.0.0` 的清单，含运行编号、方案键、来源和四个独立产物地址。运行未完成时返回 409，方案不存在时返回 404。
3. 从 `artifacts` 读取 `scene`、`trajectory`、`field` 和 `analysis`。依次用于几何与出口、逐人回放、D 聚合热力及指标。B 可复用 [ES 模块加载器](../apps/web/workbench-api.js) 的 `loadWorkbenchView(run, view)`。

运行创建接口 `POST /api/workbench/runs` 接收 `{"scene": {...}}`，返回 202 与已保存的 `run_id`，详情状态为 `queued`、`running`、`succeeded` 或 `failed`。失败记录含 `error.code` 与 `error.message`。服务重启时，遗留的评估标记为中断失败。

## C 的替换点

E 将场景快照、修订、输入哈希、栅格和出口单元格、统一初始人员、时长、步长和种子整理为 `SimulationInput`。后台编排逐个调用仿真适配器，以 D 的 `trajectory 0.2.0` 作为输出。D 根据同一批运行产物计算指标和可比性。

当前适配器从初始密度生成最多 500 名合成人员，按固定规则生成采样帧和退出事件。候选的 Mock 差异只用于验证比较流程。C 接入前需要确认真实运行如何接收出口几何、目标与行为设置；多层需要另外确认楼层及跨层连接字段。
