# 仓库初始化检查

日期：2026-11-20。执行环境：Windows，Python 3.12.14，jsonschema 4.25.1。对象为初始仓库草案和手工合成的 Mock，不是产品验收或算法实验。

## 实际执行与结果

- `python tools/check_repository.py`：通过。3 份 Schema 与关联 Mock 一致；检查时 46 个本地文档链接有效；11 条需求追踪均有测试计划；3 份 DOCX 原件 SHA-256 与归档一致。
- `python -m unittest discover -s tests/contracts -v`：11 项测试通过。覆盖有效样例、负数 / 非有限密度、转置网格、障碍内数据、无效出口 / 参数范围、场景版本、时间序列、真实结果记录要求和任务 / 结果错配。
- `git check-ignore`：本地虚拟环境、`.env`、数据目录和模型目录符合忽略规则。

命令实际使用本地 `.venv` 中的 Python。GitHub Actions 尚未执行；CI 配置计划在 Windows / Linux、Python 3.11 上运行，不能把本次本机通过当作远程运行通过。

## 范围与局限

检查只证明初始化资料与契约草案的内部一致性。尚无 Web 产品、PDE、FNO 或真实布局优化结果；REQ / NFR 全部保持 `planned`，没有产品需求因本记录转为已验收。团队尚未评审新增契约与技术提案。
