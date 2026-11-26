# CrowdTurb

智能软件工程课程项目：面向单层场馆的人流仿真与建筑布局优化原型。

四周内跑通 **配置 → STDDN 个体仿真 → 聚合分析 → 3D / 俯视回放 → 候选布局 → 同条件对比**。五名成员各自负责一个模块，通过共同的数据契约集成。路线变更见 [ADR 0002](docs/decisions/0002-stddn-trajectory-and-passage.md)。

**当前状态：D/E 集成版待团队评审。** 工作台使用当前仓库的 A 场景、B 回放和 D 分析；支持出口、通道净宽、设施位置，异步任务、持久化、候选比较与同屏回放。默认 Mock；提供本地 ONNX 文件后运行 STDDN 原型。已有受控小场验证，模型对真实场馆的适用性及课程整体验收仍待完成。验证范围见 [D/E 集成证据](docs/evidence/2026-11-27-DE-integration.md)。

## 新成员从这里开始

1. 阅读 [范围与需求](docs/requirements.md)，确认四周的必做项。
2. 阅读 [分工与四周计划](docs/plan.md)，在组会上将 A–E 对应到实际成员。
3. 阅读 [数据契约](contracts/README.md)，用 [示例数据](examples/README.md) 并行开发。
4. 从 [首周任务](docs/backlog.md) 认领任务，按 [协作约定](CONTRIBUTING.md) 提交 PR。
5. 按下方命令启动集成工作台，并运行检查。

## 启动集成工作台

启动直接使用当前 checkout 的 A/B/D，不下载其他分支快照。ONNX 接入、参数选择与产物 API 见 [工作台联调接口](docs/workbench-interfaces.md)。

当前只用 Python 标准库 HTTP 服务、SQLite 和原生 HTML / CSS / JavaScript，避免在 BOOT-08 技术评审前锁定产品框架。完整边界见 [W1 系统底座 ADR](docs/decisions/0002-w1-system-bootstrap.md)。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-workbench.txt
.\.venv\Scripts\python.exe tools/start_workbench.py
```

打开 `http://127.0.0.1:8770`，默认数据库为 `data/workbench.sqlite3`。省略模型时显示 Mock；真实运行先安装 `requirements-stddn.txt`，再加 `--onnx-model "<本地 ONNX 路径>"`。模型权重不入库。

原 W1 项目 / 场景 API 仍可用 `python -m apps.api.crowdturb --port 8000` 启动，保留历史 PDE/FNO 未配置失败检查。

## 本地检查

需要 Python 3.11 或更新版本。以下命令在仓库根目录执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools/check_repository.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/contracts -v
$env:CROWDTURB_TEAM_FIXTURES = (Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s apps/web/scene-config/tests -v
.\.venv\Scripts\python.exe -m unittest discover -s packages/analysis/tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/system -v
```

macOS / Linux 使用 `.venv/bin/python`，并设置 `export CROWDTURB_TEAM_FIXTURES="$PWD"`。真实 ONNX 系统用例还需 `requirements-stddn.txt` 与 `CROWDTURB_ONNX_MODEL`；未设置时明确跳过。CI 执行 Windows / Ubuntu 回归及 Chromium 浏览器联调，不包含模型权重或真实模型适用性实验。

## 目录

```text
apps/web/             E 工作台、A 场景编辑器、B 3D / 俯视回放
apps/api/             E：项目保存、场景版本、任务调度与结果 API
packages/solver/      C：独立求解器预留；现有 ONNX 适配位于 apps/api/crowdturb
packages/analysis/    D：轨迹聚合、指标 / 热点、候选采样与公平比较
contracts/            版本化 JSON Schema 与接口约定
examples/             可共享的合成场馆和 Mock 场结果
tests/                契约检查；产品测试随实现补充
tools/                仓库检查工具
docs/                 需求、计划、设计决策、验证与过程记录
.github/              Issue / PR 模板和自动检查
```

React / TypeScript、Python / FastAPI 等仍为候选，尚未安装或锁定。当前 W1 底座采用临时、可替换的 Python 标准库 HTTP 边界，业务服务与适配器接口不依赖该边界。见 [技术选型草案](docs/decisions/0001-architecture.md) 和 [W1 启动决策](docs/decisions/0002-w1-system-bootstrap.md)。

## 如何体现软件工程

- 需求工程：为必做能力编号，写清可观察的验收条件，并区分需求来源与团队新增设计。
- 迭代与风险管理：每周交付可演示增量；先验证完整 STDDN 轨迹，再验证同条件候选，记录模型局限与失败。
- 模块化与契约设计：五人按模块负责，先对齐输入输出；跨模块变化共同评审。
- 配置与变更管理：Issue → 分支 → PR → 评审 → 检查 → 合并；保留需求与设计变更记录。
- 验证与确认：将需求关联到测试、实验及实际结果；未执行和 Mock 证据始终明确标注。
- AI 辅助开发：用 [Agent 规则](AGENTS.md) 约束开发顺序与模块边界，在 [AI 使用记录](docs/ai/usage-log.md) 保留输入、输出、人工审查和验证；AI 输出不能代替实验证据。

具体流程见 [协作约定](CONTRIBUTING.md)、[测试计划](docs/verification.md) 和 [需求追踪](docs/traceability.json)。课程评分细则尚未提供，这些是项目采用的方法，不代表教师已认可。

## 范围与资料

项目现名 **CrowdTurb**。原始材料使用旧名 ArchFlow AI；当前基线采用 V5 四周 PRD 和 V5 五人计划，V0.1 概念说明保留作背景。版本差异、原件和校验值见 [资料索引](docs/references/README.md)。

当前主线为 STDDN，PDE/FNO 为可选历史或独立对照。模型轨迹、实测轨迹与 Mock 必须区分；当前导航、碰撞与离场规则尚待 C 校准。结果用于课程方案比较。

## GitHub

本地默认分支为 `main`，远程仓库为 [Yueban-gzh/CrowdTurb](https://github.com/Yueban-gzh/CrowdTurb)。源资料随仓库保存；许可证、分支保护和实际评审规则仍由团队决定，模板存在不代表远程规则已生效。
