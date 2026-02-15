# copilot-instruction

> 此文件是 AGENTS.md 的复制。一切以 AGENTS.md 为主。

> 使用自动化与 AI 代理（GitHub Copilot、依赖/CI 机器人，以及仓库中的运行时调度器/worker）的指导原则，适用于 g0v0-server 仓库。

---

## API 参考

本项目必须保持与公开的 osu! API 兼容。在添加或映射端点时请参考：

- **v1（旧版）：** [https://github.com/ppy/osu-api/wiki](https://github.com/ppy/osu-api/wiki)
- **v2（OpenAPI）：** [https://osu.ppy.sh/docs/openapi.yaml](https://osu.ppy.sh/docs/openapi.yaml)

任何在 `app/router/v1/`、`app/router/v2/` 或 `app/router/notification/` 中的实现必须与官方规范保持一致。自定义或实验性的端点应放在 `app/router/private/` 中。

---

## 代理类别

允许的代理分为三类：

- **代码生成/补全代理**（如 GitHub Copilot 或其他 LLM）—— **仅当** 有维护者审核并批准输出时允许使用。
- **自动维护代理**（如 Dependabot、Renovate、pre-commit.ci）—— 允许使用，但必须遵守严格的 PR 和 CI 政策。
- **运行时/后台代理**（调度器、worker）—— 属于产品代码的一部分；必须遵守生命周期、并发和幂等性规范。

所有由代理生成或建议的更改必须遵守以下规则。

---

## 所有代理的规则

1. **单一职责的 PR。** 代理的 PR 必须只解决一个问题（一个功能、一个 bug 修复或一次依赖更新）。提交信息应使用 Angular 风格（如 `feat(api): add ...`）。
2. **通过 Lint 与 CI 检查。** 每个 PR（包括代理创建的）在合并前必须通过 `pyright`、`ruff`、`pre-commit` 钩子和仓库 CI。PR 中应附带 CI 运行结果链接。
3. **绝不可提交敏感信息。** 代理不得提交密钥、密码、token 或真实 `.env` 值。如果检测到可能的敏感信息，代理必须中止并通知指定的维护者。
4. **API 位置限制。** 不得在 `app/router/v1` 或 `app/router/v2` 下添加新的公开端点，除非该端点在官方 v1/v2 规范中存在。自定义或实验性端点必须放在 `app/router/private/`。
5. **保持公共契约稳定。** 未经批准的迁移计划，不得随意修改响应 schema、路由前缀或其他公共契约。若有变更，PR 中必须包含明确的兼容性说明。

---

## Copilot / LLM 使用

> 关于在本仓库中使用 GitHub Copilot 和其他基于 LLM 的辅助工具的统一指导。

### 关键项目结构（需要了解的内容）

- **应用入口：** `main.py` —— FastAPI 应用，包含启动/关闭生命周期管理（fetchers、GeoIP、调度器、缓存与健康检查、Redis 消息、统计、成就系统）。

- **路由：** `app/router/` 包含所有路由组。主要的路由包括：
  - `v1/`（v1 端点）
  - `v2/`（v2 端点）
  - `notification/` 路由（聊天/通知子系统）
  - `auth.py`（认证/token 流程）
  - `private/`（自定义或实验性的端点）

  **规则：** `v1/` 和 `v2/` 必须与官方 API 对应。仅内部或实验端点应放在 `app/router/private/`。

- **模型与数据库工具：**
  - SQLModel/ORM 模型在 `app/database/`。
  - 非数据库模型在 `app/models/`。
  - 修改模型/schema 时必须生成 Alembic 迁移，并手动检查生成的 SQL 与索引。

- **服务层：** `app/service/` 保存领域逻辑（如缓存工具、通知/邮件逻辑）。复杂逻辑应放在 service，而不是路由处理器中。

- **任务：** `app/tasks/` 保存任务（定时任务、启动任务、关闭任务）。
  - 均在 `__init__.py` 进行导出。
  - 对于启动任务/关闭任务，在 `main.py` 的 `lifespan` 调用。
  - 定时任务使用 APScheduler

- **缓存与依赖：** 使用 `app/dependencies/` 提供的 Redis 依赖和缓存服务（遵循现有 key 命名约定，如 `user:{id}:...`）。

- **日志：** 使用 `app/log` 提供的日志工具。

### 实用工作流（提示模式）

- **添加 v2 端点（正确方式）：** 在 `app/router/v2/` 下添加文件，导出路由，实现基于数据库与缓存依赖的异步处理函数。**不得**在 v1/v2 添加非官方端点。
- **添加自定义端点：** 放在 `app/router/private/`，保持处理器精简，将业务逻辑放入 `app/service/`。
- **鉴权：** 使用 [`app.dependencies.user`](../app/dependencies/user.py) 提供的依赖注入，如 `ClientUser` 和 `get_current_user`，参考下方。
  
  ```python
  from typing import Annotated
  from fastapi import Security
  from app.dependencies.user import ClientUser, get_current_user


  @router.get("/some-api")
  async def _(current_user: Annotated[User, Security(get_current_user, scopes=["public"])]):
      ...


  @router.get("/some-client-api")
  async def _(current_user: ClientUser):
      ...
  ```

- **添加后台任务：** 将任务逻辑写在 `app/service/_job.py`（幂等、可重试）。调度器入口放在 `app/scheduler/_scheduler.py`，并在应用生命周期注册。
- **数据库 schema 变更：** 修改 `app/models/` 中的 SQLModel 模型，运行 `alembic revision --autogenerate`，检查迁移并本地测试 `alembic upgrade head` 后再提交。
- **缓存写入与响应：** 使用现有的 `UserResp` 模式和 `UserCacheService`；异步缓存写入应使用后台任务。

### 提示指导（给 LLM/Copilot 的输入）

- 明确文件位置和限制（如：`Add an async endpoint under app/router/private/... DO NOT add to app/router/v1 or v2`）。
- 要求异步处理函数、依赖注入 DB/Redis、复用已有服务/工具、加上类型注解，并生成最小化 pytest 测试样例。

### 约定与质量要求

- **使用 Annotated-style 依赖注入** 在路由处理器中。
- **提交信息风格：** `type(scope): subject`（Angular 风格）。
- **优先异步：** 路由必须为异步函数；避免阻塞事件循环。
- **关注点分离：** 业务逻辑应放在 service，而不是路由中。
- **错误处理：** 客户端错误用 `HTTPException`，服务端错误使用结构化日志。
- **类型与 lint：** 在请求评审前，代码必须通过 `pyright` 和 `ruff` 检查。
- **注释：** 避免过多注释，仅为晦涩逻辑添加简洁的“魔法注释”。
- **日志：** 使用 `app.log` 提供的 `log` 函数获取 logger 实例。（服务、任务除外）

### 工具参考

```
uv sync
pre-commit install
pre-commit run --all-files
pyright
ruff .
alembic revision --autogenerate -m "feat(db): ..."
alembic upgrade head
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### PR 范围指导

- 保持 PR 专注：一次只做一件事（如端点或重构，不要混合）。
- 不确定时，请参考现有服务，并添加简短说明性注释。

### PR 审核规则

> GitHub Copilot PR review 可参考。

1. 如果 PR 修改了端点，简要说明端点的用途和预期行为。同时检查是否满足上述的 API 位置限制。
2. 如果 PR 修改了数据库模型，必须包含 Alembic 迁移。检查迁移的 SQL 语句和索引是否合理。
3. 修改的其他功能需要提供简短的说明。
4. 提供性能优化的建议（见下文）。

---

## 性能优化提示

以下为结合本仓库架构（FastAPI + SQLModel/SQLAlchemy、Redis 缓存、后台调度器）总结的性能优化建议：

### 数据库

- **仅选择必要字段。** 使用 `select(Model.col1, Model.col2)`，避免 `select(Model)`。

```py
stmt = select(User.id, User.username).where(User.active == True)
rows = await session.execute(stmt)
```

- **使用 `select(exists())` 检查存在性。** 避免加载整行：

```py
from sqlalchemy import select, exists
exists_stmt = select(exists().where(User.id == some_id))
found = await session.scalar(exists_stmt)
```

- **避免 N+1 查询。** 需要关联对象时用 `selectinload`、`joinedload`。

- **批量操作。** 插入/更新时应批量执行，并放在一个事务中，而不是多个小事务。


### 耗时任务

- 如果这个任务来自 API Router，请使用 FastAPI 提供的 [`BackgroundTasks`](https://fastapi.tiangolo.com/tutorial/background-tasks)
- 其他情况，使用 `app.helpers` 的 `bg_tasks`，它提供了与 FastAPI 的 `BackgroundTasks` 类似的功能。

---

## 部分 LLM 的额外要求

### Claude Code

- 禁止创建额外的测试脚本。

