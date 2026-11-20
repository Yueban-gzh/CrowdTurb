# CrowdTurb

智能软件工程课程项目：面向单层场馆的连续人流仿真与建筑布局优化原型。

四周内跑通 **配置 → PDE 计算 → 3D 展示 → 拥堵分析 → 布局优化 → 同条件对比**。五名成员各自负责一个模块，通过共同的数据契约集成。

**当前状态：仓库初始化。** 已有工程文档、接口草案、Mock 数据和仓库检查；尚无可运行的 Web 产品、PDE 求解器或 FNO 模型。Mock 数据只用于联调。

## 新成员从这里开始

1. 阅读 [范围与需求](docs/requirements.md)，确认四周的必做项。
2. 阅读 [分工与四周计划](docs/plan.md)，在组会上将 A–E 对应到实际成员。
3. 阅读 [数据契约](contracts/README.md)，用 [示例数据](examples/README.md) 并行开发。
4. 从 [首周任务](docs/backlog.md) 认领任务，按 [协作约定](CONTRIBUTING.md) 提交 PR。
5. 按下方命令运行检查。真正的产品启动命令由 E 在首次实现后补充。

## 本地检查

需要 Python 3.11 或更新版本。以下命令在仓库根目录执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools/check_repository.py
.\.venv\Scripts\python.exe -m unittest discover -s tests/contracts -v
```

macOS / Linux 使用 `.venv/bin/python` 替代 `.\.venv\Scripts\python.exe`。检查覆盖契约、示例一致性、需求追踪条目、文档链接和原始资料校验值，不能证明求解算法或产品已经正确。

## 目录

```text
apps/web/             A/B：场景配置与 3D 展示的预留位置
apps/api/             E：任务、保存、统一 API 的预留位置
packages/solver/      C：PDE 与 FNO 的预留位置
packages/analysis/    D：指标与候选搜索的预留位置
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
- 迭代与风险管理：每周交付可演示增量；优先保证 PDE 闭环，记录 FNO 的回退决策。
- 模块化与契约设计：五人按模块负责，先对齐输入输出；跨模块变化共同评审。
- 配置与变更管理：Issue → 分支 → PR → 评审 → 检查 → 合并；保留需求与设计变更记录。
- 验证与确认：将需求关联到测试、实验及实际结果；未执行和 Mock 证据始终明确标注。

具体流程见 [协作约定](CONTRIBUTING.md)、[测试计划](docs/verification.md) 和 [需求追踪](docs/traceability.json)。课程评分细则尚未提供，这些是项目采用的方法，不代表教师已认可。

## 范围与资料

项目现名 **CrowdTurb**。原始材料使用旧名 ArchFlow AI；当前基线采用 V5 四周 PRD 和 V5 五人计划，V0.1 概念说明保留作背景。版本差异、原件和校验值见 [资料索引](docs/references/README.md)。

产品必须保留 PDE 评价路径；FNO 需留下训练、推理或失败尝试及误差实验记录，是否接入产品由实验决定。示意粒子是连续场的可视化，不是经过验证的真实个体轨迹。结果用于课程方案比较。

## GitHub

本地默认分支为 `main`。远程仓库名建议使用 `CrowdTurb`；当前尚未配置远程地址。源资料随仓库保存，开源许可证和远程可见性由团队决定。
