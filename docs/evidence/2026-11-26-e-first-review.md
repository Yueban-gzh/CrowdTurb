# E 首轮联调验收讨论稿

日期：2026-11-26。范围：REQ-06、VT-06、VT-10 的 Mock 集成验证。状态：已完成本地验证，待团队讨论。

## 现场演示

在仓库根目录运行。需要 Python 3.11+ 和 `requirements-api.txt` 中的依赖。将 D 的固定提交解压至 `output`，传入同时包含 `examples/` 与 `contracts/` 的根目录。

```powershell
$revision = '769b196ae2eecd9c1b51c85d535571a2c2f9d791'
New-Item -ItemType Directory -Force output | Out-Null
curl.exe -fL "https://codeload.github.com/Yueban-gzh/CrowdTurb/zip/$revision" -o output/team-D.zip
Expand-Archive output/team-D.zip output
python -m apps.api.crowdturb --port 8768 --db data/e-first-review.sqlite3 --team-fixtures "output/CrowdTurb-$revision"
```

1. 打开 [首轮验收页](http://127.0.0.1:8768/review)，点击「保存轨迹与分析样例」。检查场景 `mock-trajectory-hall`、版本 1、输入哈希与三个独立产物，刷新后应能继续打开同一记录。
2. 点击「保存比较样例」。检查场景 `mock-workflow-hall`、独立基准结果编号、27 项候选及其状态。样例中的失败原因、不可比较原因和推荐项均为 Mock。
3. 重启时沿用同一数据库。打开旧记录，逐项读取原始 JSON。启动参数 `--team-fixtures` 用于导入，历史记录由数据库提供。

## 实测结果

- 使用 D [PR #3](https://github.com/Yueban-gzh/CrowdTurb/pull/3) 的固定提交 `769b196` 导入了四个原始产物。轨迹 `0.2.0`、聚合场与分析 `0.3.0`、比较 `0.1.0` 各自通过对应 Schema。轨迹、聚合场和分析绑定同场景版本、输入摘要和网格。
- 通过 HTTP 导入后重启服务，四个 JSON 与原文件解析后的内容相等。对照组索引无法请求另一组的产物。篡改分析输入哈希返回 422，数据库没有新增记录。
- 将 E 的 HTTP 响应交给 B 固定提交 `2cf0454` 的 `adaptTrajectoryScene`、`adaptTrajectory`、`adaptDerivedField`、`adaptAnalysis`。适配得到 3×2 网格、0 / 1.5 / 4 秒三帧、2 个退出事件、3 项带单位指标、3 条时间序列与 1 个网格热点。
- 在浏览器中完成两类样例保存，刷新后重新看到原运行编号及 JSON 链接。仓库检查、11 项旧契约用例和 11 项系统用例通过。系统用例使用 `CROWDTURB_TEAM_FIXTURES` 指向上述 D 快照。

## 评审决定项

- **A/C 确认空间输入。** 选一个可移动矩形障碍，对照同一场景版本下的 3D 位姿、2D 占位、新栅格及输入哈希。首个真实对照只改一个建筑变量，出口宽度与障碍移动二选一。
- **C 确认可执行入口。** 先提供一份符合轨迹契约的真实短样本，覆盖稳定人员 ID、秒制时间、位置、速度、退出事件和运行配置。然后交付可重复调用的完整仿真入口。E 接入后记录场景、输入和运行配置，再提交一组基准与候选。
- **B/D 确认消费端。** B 的适配函数已通过数据读取，下一步由 B 接运行编号、实际回放加载和比较页。D 用 C 的真实对照确认分析和比较。本轮页面显示样例摘要与原始结果。
- **E 确认验收口径。** 先签认 Mock 导入、来源标注、失败反馈与重启读取，再记录真实运行、同条件比较和 B 页面验证。REQ-06、NFR-02 继续保持进行中。
