# 系统 API 模块（E）

状态：W1 实现待评审。该服务负责统一项目入口、场景版本、任务状态机、结果保存和错误返回；不实现 PDE、FNO、3D、指标或候选优化。

## 启动

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-api.txt
.\.venv\Scripts\python.exe -m apps.api.crowdturb --port 8000
```

默认写入 `data/crowdturb.sqlite3`，首次启动创建 Mock 示例项目。`--no-demo` 可关闭自动创建，`--db` 可指定数据库路径。

## HTTP 接口

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/health` | 服务与适配器状态 |
| GET / POST | `/api/projects` | 列出 / 创建项目 |
| GET | `/api/projects/{id}` | 项目、最新场景、任务和结果索引 |
| GET | `/api/projects/{id}/scenes/latest` | 最新场景 |
| GET | `/api/projects/{id}/scenes/{revision}` | 指定场景版本 |
| POST | `/api/projects/{id}/scenes` | 保存新版本，由服务端递增 revision |
| GET / POST | `/api/projects/{id}/tasks` | 列出 / 创建任务 |
| GET | `/api/tasks/{id}` | 任务状态快照 |
| GET | `/api/projects/{id}/results` | 项目结果索引 |
| GET | `/api/results/{id}` | 连续场结果 |

错误统一为 `{"error":{"code":"...","message":"..."}}`。创建任务返回 `202`；状态按 `queued → running → succeeded / failed` 更新。`succeeded` 必须有结果，`failed` 必须有可读错误。

## 模块适配器

`apps/api/crowdturb/adapters.py` 是 C 的接入口。适配器只需要实现：

```python
def solve(scene: dict, task_id: str) -> dict:
    ...
```

返回值必须通过 `field-result.schema.json` 和跨字段语义检查。PDE / FNO 当前是显式失败占位；C 接入独立命令后替换对应适配器，不要让服务端复制算法实现或把 Mock 改标签。D 的候选优化通过任务接口提交，不绕开运行记录。

## 存储与恢复

- SQLite 保存项目、场景版本、任务快照和结果；场景/结果同时保存完整 JSON。
- 场景 revision 由服务端递增，`scene_id` 不允许在版本间改变。
- 服务重启时，遗留的 `queued / running` 任务标记为 `failed`，错误码 `task_interrupted`，不会伪装成功。
- 当前结果以 JSON 保存，适合 W1 小样例；真实大数组格式待 C/E 在契约评审后决定。

## 当前限制

- 标准库 HTTP 服务是临时 W1 边界，未实现认证、权限、分布式队列或并发配额。
- Mock 适配器固定读取 `examples/field.mock.json`；场景网格变化时任务会明确失败，不会自动编造匹配结果。
- 尚无正式部署、日志聚合、数据迁移或大结果文件存储。

## 检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/contracts -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/system -v
```

系统测试覆盖项目与场景往返、Mock 成功、PDE 未接入失败、无效输入、重启持久化和中断任务恢复；这些测试不证明任何 PDE / FNO 算法正确。
