# W1 系统底座验证记录

日期：2026-11-20。执行人：E（H-yuin，AI 辅助草稿由提交者审查）。对象：`apps/api/` 与 `apps/web/` 的 W1 Mock 联调底座。

状态：已执行，待团队评审。关联 BOOT-09、REQ-06、NFR-02；结果来源明确为 **Mock**，没有 PDE 或 FNO 结果。

## 环境与范围

- Windows，工作目录 `D:\course\CrowdTurb`。
- Python 3.12.14，`jsonschema==4.25.1`，本地 `.venv`。
- 仓库检查与契约测试使用现有样例；系统测试使用临时 SQLite 数据库和真实 HTTP 请求。
- 浏览器检查使用本地 Edge；截图输出在忽略目录 `output/playwright/system-base.png`，不作为版本化验收证据。

## 实际执行

- `python tools/check_repository.py`：通过。3 份 Schema 与关联 Mock 一致；50 个本地文档链接有效；11 条需求追踪条目有效；3 份 DOCX 原件校验值一致。
- `python -m unittest discover -s tests/contracts -v`：11 项通过。
- `python -m unittest discover -s tests/system -v`：8 项通过。覆盖健康检查与静态页面、项目创建、场景版本 1→2 往返、非法场景拒绝且不保存、Mock 任务成功并保存结果、PDE 未接入时以 `solver_not_configured` 失败、未知项目和来源错误、服务重启后结果仍可读取、遗留任务重启后标记 `task_interrupted`。
- Python 语法检查：`apps/api/crowdturb/*.py` 与系统测试全部通过 `py_compile`。
- 本地 HTTP 服务：`python -m apps.api.crowdturb --port 8765 --db data/demo.sqlite3` 可启动；页面显示默认项目和场景 JSON，服务状态在线。
- 浏览器交互检查：桌面 1440×1000 页面正常加载；选择 PDE 后任务实际进入 `failed`，错误码为 `solver_not_configured`；再选择 Mock 后任务实际进入 `succeeded`，结果显示“Mock 联调 · 2 帧”，原始 JSON 的 `source` 为 `mock`，浏览器控制台无错误。截图保存在忽略目录 `output/playwright/system-base-success.png`。

## 结果与局限

- HTTP 任务状态实际按 `queued → running → succeeded / failed` 写入 SQLite；成功结果包含两帧 Mock 场，PDE / FNO 失败有可读原因。
- 场景版本由服务端递增，保存后重新读取得到新 revision；非法场景返回 400 且不产生新版本。
- 浏览器交互覆盖来源切换、PDE 失败、Mock 成功和结果展示；场景保存、非法输入、重启恢复等仍由 HTTP / 持久化测试覆盖，没有逐按钮走完全部 UI。
- GitHub Actions 尚未对本次本地改动运行；不能把本地通过当作远程 CI 通过。
- 尚无 PDE / FNO 求解、3D / 俯视、指标、候选优化、正式部署、认证或大数组结果存储，也没有整体产品验收。

## 结论

W1 系统底座在 Mock 路径上达到可启动、可保存、可查询、可恢复的验证原型状态。REQ-06 与 NFR-02 保持 `in_progress`，需 A-D 联调与 PR 评审后才能进一步更新；本记录不构成真实疏散或算法正确性结论。
