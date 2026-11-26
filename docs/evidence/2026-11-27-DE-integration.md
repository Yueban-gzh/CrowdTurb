# D/E 集成回归记录

日期：2026-11-27。关联 Issue #8；REQ-01、REQ-03–06、NFR-02。当前为待团队评审的集成实现，不等于课程整体验收。

## 接入范围

以 main `5b9740e` 为基线，通过合并提交保留 D `769b196` 与 E `1f34576` 历史。D 的聚合、自动采样、预算与公平比较进入 E 的异步任务与持久化；B 使用同一运行产物进行回放与同屏比较。E 提供的 ONNX 原型及其规划 / 碰撞 / 离场逻辑原样接入，C 仍负责模型与完整仿真的确认和校准。

集成修复：启动脚本直接使用当前仓库 A/B/D；默认场景保留 0.3 通道；A 网页新增既有通道净宽控件、隔挡绘制与栅格化、声明更新及几何检查。没有变更 JSON 契约字段或单位。新增通道的包络与方向仍在 JSON 声明。

## 实际检查

环境：Windows，本地 Python 3.12、NumPy 1.26.4、jsonschema 4.25.1、onnxruntime 1.20.1；浏览器为本机 Chrome，Playwright 1.62.1。下列命令均在仓库根目录实际执行。既有 .venv 引用的旧宿主 Python 已不存在，测试使用隔离临时环境；随后更新本机 .venv 的宿主路径与依赖。环境与日志目录均被 Git 忽略。

- `python tools/check_repository.py`：通过，检查共享契约、D 可重建样例、链接、11 条需求追踪和 3 份原始资料校验值。
- `python -m unittest discover -s tests/contracts -v`：11 项通过。
- `python -m unittest discover -s apps/web/scene-config/tests -v`：43 项通过。
- `python -m unittest discover -s packages/analysis/tests -v`：39 项通过。
- 设置 `CROWDTURB_TEAM_FIXTURES` 为当前仓库根目录、`CROWDTURB_ONNX_MODEL` 为本地权重后，`python -m unittest discover -s tests/system -v`：20 项通过，无跳过。包含 44 人展台真实 ONNX 运行、真实通道候选、Mock 通道及任务 / 持久化 / 失败路径。
- `python tools/start_workbench.py --prepare-only`：成功，从本地源码生成 B 兼容页，不再下载分支快照。
- `tests/e2e/workbench.mjs`：通过。实际修改出口至 6.1 m、提交运行、刷新恢复、D 候选回放及 B 同屏时间轴；随后把 P1 净宽改为 6 m、选择通道参数提交、刷新恢复，逐格核对 JavaScript 与 Python 的可通行掩码完全相同，并通过文件输入重新载入该 0.3 场景；页面无未捕获错误。本机通过临时测试入口引用自带 Playwright，设置 `PLAYWRIGHT_CHANNEL=chrome`，断言与仓库内脚本一致。

原始本地日志：`artifacts/contracts.log`、`artifacts/a-tests.log`、`artifacts/d-tests.log`、`artifacts/integration-system.log`、`artifacts/integration-browser.log`，均为本次执行产物，不提交运行目录。可复核断言在相应测试代码中；团队可按相同参数重跑。

模型文件不入库，SHA-256 为 `b3e5faa7e0e8846da3b05f905f4a42da653d9d4e03014cacea12aa29418b5272`。真实通道用例从默认场景保留同一初始人群，设 P1 基准净宽为 6 m，再生成候选；检查两侧掩码和输入哈希确实变化，初始人员摘要与仿真设置保持一致，D 场 / 分析与各自轨迹哈希一致。44 人测试检查基准及候选均有 44 条 E1 离场事件，末帧无残留。

## 仍需评审与验证

- 本次本地运行不能代替 GitHub CI；CI 状态以关联 PR 为准。CI 不持有模型，真实 ONNX 两项明确跳过，其他集成用例在当前仓库源码上执行。
- 需要 A 复核 0.3 编辑与几何，B 复核回放接入，C 复核 ONNX 完整推进，D/E 共同复核参数、预算、结果与持久化。未记录任何未发生的成员评审。
- ONNX 原型限定单层、最多 100 人、120 秒；当前规则尚未通过真实场馆校准。D 推荐使用期末残留与累计拥堵指标，不以最后离场时间排序，也不保证候选改善；每次 ONNX 最多求解一个候选。
- 按 CONTRIBUTING 的 Issue → 分支 → PR → 非作者评审 → 检查 → 合并流程进行；需求保持 in_progress，不能用集成通过代替模型适用性、性能或课程验收。
