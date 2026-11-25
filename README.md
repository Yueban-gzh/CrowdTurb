# CrowdTurb

智能软件工程课程项目：面向单层场馆的人流仿真与建筑布局优化原型。

四周内跑通 **配置 → 个体仿真 → 3D 展示 → 拥堵分析 → 布局优化 → 同条件对比**。2026-11-25 用户确认采用 STDDN 路线，见 [方向变更](docs/decisions/0002-stddn-trajectory-and-passage.md)。

**当前分支：D 开发版，基于 A 的场景分支。** 已有 A 场景/校验、D 通道宽度扩展、轨迹聚合/指标/候选及 Mock。B 原型位于独立分支，尚未合并；C 完整仿真、真实比较和 E 服务尚未集成。Mock 仅用于联调。

## 新成员从这里开始

1. 阅读 [范围与需求](docs/requirements.md)，确认四周的必做项。
2. 阅读 [分工与四周计划](docs/plan.md)，在组会上将 A–E 对应到实际成员。
3. 阅读 [数据契约](contracts/README.md)，用 [示例数据](examples/README.md) 并行开发。
4. 从 [首周任务](docs/backlog.md) 认领任务，按 [协作约定](CONTRIBUTING.md) 提交 PR。
5. 按下方命令运行检查。真正的产品启动命令由 E 在首次实现后补充。

## 本地检查

检查环境使用 Python 3.11/3.12。以下命令在仓库根目录执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools/check_repository.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/contracts -v
.\.venv\Scripts\python.exe -m unittest discover -s apps/web/scene-config/tests -v
.\.venv\Scripts\python.exe -m unittest discover -s packages/analysis/tests -v
```

macOS / Linux 使用 `.venv/bin/python` 替代 `.\.venv\Scripts\python.exe`。检查覆盖契约、示例一致性、需求追踪条目、文档链接和原始资料校验值，不能证明求解算法或产品已经正确。

## 目录

```text
apps/web/             A/B：场景配置与 3D 展示的预留位置
apps/api/             E：任务、保存、统一 API 的预留位置
packages/solver/      C：完整个体仿真的预留位置
packages/analysis/    D：轨迹聚合、指标、热点与候选搜索
contracts/            版本化 JSON Schema 与接口约定
examples/             可共享的合成场馆和 Mock 场结果
tests/                契约检查；产品测试随实现补充
tools/                仓库检查工具
docs/                 需求、计划、设计决策、验证与过程记录
.github/              Issue / PR 模板和自动检查
```

React / TypeScript、Python / FastAPI 等仅为早期候选，尚未安装或锁定。见 [技术选型草案](docs/decisions/0001-architecture.md)。

## 如何体现软件工程

- 需求工程：为必做能力编号，写清可观察的验收条件，并区分需求来源与团队新增设计。
- 迭代与风险管理：每周交付可演示增量；优先保证完整仿真闭环，记录模型边界与失败。
- 模块化与契约设计：五人按模块负责，先对齐输入输出；跨模块变化共同评审。
- 配置与变更管理：Issue → 分支 → PR → 评审 → 检查 → 合并；保留需求与设计变更记录。
- 验证与确认：将需求关联到测试、实验及实际结果；未执行和 Mock 证据始终明确标注。

具体流程见 [协作约定](CONTRIBUTING.md)、[测试计划](docs/verification.md) 和 [需求追踪](docs/traceability.json)。课程评分细则尚未提供，这些是项目采用的方法，不代表教师已认可。

## 范围与资料

项目现名 **CrowdTurb**。原始材料使用旧名 ArchFlow AI；V5 范围经 2026-11-25 用户确认修订为 STDDN 个体仿真与 D 聚合。原件和校验值保留，见 [资料索引](docs/references/README.md)。

真实布局评价需要 C 完整仿真，模型单次推理成功不能代替。D 聚合场不等于 PDE 输出；装饰性粒子与模型轨迹区分。结果只用于课程方案比较。

## GitHub

默认集成分支为 `main`，本轮工作在 `feat/D-analysis-passage`，远程为 [Yueban-gzh/CrowdTurb](https://github.com/Yueban-gzh/CrowdTurb)。源资料随仓库保存，许可证由团队决定。
